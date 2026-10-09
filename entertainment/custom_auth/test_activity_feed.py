"""Community activity feed: titles added to the database (including automatic
"System" imports) show up without duplicating watchlist/review entries."""
import datetime

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from custom_auth.models import Watchlist
from games.models import Game
from movies.models import Movie
from tvshows.models import TVShow

User = get_user_model()

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def make_movie(tmdb_id, added_by=None, minutes_ago=0, **extra):
    movie = Movie.objects.create(
        title=f'Movie {tmdb_id}', original_title=f'Movie {tmdb_id}', tmdb_id=tmdb_id,
        runtime=100, rating=7.0, status='Released', description='desc', added_by=added_by, **extra,
    )
    # date_added is auto_now_add, so backdate with an update
    Movie.objects.filter(pk=movie.pk).update(date_added=timezone.now() - datetime.timedelta(minutes=minutes_ago))
    return movie


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class CommunityFeedAddedContentTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(username='viewer', email='viewer@example.com', password='pw')
        self.client.force_login(self.viewer)

    def _feed(self, **params):
        response = self.client.get(reverse('recent_activity'), params)
        self.assertEqual(response.status_code, 200)
        return response.json()['results']

    def test_system_added_movie_is_shown_without_profile_link(self):
        make_movie(1)

        [row] = self._feed()

        self.assertEqual(row['username'], 'System')
        self.assertEqual(row['action'], 'added to database')
        self.assertEqual(row['kind'], 'added')
        self.assertIsNone(row['profile_url'])
        self.assertFalse(row['is_own'])
        self.assertEqual(row['detail_url'], '/movies/1')

    def test_system_added_tv_show_is_shown(self):
        TVShow.objects.create(title='Show', original_title='Show', tmdb_id=7)

        [row] = self._feed()

        self.assertEqual((row['username'], row['content_type'], row['action']), ('System', 'TV Show', 'added to database'))
        self.assertEqual(row['detail_url'], '/tvshows/7')

    def test_user_add_plus_watchlist_is_one_watchlist_entry(self):
        adder = User.objects.create_user(username='adder', email='adder@example.com', password='pw')
        movie = make_movie(2, added_by=adder)
        Watchlist.objects.create(user=adder, content_type=ContentType.objects.get_for_model(Movie), object_id=movie.id)

        [row] = self._feed()

        self.assertEqual((row['username'], row['action'], row['kind']), ('adder', 'added to watchlist', 'watchlist'))
        self.assertIsNotNone(row['profile_url'])

    def test_burst_of_system_imports_collapses_into_one_bundle(self):
        for i in range(5):
            make_movie(100 + i, minutes_ago=i)

        [row] = self._feed()

        self.assertEqual(row['kind'], 'bundle')
        self.assertEqual(row['bundle_of'], 'added')
        self.assertEqual(row['action'], 'added 5 movies to database')
        self.assertEqual(row['username'], 'System')
        self.assertIsNone(row['profile_url'])
        self.assertEqual(len(row['items']), 5)

    def test_filters(self):
        make_movie(3)
        TVShow.objects.create(title='Show', original_title='Show', tmdb_id=8)

        self.assertEqual([r['content_type'] for r in self._feed(media='movies')], ['Movie'])
        self.assertEqual([r['content_type'] for r in self._feed(media='tvshows')], ['TV Show'])
        # The activity-kind filters (reviews / watched / watchlist) don't include additions
        self.assertEqual(self._feed(kind='reviews'), [])
        self.assertEqual(self._feed(kind='watchlist'), [])

    def test_movie_and_game_with_same_id_are_not_merged(self):
        adder = User.objects.create_user(username='adder2', email='adder2@example.com', password='pw')
        movie = make_movie(4, added_by=adder)
        game = Game.objects.create(id=movie.id, title='Game', original_title='Game', rawg_id=55, added_by=adder)

        rows = self._feed()

        # Both additions are present (possibly bundled together as one burst)
        titles = {item['title'] for row in rows for item in row.get('items', [row])}
        self.assertEqual(titles, {movie.title, game.title})


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class ProfileFeedAddedContentTests(TestCase):
    def test_profile_feed_includes_tv_shows_the_user_added(self):
        user = User.objects.create_user(username='curator', email='curator@example.com', password='pw')
        self.client.force_login(user)
        TVShow.objects.create(title='Their Show', original_title='Their Show', tmdb_id=9, added_by=user)
        TVShow.objects.create(title='System Show', original_title='System Show', tmdb_id=10)

        response = self.client.get(reverse('user_recent_activity', args=['curator']))

        rows = response.json()['results']
        self.assertEqual([(r['title'], r['action']) for r in rows], [('Their Show', 'added to database')])
