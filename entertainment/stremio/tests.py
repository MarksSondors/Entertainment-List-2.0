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
        # Never call Cinemeta from tests; individual tests override the response
        self.cinemeta = mock.patch('stremio.views.requests.get').start()
        self.cinemeta.return_value.ok = False
        self.addCleanup(mock.patch.stopall)

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

    def test_discover_title_gets_imdb_link(self):
        self.cinemeta.return_value.ok = True
        self.cinemeta.return_value.json.return_value = {'meta': {'imdbRating': '6.9'}}
        cache.set('stremio_ext_meta_tt0000042', {'id': 'tt0000042', 'type': 'movie', 'name': 'Far', 'links': []})
        meta = self.client.get(self.url('meta/movie/tt0000042.json')).json()['meta']
        self.assertEqual(meta['links'][0], {'name': '6.9', 'category': 'imdb', 'url': 'https://imdb.com/title/tt0000042'})

    def test_movie_description_has_rating_but_not_review_text(self):
        movie = self.make_movie(1, 'tt0000051', trailer='https://www.youtube.com/embed/dQw4w9WgXcQ')
        Review.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(Movie), object_id=movie.id,
            rating=9, review_text='secret thoughts',
        )
        meta = self.client.get(self.url('meta/movie/tt0000051.json')).json()['meta']
        # Ratings are chips now; the description is the plot alone
        self.assertEqual(meta['description'], 'Movie overview')
        site_group = [link['name'] for link in meta['links'] if link['category'] == 'Entertainment List']
        self.assertEqual(site_group, ['You ★ 9', 'Community ★ 9 (1)', 'Open in Entertainment List'])
        self.assertNotIn('secret thoughts', json.dumps(meta))
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

    def test_waiting_for_new_episodes_orders_by_next_air_date(self):
        tvshow_ct = ContentType.objects.get_for_model(TVShow)
        yesterday = date.today() - timedelta(days=1)

        def caught_up_show(n, next_in_days):
            show = TVShow.objects.create(
                title=f'Show {n}', original_title=f'Show {n}', tmdb_id=900 + n, imdb_id=f'tt09000{n:02d}',
                first_air_date=date(2020, 1, 1), status='Returning Series',
            )
            season = Season.objects.create(show=show, season_number=1)
            aired = Episode.objects.create(season=season, episode_number=1, title='E1', air_date=yesterday)
            WatchedEpisode.objects.create(user=self.user, episode=aired)
            if next_in_days is not None:
                Episode.objects.create(season=season, episode_number=2, title='E2',
                                       air_date=date.today() + timedelta(days=next_in_days))
                # a later episode must not override the earliest one
                Episode.objects.create(season=season, episode_number=3, title='E3',
                                       air_date=date.today() + timedelta(days=next_in_days + 1))
            Watchlist.objects.create(user=self.user, content_type=tvshow_ct, object_id=show.id)
            return show

        # watchlist add order: 1 (oldest) .. 7 (newest)
        caught_up_show(1, 30)
        caught_up_show(2, None)
        caught_up_show(3, 3)
        caught_up_show(4, None)
        caught_up_show(5, 10)
        old = caught_up_show(6, None)
        recent = caught_up_show(7, None)
        TVShow.objects.filter(id=recent.id).update(last_air_date=date.today() - timedelta(days=20))
        TVShow.objects.filter(id=old.id).update(last_air_date=date(2024, 4, 23))

        metas = self.client.get(self.url('catalog/series/waiting-for-new-episodes.json')).json()['metas']
        # soonest release first; then no announced date, longest since the last episode first;
        # no last_air_date at all goes last, newest watchlist add first
        self.assertEqual([m['id'] for m in metas],
                         ['tt0900003', 'tt0900005', 'tt0900001', 'tt0900006', 'tt0900007',
                          'tt0900004', 'tt0900002'])

    def test_not_started_puts_unaired_shows_last_with_premiere_banner(self):
        from .poster import poster_state
        tvshow_ct = ContentType.objects.get_for_model(TVShow)
        yesterday = date.today() - timedelta(days=1)

        def show(n, first_air, aired_episode):
            s = TVShow.objects.create(
                title=f'Show {n}', original_title=f'Show {n}', tmdb_id=950 + n, imdb_id=f'tt09500{n:02d}',
                first_air_date=first_air, status='Returning Series',
            )
            season = Season.objects.create(show=s, season_number=1)
            Episode.objects.create(season=season, episode_number=1, title='E1',
                                   air_date=yesterday if aired_episode else first_air)
            Watchlist.objects.create(user=self.user, content_type=tvshow_ct, object_id=s.id)
            return s

        # watchlist add order: 1 (oldest) .. 4 (newest)
        show(1, date(2020, 1, 1), True)
        later = show(2, date.today() + timedelta(days=40), False)
        soon = show(3, date.today() + timedelta(days=16), False)
        show(4, date(2021, 1, 1), True)

        metas = self.client.get(self.url('catalog/series/watchlist-series.json')).json()['metas']
        # startable shows keep newest-add-first; unaired follow, soonest premiere first
        self.assertEqual([m['id'] for m in metas], ['tt0950004', 'tt0950001', 'tt0950003', 'tt0950002'])

        expected = f"PREMIERES {soon.first_air_date.strftime('%b %d, %Y').upper()}"
        self.assertEqual(poster_state(soon, 'series', self.user, None)['banner'],
                         {'icon': 'calendar', 'text': expected})
        from .poster import bulk_poster_states
        self.assertEqual(bulk_poster_states([soon, later], 'series', self.user, None)[soon.id]['banner'],
                         {'icon': 'calendar', 'text': expected})
        # continue-watching posters never get a premiere banner
        self.assertIsNone(poster_state(soon, 'series', self.user, 'cw')['banner'])

    # --- #31 addon fixes -----------------------------------------------------------------------

    def _cinemeta_rating(self, value):
        self.cinemeta.return_value.ok = True
        self.cinemeta.return_value.json.return_value = {'meta': {'imdbRating': value}}

    def test_movie_meta_has_imdb_rating_and_default_video(self):
        self._cinemeta_rating('8.5')
        self.make_movie(1, 'tt6751668', title='Parasite')
        meta = self.client.get(self.url('meta/movie/tt6751668.json')).json()['meta']
        self.assertEqual(meta['imdbRating'], '8.5')
        # Stremio ignores imdbRating when links exist, so the badge has to be an 'imdb' link
        self.assertIn({'name': '8.5', 'category': 'imdb', 'url': 'https://imdb.com/title/tt6751668'}, meta['links'])
        self.assertEqual(meta['behaviorHints'], {'defaultVideoId': 'tt6751668'})
        # cached: a second request doesn't hit Cinemeta again
        self.client.get(self.url('meta/movie/tt6751668.json'))
        self.assertEqual(self.cinemeta.call_count, 1)

    def test_imdb_rating_reaches_catalog_only_from_cache(self):
        movie = self.make_movie(1, 'tt0000071')
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie), object_id=movie.id)
        item = self.client.get(self.url('catalog/movie/watchlist-movies.json')).json()['metas'][0]
        self.assertNotIn('imdbRating', item)
        self.cinemeta.assert_not_called()  # catalogs never wait on Cinemeta

        self._cinemeta_rating('7.1')
        self.client.get(self.url('meta/movie/tt0000071.json'))
        from .catalog_cache import bump_catalog_generation
        bump_catalog_generation(self.user.id)  # skip the 120 s outer page cache
        item = self.client.get(self.url('catalog/movie/watchlist-movies.json')).json()['metas'][0]
        self.assertEqual(item['imdbRating'], '7.1')

    def test_meta_and_catalog_carry_released_and_year(self):
        movie = self.make_movie(1, 'tt0000075', release_date=date(2019, 5, 30))
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie), object_id=movie.id)
        meta = self.client.get(self.url('meta/movie/tt0000075.json')).json()['meta']
        self.assertEqual((meta['releaseInfo'], meta['year'], meta['released']),
                         ('2019', '2019', '2019-05-30T00:00:00.000Z'))
        item = self.client.get(self.url('catalog/movie/watchlist-movies.json')).json()['metas'][0]
        self.assertEqual(item['released'], '2019-05-30T00:00:00.000Z')
        show = TVShow.objects.create(title='S', original_title='S', tmdb_id=8, imdb_id='tt0000076',
                                     first_air_date=date(2021, 3, 1), status='Returning Series')
        meta = self.client.get(self.url('meta/series/tt0000076.json')).json()['meta']
        self.assertEqual((meta['year'], meta['released']), ('2021–', '2021-03-01T00:00:00.000Z'))

    def test_no_imdb_link_without_rating(self):
        self.make_movie(1, 'tt0000077')
        meta = self.client.get(self.url('meta/movie/tt0000077.json')).json()['meta']
        self.assertNotIn('imdb', [link['category'] for link in meta['links']])

    def test_bad_or_missing_cinemeta_rating_is_dropped(self):
        self._cinemeta_rating('<b>9</b>')
        self.make_movie(1, 'tt0000072')
        meta = self.client.get(self.url('meta/movie/tt0000072.json')).json()['meta']
        self.assertNotIn('imdbRating', meta)
        self.cinemeta.side_effect = __import__('requests').ConnectionError
        self.make_movie(2, 'tt0000073')
        self.assertEqual(self.client.get(self.url('meta/movie/tt0000073.json')).status_code, 200)

    def test_stored_imdb_rating_is_served_without_cinemeta(self):
        from decimal import Decimal
        self._cinemeta_rating('5.0')  # what would show if Cinemeta were asked
        movie = self.make_movie(1, 'tt0000091', imdb_rating=Decimal('8.4'), imdb_votes=2134567)
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie), object_id=movie.id)
        meta = self.client.get(self.url('meta/movie/tt0000091.json')).json()['meta']
        self.assertEqual(meta['imdbRating'], '8.4')
        self.assertIn({'name': '8.4', 'category': 'imdb', 'url': 'https://imdb.com/title/tt0000091'}, meta['links'])
        item = self.client.get(self.url('catalog/movie/watchlist-movies.json')).json()['metas'][0]
        self.assertEqual(item['imdbRating'], '8.4')
        TVShow.objects.create(title='S', original_title='S', tmdb_id=9, imdb_id='tt0000092', imdb_rating=Decimal('9.0'))
        meta = self.client.get(self.url('meta/series/tt0000092.json')).json()['meta']
        self.assertEqual(meta['imdbRating'], '9.0')
        cinemeta_calls = [c for c in self.cinemeta.call_args_list if 'cinemeta' in str(c.args[:1])]
        self.assertEqual(cinemeta_calls, [])

    def _series_with_episode(self):
        show = TVShow.objects.create(title='Show', original_title='Show', tmdb_id=7, imdb_id='tt0000081')
        season = Season.objects.create(show=show, season_number=1)
        Episode.objects.create(
            season=season, episode_number=1, title='Pilot', overview='Who did it',
            still='https://image.tmdb.org/t/p/original/still.jpg',
        )
        return show

    def test_episode_thumbnails_are_w500(self):
        self._series_with_episode()
        video = self.client.get(self.url('meta/series/tt0000081.json')).json()['meta']['videos'][0]
        self.assertEqual(video['thumbnail'], 'https://image.tmdb.org/t/p/w500/still.jpg')
        self.assertEqual(video['overview'], 'Who did it')

    def test_hidden_plot_also_hides_episode_and_catalog_overviews(self):
        from custom_auth.models import UserSettings
        UserSettings.objects.update_or_create(user=self.user, defaults={'show_plot': False})
        self.user.refresh_from_db()
        show = self._series_with_episode()
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(TVShow), object_id=show.id)
        show.description = 'Series plot'
        show.save()
        meta = self.client.get(self.url('meta/series/tt0000081.json')).json()['meta']
        self.assertEqual(meta['videos'][0]['overview'], '')
        self.assertNotIn('Series plot', meta['description'])
        item = self.client.get(self.url('catalog/series/watchlist-series.json')).json()['metas'][0]
        self.assertNotIn('description', item)

    def test_genre_links_follow_enabled_catalogs(self):
        genre = Genre.objects.create(name='Drama')
        self.make_movie(1, 'tt0000091').genres.add(genre)

        def genre_urls(config):
            meta = self.client.get(self.url('meta/movie/tt0000091.json', config)).json()['meta']
            return [link['url'] for link in meta['links'] if link['category'] == 'Genres']

        self.assertTrue(genre_urls(self.config)[0].endswith('/movie/top-rated?genre=Drama'))
        only_watchlist = encode_config({'api_key': self.user.api_key, 'catalogs': ['watchlist-movies']})
        self.assertTrue(genre_urls(only_watchlist)[0].endswith('/movie/watchlist-movies?genre=Drama'))
        no_filter_catalog = encode_config({'api_key': self.user.api_key, 'catalogs': ['recommendations']})
        self.assertEqual(genre_urls(no_filter_catalog), [])

    def test_watchlist_change_shows_up_in_catalog_immediately(self):
        movie_ct = ContentType.objects.get_for_model(Movie)
        first = self.make_movie(1, 'tt0000101')
        second = self.make_movie(2, 'tt0000102')
        with self.captureOnCommitCallbacks(execute=True):
            Watchlist.objects.create(user=self.user, content_type=movie_ct, object_id=first.id)
        ids = lambda: [m['id'] for m in self.client.get(self.url('catalog/movie/watchlist-movies.json')).json()['metas']]
        self.assertEqual(ids(), ['tt0000101'])
        with self.captureOnCommitCallbacks(execute=True):
            Watchlist.objects.create(user=self.user, content_type=movie_ct, object_id=second.id)
        self.assertCountEqual(ids(), ['tt0000101', 'tt0000102'])  # not the 120 s cached page
        with self.captureOnCommitCallbacks(execute=True):
            Review.objects.create(user=self.user, content_type=movie_ct, object_id=second.id, rating=8)
            Watchlist.objects.filter(user=self.user, object_id=second.id).delete()
        self.assertEqual(ids(), ['tt0000101'])

    def test_catalog_invalidation_is_per_user(self):
        from .catalog_cache import catalog_generation
        other = CustomUser.objects.create_user(username='other2', email='other2@example.com', password='x')
        before = catalog_generation(other.id)
        with self.captureOnCommitCallbacks(execute=True):
            Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie),
                                     object_id=self.make_movie(1, 'tt0000111').id)
        self.assertEqual(catalog_generation(other.id), before)

    # --- auth cache -----------------------------------------------------------------------------

    def test_series_ratings_are_chips_in_order_after_genres(self):
        show = TVShow.objects.create(title='Show', original_title='Show', tmdb_id=9, imdb_id='tt0000121',
                                     description='Plot here')
        show.genres.add(Genre.objects.create(name='Horror'))
        ct = ContentType.objects.get_for_model(TVShow)
        for n, rating in ((1, 7), (2, 9), (10, 8)):
            season = Season.objects.create(show=show, season_number=n)
            episode = Episode.objects.create(season=season, episode_number=1, title='E1',
                                             air_date=date.today() - timedelta(days=30))
            WatchedEpisode.objects.create(user=self.user, episode=episode)  # season reviews need it watched
            Review.objects.create(user=self.user, content_type=ct, object_id=show.id, season=season, rating=rating)
        meta = self.client.get(self.url('meta/series/tt0000121.json')).json()['meta']
        self.assertEqual(meta['description'], 'Plot here')
        self.assertEqual([l['name'] for l in meta['links'] if l['category'] == 'Season ratings'], ['S1 7', 'S2 9', 'S10 8'])
        site = [l['name'] for l in meta['links'] if l['category'] == 'Entertainment List']
        self.assertEqual(site, ['You ★ 8', 'Community ★ 8 (3)', 'Open in Entertainment List'])
        order = []
        for link in meta['links']:
            if link['category'] not in order:
                order.append(link['category'])
        self.assertEqual(order[:3], ['Genres', 'Entertainment List', 'Season ratings'])
        self.assertTrue(all(l['url'].startswith('http') for l in meta['links'] if l['category'] == 'Season ratings'))

    def test_hidden_plot_leaves_description_empty_but_keeps_rating_chips(self):
        from custom_auth.models import UserSettings
        UserSettings.objects.update_or_create(user=self.user, defaults={'show_plot': False})
        movie = self.make_movie(1, 'tt0000122')
        Review.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie),
                              object_id=movie.id, rating=6)
        meta = self.client.get(self.url('meta/movie/tt0000122.json')).json()['meta']
        self.assertEqual(meta['description'], '')
        self.assertIn('You ★ 6', [l['name'] for l in meta['links']])

    # --- watch sync probe (#33 step 0) ------------------------------------------------------------

    def test_manifest_declares_player_and_library(self):
        data = self.client.get(self.url('manifest.json')).json()
        self.assertEqual(data['resources'], ['catalog', 'meta', 'player', 'library'])

    def test_player_event_is_recorded_and_answers_empty(self):
        from .models import AddonEvent
        response = self.client.get(
            self.url('player/series/tt0903747:2:5/action=stop&currentTime=2712000&duration=2820000.json'),
            HTTP_USER_AGENT='StremioTest/1.0',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'metas': []})
        self.assertEqual(response['Access-Control-Allow-Origin'], '*')
        self.assertEqual(response['Cache-Control'], 'no-store')
        event = AddonEvent.objects.get()
        self.assertEqual((event.user, event.kind, event.media_type, event.item_id, event.action),
                         (self.user, 'player', 'series', 'tt0903747:2:5', 'stop'))
        self.assertEqual((event.current_time, event.duration), (2712000, 2820000))
        self.assertEqual(event.user_agent, 'StremioTest/1.0')

    def test_library_batch_event_keeps_video_ids(self):
        from .models import AddonEvent
        response = self.client.get(self.url('library/series/tt0903747/action=watched&videoId=tt0903747:1:1,tt0903747:1:2.json'))
        self.assertEqual(response.status_code, 200)
        event = AddonEvent.objects.get()
        self.assertEqual((event.kind, event.action), ('library', 'watched'))
        self.assertEqual(event.video_ids, ['tt0903747:1:1', 'tt0903747:1:2'])
        self.client.get(self.url('library/movie/tt6751668/action=libraryAdd.json'))
        self.assertEqual(AddonEvent.objects.filter(action='libraryAdd').count(), 1)

    def test_unknown_action_is_flagged_and_bad_key_rejected(self):
        from .models import AddonEvent
        self.client.get(self.url('player/movie/tt6751668/action=rewind&currentTime=abc.json'))
        event = AddonEvent.objects.get()
        self.assertEqual((event.action, event.current_time), ('?rewind', None))
        bad = encode_config({'api_key': 'nope'})
        self.assertEqual(self.client.get(self.url('player/movie/tt1/action=start.json', bad)).status_code, 401)
        self.assertEqual(AddonEvent.objects.count(), 1)

    def test_event_storage_failure_still_answers_ok(self):
        with mock.patch('stremio.models.AddonEvent.objects.create', side_effect=RuntimeError('db down')):
            response = self.client.get(self.url('player/movie/tt6751668/action=start&currentTime=0&duration=1.json'))
        self.assertEqual(response.status_code, 200)

    def test_regenerating_key_revokes_old_key_immediately(self):
        self.client.get(self.url('catalog/movie/watchlist-movies.json'))  # populates the auth cache
        old_key = self.user.api_key
        self.assertTrue(cache.get(auth_cache_key(old_key)))

        self.user.generate_api_key()
        self.assertIsNone(cache.get(auth_cache_key(old_key)))
        response = self.client.get(self.url('catalog/movie/watchlist-movies.json'))
        self.assertEqual(response.status_code, 401)


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class PosterContentVersionTests(TestCase):
    """Posters are keyed by what's drawn on them: re-render on change, never on a timer."""

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(username='poster', email='poster@example.test', password='x')
        self.user.generate_api_key()
        self.other = CustomUser.objects.create_user(username='rater', email='rater@example.test', password='x')
        self.config = encode_config({'api_key': self.user.api_key})
        self.movie = Movie.objects.create(
            title='Nathan For You', original_title='Nathan For You', tmdb_id=9101, imdb_id='tt2297757',
            runtime=100, rating=7.0, description='x', poster='/abc.jpg', release_date=date(2013, 2, 28))
        self.show = TVShow.objects.create(
            title='The Rehearsal', original_title='The Rehearsal', tmdb_id=9102, imdb_id='tt10802170',
            description='x', poster='/reh.jpg')
        season = Season.objects.create(show=self.show, season_number=1, title='Season 1')
        self.eps = [Episode.objects.create(season=season, episode_number=n, title=f'E{n}',
                                           air_date=date(2022, 7, n)) for n in (1, 2)]
        enqueue = mock.patch('django_q.tasks.async_task')
        self.enqueue = enqueue.start()
        self.addCleanup(enqueue.stop)

    def version(self, media, media_type, ctx=None):
        from .poster import content_version
        return content_version(media, media_type, self.user, ctx)

    def review(self, rating):
        return Review.objects.create(
            user=self.other, rating=rating, review_text='ok',
            content_type=ContentType.objects.get_for_model(Movie), object_id=self.movie.id)

    def test_version_stable_until_content_changes(self):
        first = self.version(self.movie, 'movie')
        self.assertEqual(first, self.version(self.movie, 'movie'))
        self.review(8)
        self.assertNotEqual(first, self.version(self.movie, 'movie'))

    def test_progress_changes_cw_version_only_for_that_user(self):
        before = self.version(self.show, 'series', 'cw')
        WatchedEpisode.objects.create(user=self.user, episode=self.eps[0])
        self.assertNotEqual(before, self.version(self.show, 'series', 'cw'))
        from .poster import content_version
        self.assertEqual(content_version(self.show, 'series', self.other, 'cw'),
                         content_version(self.show, 'series', self.other, 'cw'))

    def test_rendered_poster_reused_until_rating_changes(self):
        from . import poster
        with mock.patch.object(poster, 'render_catalog_poster', return_value=b'img') as render:
            poster.get_cached_poster(self.movie, 'movie', self.user, None)
            poster.get_cached_poster(self.movie, 'movie', self.user, None)
            self.assertEqual(render.call_count, 1)
            self.review(9)
            poster.get_cached_poster(self.movie, 'movie', self.user, None)
            self.assertEqual(render.call_count, 2)

    def test_catalog_url_carries_content_version(self):
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie),
                                 object_id=self.movie.id)
        metas = self.client.get(f'/stremio/{self.config}/catalog/movie/watchlist-movies.json').json()['metas']
        self.assertTrue(metas[0]['poster'].endswith(f"v={self.version(self.movie, 'movie')}"))

    def test_poster_response_immutable_only_for_current_version(self):
        from . import poster
        url = f'/stremio/{self.config}/poster/movie/{self.movie.imdb_id}.png'
        with mock.patch.object(poster, 'render_catalog_poster', return_value=b'img'):
            current = self.client.get(url, {'v': self.version(self.movie, 'movie')})
            stale = self.client.get(url, {'v': 'deadbeef00'})
        self.assertIn('immutable', current['Cache-Control'])
        self.assertNotIn('immutable', stale['Cache-Control'])

    def test_review_queues_render_for_watchlist_addon_users(self):
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie),
                                 object_id=self.movie.id)
        with self.captureOnCommitCallbacks(execute=True):
            self.review(7)
        warm = [c.args for c in self.enqueue.call_args_list if c.args[0] == 'stremio.tasks.warm_poster']
        self.assertEqual(warm, [('stremio.tasks.warm_poster', 'movie', self.movie.imdb_id, self.user.id, None)])

    def test_watching_episode_queues_render_for_that_user(self):
        with self.captureOnCommitCallbacks(execute=True):
            WatchedEpisode.objects.create(user=self.user, episode=self.eps[0])
        warm = {c.args[1:] for c in self.enqueue.call_args_list if c.args[0] == 'stremio.tasks.warm_poster'}
        self.assertEqual(warm, {('series', self.show.imdb_id, self.user.id, 'cw'),
                                ('series', self.show.imdb_id, self.user.id, None)})

    def test_no_timer_schedule_for_posters(self):
        from django.apps import apps
        from django_q.models import Schedule

        from .apps import _setup_stremio_schedules
        Schedule.objects.create(func='stremio.tasks.warm_all_posters', schedule_type=Schedule.MINUTES, minutes=20)
        _setup_stremio_schedules(apps.get_app_config('stremio'))
        self.assertFalse(Schedule.objects.filter(func='stremio.tasks.warm_all_posters').exists())

    def test_bulk_poster_states_matches_single_and_avoids_n_plus_1(self):
        from .poster import bulk_poster_states, poster_state

        extra_movies = [
            Movie.objects.create(
                title=f'Film {i}', original_title=f'Film {i}', tmdb_id=9200 + i, imdb_id=f'tt920000{i}',
                runtime=100, rating=7.0, description='x', poster=f'/p{i}.jpg', release_date=date(2020, 1, 1),
            )
            for i in range(15)
        ]
        all_movies = [self.movie, *extra_movies]
        self.review(8)
        for m in all_movies:
            Watchlist.objects.create(
                user=self.user, content_type=ContentType.objects.get_for_model(Movie), object_id=m.id
            )

        bulk = bulk_poster_states(all_movies, 'movie', self.user, None)
        for m in all_movies:
            self.assertEqual(bulk[m.id], poster_state(m, 'movie', self.user, None))

        # Series with progress + future episode banner
        WatchedEpisode.objects.create(user=self.user, episode=self.eps[0])
        WatchedEpisode.objects.create(user=self.user, episode=self.eps[1])
        future = date.today() + timedelta(days=10)
        Episode.objects.create(season=self.eps[0].season, episode_number=3, title='E3', air_date=future)
        self.assertEqual(
            bulk_poster_states([self.show], 'series', self.user, 'cw')[self.show.id],
            poster_state(self.show, 'series', self.user, 'cw'),
        )
        self.assertEqual(
            bulk_poster_states([self.show], 'series', self.user, None)[self.show.id],
            poster_state(self.show, 'series', self.user, None),
        )

        # Catalog endpoint must not scale queries with item count (16 items in 6 queries total;
        # the 6th is the one UserSettings.show_plot lookup, constant per page)
        cache.clear()
        with self.assertNumQueries(6):
            res = self.client.get(f'/stremio/{self.config}/catalog/movie/watchlist-movies.json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()['metas']), 16)

    def test_poster_cache_uses_dedicated_posters_alias(self):
        from django.core.cache import caches
        from . import poster

        two_caches = {
            'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'default-only'},
            'posters': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'posters-only'},
        }
        with override_settings(CACHES=two_caches):
            with mock.patch.object(poster, 'render_catalog_poster', return_value=b'jpeg-bytes') as render:
                first = poster.get_cached_poster(self.movie, 'movie', self.user, None)
                second = poster.get_cached_poster(self.movie, 'movie', self.user, None)
            self.assertEqual(first, b'jpeg-bytes')
            self.assertEqual(second, b'jpeg-bytes')
            self.assertEqual(render.call_count, 1)
            # Stored in 'posters', not 'default'
            self.assertEqual(len(caches['default']._cache), 0)
            self.assertGreater(len(caches['posters']._cache), 0)

    def test_stremio_recommendation_build_warms_web_cache_too(self):
        from custom_auth.services.recommendation_cache import SCOPE_MOVIES, SCOPE_STREMIO, get_cached
        from .views import RECOMMENDATIONS_SIZE, _build_recommendation_movie_ids

        self.movie.predicted_rating = 8.4

        class FakeRec:
            def get_recommendations_for_user(self, user_id, max_recommendations=60):
                return [self.movie_ref]

        FakeRec.movie_ref = self.movie
        with mock.patch('stremio.views.MovieRecommender', FakeRec):
            ids = _build_recommendation_movie_ids(self.user)

        self.assertEqual(ids, [self.movie.id])
        self.assertEqual(get_cached(SCOPE_STREMIO, self.user.id, RECOMMENDATIONS_SIZE), [self.movie.id])
        web_cached = get_cached(SCOPE_MOVIES, self.user.id, 10)
        self.assertIsNotNone(web_cached)
        self.assertEqual(web_cached[0]['tmdb_id'], self.movie.tmdb_id)
