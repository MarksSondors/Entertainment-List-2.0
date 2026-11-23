"""Regression tests for the small-bugfix batch (#13, #36, #38, #39, #40)."""
import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from custom_auth.models import Person, Watchlist
from movies.models import Movie
from tvshows.models import (
    Episode, EpisodeGroup, EpisodeSubGroup, Season, TVShow, WatchedEpisode,
)

User = get_user_model()

# CI has no Redis and no TMDB token (see .github/workflows): never touch either.
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE)
class _NoSSLRedirect(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._override = override_settings(SECURE_SSL_REDIRECT=False)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        super().tearDownClass()


def _make_show(tmdb_id=880001, **extra):
    return TVShow.objects.create(
        title=f'Show {tmdb_id}', original_title=f'Show {tmdb_id}', tmdb_id=tmdb_id, **extra
    )


def _make_movie(tmdb_id=990001, **extra):
    return Movie.objects.create(
        title=f'Movie {tmdb_id}', original_title=f'Movie {tmdb_id}', tmdb_id=tmdb_id,
        description='d', rating=7.0, status='Released', runtime=100,
        release_date=datetime.date(2020, 1, 1), **extra
    )


class SubgroupEpisodesQueryTests(_NoSSLRedirect):
    """#13: queries must not grow with the number of episodes in a subgroup."""

    def setUp(self):
        self.user = User.objects.create_user(username='sg-viewer', email='sg-viewer@example.com', password='x')
        self.friend = User.objects.create_user(username='sg-friend', email='sg-friend@example.com', password='x')
        self.show = _make_show()
        self.season = Season.objects.create(show=self.show, season_number=1)
        group = EpisodeGroup.objects.create(name='Arcs', show=self.show)
        self.small = EpisodeSubGroup.objects.create(name='Small', parent_group=group)
        self.large = EpisodeSubGroup.objects.create(name='Large', parent_group=group, order=1)
        self._episodes(self.small, 2, start=1)
        self.large_eps = self._episodes(self.large, 12, start=10)
        self.client.force_login(self.user)
        self.client.get(reverse('subgroup_episodes', args=[self.small.id]))  # warm session middleware

    def _episodes(self, subgroup, count, start):
        eps = []
        for n in range(start, start + count):
            ep = Episode.objects.create(season=self.season, title=f'E{n}', episode_number=n,
                                        air_date=datetime.date(2020, 1, 1) + datetime.timedelta(days=n))
            subgroup.episodes.add(ep)
            WatchedEpisode.objects.create(user=self.friend, episode=ep)
            eps.append(ep)
        return eps

    def _count(self, subgroup):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('subgroup_episodes', args=[subgroup.id]))
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries), response.json()

    def test_query_count_is_constant(self):
        few, _ = self._count(self.small)
        many, _ = self._count(self.large)
        self.assertEqual(few, many)

    def test_watched_flags_and_watchers(self):
        WatchedEpisode.objects.create(user=self.user, episode=self.large_eps[0])
        _, data = self._count(self.large)
        first, second = data['episodes'][0], data['episodes'][1]
        self.assertTrue(first['is_watched'])
        self.assertCountEqual(first['watched_by'], ['sg-friend', 'sg-viewer'])
        self.assertFalse(second['is_watched'])
        self.assertEqual(second['watched_by'], ['sg-friend'])
        self.assertEqual(first['season_number'], 1)


class PersonFilmographyQueryTests(_NoSSLRedirect):
    """#36: in_database must be resolved without one query per credit."""

    def setUp(self):
        self.user = User.objects.create_user(username='film-viewer', email='film-viewer@example.com', password='x')
        self.person = Person.objects.create(name='Someone', tmdb_id=4242)
        _make_movie(tmdb_id=101)
        _make_show(tmdb_id=201)
        self.client.force_login(self.user)

    def _call(self, n_credits):
        movie_credits = {
            'cast': [{'id': 101, 'title': 'Known'}] + [{'id': 1000 + i, 'title': f'M{i}'} for i in range(n_credits)],
            'crew': [{'id': 2000 + i, 'title': f'C{i}', 'job': 'Director'} for i in range(n_credits)],
        }
        tv_credits = {
            'cast': [{'id': 201, 'name': 'Known show'}] + [{'id': 3000 + i, 'name': f'S{i}'} for i in range(n_credits)],
            'crew': [{'id': 4000 + i, 'name': f'T{i}', 'job': 'Writer'} for i in range(n_credits)],
        }
        # Patch the classes, not just the methods: their constructors read TMDB_BEARER_TOKEN
        with patch('api.services.movies.MoviesService') as movies_cls, \
             patch('api.services.tvshows.TVShowsService') as tv_cls, \
             CaptureQueriesContext(connection) as ctx:
            movies_cls.return_value.get_person_movie_credits.return_value = movie_credits
            tv_cls.return_value.get_person_tv_credits.return_value = tv_credits
            response = self.client.get(reverse('person_tmdb_filmography', args=[self.person.id]))
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries), response.json()

    def test_query_count_is_constant(self):
        self._call(1)  # warm session middleware
        few, _ = self._call(1)
        many, _ = self._call(25)
        self.assertEqual(few, many)

    def test_in_database_flags(self):
        _, data = self._call(2)
        movies = {m['id']: m['in_database'] for m in data['movies']}
        shows = {s['id']: s['in_database'] for s in data['tv_shows']}
        self.assertTrue(movies[101])
        self.assertFalse(movies[1000])
        self.assertTrue(shows[201])
        self.assertFalse(shows[3000])


