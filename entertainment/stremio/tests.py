import base64
import json
from datetime import date, timedelta
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase, override_settings

from custom_auth.models import CustomUser, Genre, Review, Watchlist
from movies.models import Movie
from tvshows.models import Episode, Season, TVShow, WatchedEpisode

from .authentication import auth_cache_key, decode_config
from .views import MANIFEST_VERSION, parse_extra

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def encode_config(data) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')


class ParseExtraTests(TestCase):
    def test_genre_with_ampersand_is_kept_whole(self):
        self.assertEqual(
            parse_extra('genre=Sci-Fi & Fantasy&skip=100'),
            {'skip': 100, 'genre': 'Sci-Fi & Fantasy', 'search': None},
        )

    def test_bad_skip_values_fall_back_to_zero(self):
        self.assertEqual(parse_extra('skip=-5')['skip'], 0)
        self.assertEqual(parse_extra('skip=abc')['skip'], 0)
        self.assertEqual(parse_extra('skip=99999999')['skip'], 10_000)

    def test_search(self):
        self.assertEqual(parse_extra('search=the matrix')['search'], 'the matrix')
        self.assertEqual(parse_extra(None), {'skip': 0, 'genre': None, 'search': None})


class DecodeConfigTests(TestCase):
    def test_non_object_configs_decode_to_empty_dict(self):
        self.assertEqual(decode_config('!!!not-base64'), {})
        self.assertEqual(decode_config(encode_config([1, 2])), {})
        self.assertEqual(decode_config(encode_config('key')), {})

    def test_object_config(self):
        self.assertEqual(decode_config(encode_config({'api_key': 'abc'})), {'api_key': 'abc'})


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class StremioEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(username='viewer', email='viewer@example.com', password='x')
        self.user.generate_api_key()
        self.config = encode_config({'api_key': self.user.api_key})
        async_patch = mock.patch('django_q.tasks.async_task')
        async_patch.start()
        self.addCleanup(async_patch.stop)

    def make_movie(self, tmdb_id, imdb_id, title='Movie', **kwargs):
        return Movie.objects.create(
            title=title, original_title=title, tmdb_id=tmdb_id, imdb_id=imdb_id,
            runtime=100, rating=7.0, description=f"{title} overview", **kwargs,
        )

    def url(self, path, config=None):
        return f"/stremio/{config or self.config}/{path}"

    # --- manifest -------------------------------------------------------------------------------

    def test_manifest_lists_search_catalogs_and_version(self):
        data = self.client.get(self.url('manifest.json')).json()
        self.assertEqual(data['version'], MANIFEST_VERSION)
        self.assertFalse(data['behaviorHints']['configurationRequired'])
        ids = {(c['type'], c['id']) for c in data['catalogs']}
        self.assertIn(('movie', 'search-movies'), ids)
        self.assertIn(('series', 'search-series'), ids)

    def test_manifest_respects_catalog_subset(self):
        config = encode_config({'api_key': self.user.api_key, 'catalogs': ['watchlist-movies', 'top-rated-series']})
        data = self.client.get(self.url('manifest.json', config)).json()
        self.assertEqual(
            [(c['type'], c['id']) for c in data['catalogs']],
            [('movie', 'watchlist-movies'), ('series', 'top-rated')],
        )

    def test_manifest_without_valid_key_requires_configuration(self):
        data = self.client.get('/stremio/manifest.json').json()
        self.assertTrue(data['behaviorHints']['configurationRequired'])

    def test_bare_configure_route(self):
        self.assertEqual(self.client.get('/stremio/configure').status_code, 200)

    def test_install_page_shows_https_manifest_url(self):
        self.client.force_login(self.user)
        response = self.client.get('/settings/stremio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="http://testserver/stremio/{self.config}/manifest.json"')
        self.assertContains(response, 'class="catalog-toggle" value="search-movies"')

    # --- catalogs -------------------------------------------------------------------------------

    def test_bad_key_gets_401_with_cors(self):
        response = self.client.get(self.url('catalog/movie/watchlist-movies.json', encode_config({'api_key': 'nope'})))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')

    def test_catalog_has_cors_and_cache_headers(self):
        response = self.client.get(self.url('catalog/movie/watchlist-movies.json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')
        self.assertIn('max-age=60', response['Cache-Control'])

    def test_genre_filter_with_ampersand(self):
        genre = Genre.objects.create(name='Sci-Fi & Fantasy')
        show = TVShow.objects.create(title='Space Show', original_title='Space Show', tmdb_id=1, imdb_id='tt0000001')
        show.genres.add(genre)
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(TVShow), object_id=show.id)

        response = self.client.get(self.url('catalog/series/watchlist-series/genre=Sci-Fi%20%26%20Fantasy.json'))
        metas = response.json()['metas']
        self.assertEqual([m['id'] for m in metas], ['tt0000001'])
        self.assertEqual(metas[0]['genres'], ['Sci-Fi & Fantasy'])

    def test_failing_catalog_returns_empty_list(self):
        with mock.patch('stremio.views.get_watchlist_movies', side_effect=RuntimeError('boom')):
            response = self.client.get(self.url('catalog/movie/watchlist-movies.json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'metas': []})

    def test_search_ranks_exact_then_prefix(self):
        self.make_movie(1, 'tt0000011', title='The Matrix Reloaded', )
        self.make_movie(2, 'tt0000012', title='The Matrix')
        self.make_movie(3, 'tt0000013', title='Unrelated')
        metas = self.client.get(self.url('catalog/movie/search-movies/search=the matrix.json')).json()['metas']
        self.assertEqual([m['id'] for m in metas], ['tt0000012', 'tt0000011'])

    def test_top_rated_tie_break_is_stable(self):
        other = CustomUser.objects.create_user(username='other', email='other@example.com', password='x')
        movie_ct = ContentType.objects.get_for_model(Movie)
        first = self.make_movie(1, 'tt0000021')
        second = self.make_movie(2, 'tt0000022')
        for movie in (second, first):
            Review.objects.create(user=other, content_type=movie_ct, object_id=movie.id, rating=8)
        metas = self.client.get(self.url('catalog/movie/top-rated.json')).json()['metas']
        self.assertEqual([m['id'] for m in metas], ['tt0000021', 'tt0000022'])

    # --- meta -----------------------------------------------------------------------------------

    def test_duplicate_imdb_ids_do_not_500(self):
        self.make_movie(1, 'tt0000031', title='First')
        self.make_movie(2, 'tt0000031', title='Duplicate')
        response = self.client.get(self.url('meta/movie/tt0000031.json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['meta']['name'], 'First')

    def test_unknown_title_is_404_with_cors(self):
        response = self.client.get(self.url('meta/movie/tt9999999.json'))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')

    def test_discover_title_uses_cached_external_meta(self):
        cache.set('stremio_ext_meta_tt0000041', {'id': 'tt0000041', 'type': 'movie', 'name': 'Elsewhere'})
        response = self.client.get(self.url('meta/movie/tt0000041.json'))
        self.assertEqual(response.json()['meta']['name'], 'Elsewhere')

    def test_movie_description_has_rating_but_not_review_text(self):
        movie = self.make_movie(1, 'tt0000051', trailer='https://www.youtube.com/embed/dQw4w9WgXcQ')
        Review.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(Movie), object_id=movie.id,
            rating=9, review_text='secret thoughts',
        )
        meta = self.client.get(self.url('meta/movie/tt0000051.json')).json()['meta']
        self.assertIn('Your Rating: 9', meta['description'])
        self.assertIn('Community: 9.0/10 (1 review)', meta['description'])
        self.assertNotIn('secret thoughts', meta['description'])
        self.assertEqual(meta['trailerStreams'], [{'title': 'Trailer', 'ytId': 'dQw4w9WgXcQ'}])
        self.assertIn('Open in Entertainment List', [link['name'] for link in meta['links']])

    def test_series_meta_has_videos_with_watched_marks(self):
        show = TVShow.objects.create(
            title='Show', original_title='Show', tmdb_id=5, imdb_id='tt0000061',
            first_air_date=date(2020, 1, 1), status='Returning Series',
        )
        season = Season.objects.create(show=show, season_number=1)
        yesterday = date.today() - timedelta(days=1)
        watched = Episode.objects.create(season=season, episode_number=1, title='Pilot', air_date=yesterday)
        Episode.objects.create(season=season, episode_number=2, title='Second', air_date=yesterday)
        WatchedEpisode.objects.create(user=self.user, episode=watched)

        meta = self.client.get(self.url('meta/series/tt0000061.json')).json()['meta']
        self.assertEqual(meta['releaseInfo'], '2020–')
        self.assertEqual(
            [(v['id'], v['title']) for v in meta['videos']],
            [('tt0000061:1:1', '✓ Pilot'), ('tt0000061:1:2', 'Second')],
        )

    # --- auth cache -----------------------------------------------------------------------------

    def test_regenerating_key_revokes_old_key_immediately(self):
        self.client.get(self.url('catalog/movie/watchlist-movies.json'))  # populates the auth cache
        old_key = self.user.api_key
        self.assertTrue(cache.get(auth_cache_key(old_key)))

        self.user.generate_api_key()
        self.assertIsNone(cache.get(auth_cache_key(old_key)))
        response = self.client.get(self.url('catalog/movie/watchlist-movies.json'))
        self.assertEqual(response.status_code, 401)
