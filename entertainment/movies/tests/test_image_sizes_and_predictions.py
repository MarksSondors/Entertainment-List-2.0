"""TMDB image sizing, the "For you" predicted rating, and task-history hygiene."""

import datetime
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from custom_auth.models import Review, Watchlist
from custom_auth.services.image_sizes import tmdb_size
from movies.models import Movie
from movies.services import predicted_ratings

User = get_user_model()
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
ORIG = 'https://image.tmdb.org/t/p/original/abc.jpg'


class TmdbSizeTests(SimpleTestCase):
    def test_roles_and_raw_sizes(self):
        self.assertEqual(tmdb_size(ORIG, 'poster'), 'https://image.tmdb.org/t/p/w342/abc.jpg')
        self.assertEqual(tmdb_size(ORIG, 'backdrop'), 'https://image.tmdb.org/t/p/w1280/abc.jpg')
        self.assertEqual(tmdb_size(ORIG, 'profile'), 'https://image.tmdb.org/t/p/w185/abc.jpg')
        self.assertEqual(tmdb_size(ORIG, 'w92'), 'https://image.tmdb.org/t/p/w92/abc.jpg')

    def test_non_tmdb_and_empty_pass_through(self):
        for url in (None, '', 'https://media.rawg.io/media/games/x.jpg',
                    '/media/profile_pictures/a.png', 'https://image.tmdb.org/t/p/w500/abc.jpg'):
            self.assertEqual(tmdb_size(url, 'poster'), url)

    def test_template_filter_is_builtin_and_keeps_default(self):
        tpl = Template("{{ p|tmdb:'poster' }}|{{ none|tmdb:'poster_lg'|default:'fallback' }}")
        self.assertEqual(tpl.render(Context({'p': ORIG, 'none': None})),
                         'https://image.tmdb.org/t/p/w342/abc.jpg|fallback')


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class MoviePageImagesAndPredictionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='viewer', email='viewer@example.com', password='pw')
        self.client.force_login(self.user)
        self.movie = Movie.objects.create(
            title='Sized', original_title='Sized', tmdb_id=910001, description='plot', rating=7.0,
            status='Released', runtime=100, release_date=datetime.date(2001, 5, 4),
            poster='https://image.tmdb.org/t/p/original/poster.jpg',
            backdrop='https://image.tmdb.org/t/p/original/backdrop.jpg',
        )

    def page(self):
        return self.client.get(reverse('movie_page', args=[self.movie.tmdb_id])).content.decode()

    def test_movie_page_serves_resized_poster_and_backdrop(self):
        html = self.page()
        self.assertIn('/t/p/w500/poster.jpg', html)
        self.assertIn('/t/p/w1280/backdrop.jpg', html)

    def test_watchlist_html_and_api_use_card_size(self):
        Watchlist.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie),
                                 object_id=self.movie.id)
        html = self.client.get(reverse('watchlist_page')).content.decode()
        self.assertIn('/t/p/w342/poster.jpg', html)
        self.assertNotIn('/t/p/original/poster.jpg', html)
        self.assertRegex(html, r'<img loading="lazy" decoding="async" src="[^"]*/t/p/w342/poster\.jpg"')
        api = self.client.get('/api/watchlist/').content.decode()
        self.assertIn('/t/p/w342/poster.jpg', api)
        self.assertNotIn('/t/p/original/', api)

    def test_prediction_shown_from_cache_only(self):
        self.assertNotIn('For You:', self.page())  # nothing cached -> nothing shown
        cache.set(f'predicted_ratings:{self.user.id}', {self.movie.tmdb_id: 7.8})
        self.assertRegex(self.page(), r'For You:</strong> <span[^>]*>7\.8/10')

    def test_prediction_hidden_once_rated(self):
        cache.set(f'predicted_ratings:{self.user.id}', {self.movie.tmdb_id: 7.8})
        Review.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie),
                              object_id=self.movie.id, rating=6)
        self.assertNotIn('For You:', self.page())

    def test_movie_page_never_loads_the_model(self):
        with mock.patch('movies.services.recommendation.MovieRecommender.__init__',
                        side_effect=AssertionError('model loaded in a web request')):
            self.assertEqual(self.client.get(reverse('movie_page', args=[self.movie.tmdb_id])).status_code, 200)

    def test_build_for_user_caches_clamped_display_scale(self):
        other = Movie.objects.create(title='O', original_title='O', tmdb_id=910002, description='p',
                                     rating=6.0, status='Released', runtime=90)
        rec = mock.Mock()
        rec.get_user_factor.return_value = [0.1]
        rec._user_factor.return_value = [0.1]
        rec.predict_rating.side_effect = lambda uid, tmdb, year=None, user_factor_override=None: (
            3.9 if tmdb == self.movie.tmdb_id else 9.0)
        self.assertEqual(predicted_ratings.build_for_user(rec, self.user), 2)
        self.assertEqual(predicted_ratings.get_for_movie(self.user.id, self.movie.tmdb_id), 7.8)
        self.assertEqual(predicted_ratings.get_for_movie(self.user.id, other.tmdb_id), 10.0)

    def test_build_for_user_without_factor_clears_cache(self):
        cache.set(f'predicted_ratings:{self.user.id}', {1: 5.0})
        rec = mock.Mock()
        rec.get_user_factor.return_value = None
        self.assertEqual(predicted_ratings.build_for_user(rec, self.user), 0)
        self.assertIsNone(predicted_ratings.get_for_movie(self.user.id, 1))


class TaskHygieneTests(TestCase):
    def test_clear_expired_sessions(self):
        from custom_auth.tasks import clear_expired_sessions
        now = timezone.now()
        Session.objects.create(session_key='old', session_data='x', expire_date=now - datetime.timedelta(days=1))
        Session.objects.create(session_key='new', session_data='x', expire_date=now + datetime.timedelta(days=1))
        self.assertEqual(clear_expired_sessions(), '1 expired sessions removed')
        self.assertEqual(list(Session.objects.values_list('session_key', flat=True)), ['new'])

    def test_clear_sessions_is_scheduled(self):
        from django_q.models import Schedule
        from custom_auth.apps import _setup_custom_auth_schedules
        _setup_custom_auth_schedules(sender=None)
        self.assertTrue(Schedule.objects.filter(func='custom_auth.tasks.clear_expired_sessions').exists())

    def test_warm_poster_is_not_saved_on_success(self):
        import inspect
        from stremio import signals, views
        for module in (signals, views):
            calls = re.findall(r"async_task\('stremio\.tasks\.warm_poster'[^\n]*", inspect.getsource(module))
            self.assertTrue(calls)
            self.assertTrue(all('save=False' in c for c in calls), calls)