@override_settings(MODEL_UPLOAD_KEY='correct-key')
class UploadModelAuthTests(_NoSSLRedirect):
    """#38: wrong or non-ASCII keys are rejected with 403, never a 500."""

    def _post(self, header):
        return self.client.post(reverse('upload_model'), HTTP_AUTHORIZATION=header)

    def test_wrong_key_rejected(self):
        self.assertEqual(self._post('Bearer wrong-key').status_code, 403)

    def test_non_ascii_key_rejected_not_500(self):
        self.assertEqual(self._post('Bearer kéy-ü').status_code, 403)

    def test_right_key_passes_auth(self):
        # Gets past auth and fails on the missing file instead
        self.assertEqual(self._post('Bearer correct-key').status_code, 400)


@override_settings(CACHES=LOCMEM_CACHE)
class CreateExistingTitleTests(TestCase):
    """#39: importing a title that already exists returns it instead of crashing."""

    def setUp(self):
        self.user = User.objects.create_user(username='importer', email='importer@example.com', password='x')

    def test_create_tvshow_returns_existing_without_tmdb_fetch(self):
        from tvshows.parsers import create_tvshow
        show = _make_show(tmdb_id=119769)
        with patch('tvshows.parsers.TVShowsService') as service:
            result = create_tvshow(119769, add_to_watchlist=True, user_id=self.user.id)
        service.assert_not_called()
        self.assertEqual(result.pk, show.pk)
        self.assertEqual(TVShow.objects.filter(tmdb_id=119769).count(), 1)
        ct = ContentType.objects.get_for_model(TVShow)
        self.assertTrue(Watchlist.objects.filter(user=self.user, content_type=ct, object_id=show.pk).exists())
        # Second add is a no-op, not an IntegrityError
        create_tvshow(119769, add_to_watchlist=True, user_id=self.user.id)
        self.assertEqual(Watchlist.objects.filter(user=self.user, content_type=ct, object_id=show.pk).count(), 1)

    def test_create_movie_basic_returns_existing_without_tmdb_fetch(self):
        from movies.parsers import create_movie, create_movie_basic
        movie = _make_movie(tmdb_id=555001)
        with patch('movies.parsers.MoviesService') as service:
            self.assertEqual(create_movie_basic(555001).pk, movie.pk)
            self.assertEqual(create_movie(555001).pk, movie.pk)
        service.assert_not_called()

    def test_create_movie_fast_does_not_reenrich_existing(self):
        from movies.tasks import create_movie_fast
        movie = _make_movie(tmdb_id=555002)
        with patch('movies.tasks.async_task') as enqueue, patch('movies.parsers.MoviesService'):
            self.assertEqual(create_movie_fast(555002).pk, movie.pk)
        enqueue.assert_not_called()


@override_settings(CACHES=LOCMEM_CACHE)
class BackdropFallbackTemplateTests(TestCase):
    """#40: a missing backdrop must not render url('None')."""

    def test_backdrop_guard_renders_fallback(self):
        snippet = ("background: {% if movie.backdrop %}url('{{ movie.backdrop }}') no-repeat center center fixed"
                   "{% else %}#008080{% endif %};")
        out = Template(snippet).render(Context({'movie': {'backdrop': None}}))
        self.assertNotIn('None', out)
        self.assertIn('#008080', out)

    def test_no_unguarded_backdrop_urls_left(self):
        import pathlib
        import re
        from django.conf import settings
        root = pathlib.Path(settings.BASE_DIR)
        unguarded = re.compile(r"background:\s*url\(['\"]?\{\{")
        offenders = [str(p.relative_to(root)) for p in root.rglob('*.html')
                     if 'staticfiles' not in p.parts and unguarded.search(p.read_text(errors='ignore'))]
        self.assertEqual(offenders, [])
