"""Tests for the Discover page's Quickest Watch / Random Movie quick picks."""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from custom_auth.models import Review, Watchlist
from movies.models import Movie
from movies.services import quick_picks

User = get_user_model()

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class QuickPickTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='picker', email='picker@example.com', password='pw')
        self.other = User.objects.create_user(username='other', email='other@example.com', password='pw')
        self.client.force_login(self.user)
        self.ct = ContentType.objects.get_for_model(Movie)
        self._tmdb = 800000

    def movie(self, title, runtime=100, status='Released', poster=None):
        self._tmdb += 1
        return Movie.objects.create(
            title=title, original_title=title, tmdb_id=self._tmdb, description=f'{title} plot',
            rating=7.25, status=status, runtime=runtime, release_date=datetime.date(2001, 5, 4),
            poster=poster,
        )

    def watchlist(self, movie, user=None):
        Watchlist.objects.create(user=user or self.user, content_type=self.ct, object_id=movie.id)

    def review(self, movie, user=None):
        Review.objects.create(user=user or self.user, content_type=self.ct, object_id=movie.id, rating=7)

    # Quickest Watch -------------------------------------------------------

    def test_quickest_returns_shortest_unwatched_watchlist_movie(self):
        long_one = self.movie('Long', runtime=180)
        short_one = self.movie('Short', runtime=85)
        seen = self.movie('Seen Short', runtime=70)
        unreleased = self.movie('Upcoming', runtime=60, status='In Production')
        broken = self.movie('No Runtime', runtime=0)
        self.movie('Not On List', runtime=50)
        for m in (long_one, short_one, seen, unreleased, broken):
            self.watchlist(m)
        self.review(seen)

        pick = quick_picks.pick_quickest_movie(self.user)

        self.assertEqual(pick.movie, short_one)
        self.assertEqual(pick.source, 'watchlist')
        self.assertEqual((pick.position, pick.pool_size), (1, 2))

    def test_quickest_steps_through_and_wraps(self):
        a, b = self.movie('A', runtime=90), self.movie('B', runtime=95)
        self.watchlist(a)
        self.watchlist(b)

        second = quick_picks.pick_quickest_movie(self.user, exclude_ids=[a.id])
        self.assertEqual((second.movie, second.position, second.wrapped), (b, 2, False))

        wrapped = quick_picks.pick_quickest_movie(self.user, exclude_ids=[a.id, b.id])
        self.assertEqual((wrapped.movie, wrapped.wrapped), (a, True))

    def test_quickest_falls_back_to_catalog_without_watchlist(self):
        self.movie('Long', runtime=150)
        short = self.movie('Short', runtime=80)
        seen = self.movie('Seen', runtime=60)
        self.review(seen)

        pick = quick_picks.pick_quickest_movie(self.user)

        self.assertEqual((pick.movie, pick.source), (short, 'catalog'))

    # Random Movie ---------------------------------------------------------

    def test_random_prefers_movies_nobody_reviewed(self):
        fresh = self.movie('Fresh')
        reviewed = self.movie('Reviewed by other')
        self.review(reviewed, user=self.other)
        self.movie('Upcoming', status='Post Production')

        for _ in range(5):
            pick = quick_picks.pick_random_movie(self.user)
            self.assertEqual((pick.movie, pick.source), (fresh, 'unwatched'))

    def test_random_never_serves_unreleased_or_own_reviewed(self):
        mine = self.movie('Mine')
        self.review(mine)
        self.movie('Upcoming', status='In Production')
        once = self.movie('Once')
        twice = self.movie('Twice')
        self.review(once, user=self.other)
        self.review(twice, user=self.other)
        extra = User.objects.create_user(username='extra', email='extra@example.com', password='pw')
        self.review(twice, user=extra)

        pick = quick_picks.pick_random_movie(self.user)

        self.assertEqual((pick.movie, pick.source), (once, 'least_watched'))

    def test_random_excludes_seen_until_exhausted(self):
        a, b = self.movie('A'), self.movie('B')
        self.assertEqual(quick_picks.pick_random_movie(self.user, exclude_ids=[a.id]).movie, b)
        wrapped = quick_picks.pick_random_movie(self.user, exclude_ids=[a.id, b.id])
        self.assertTrue(wrapped.wrapped)
        self.assertIn(wrapped.movie, (a, b))

    def test_parse_exclude_ids_ignores_junk_and_caps(self):
        self.assertEqual(quick_picks.parse_exclude_ids('1, x,2,,-3,4'), [1, 2, 4])
        self.assertEqual(len(quick_picks.parse_exclude_ids(','.join(['7'] * 500))), quick_picks.MAX_EXCLUDED_IDS)

    # Endpoints ------------------------------------------------------------

    def test_quick_pick_endpoint_payload(self):
        m = self.movie('Tiny', runtime=75, poster='https://image.tmdb.org/t/p/original/abc.jpg')
        self.watchlist(m)

        response = self.client.get(reverse('movie_quick_pick'), {'mode': 'quickest'})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['source'], 'watchlist')
        movie = data['movie']
        self.assertEqual(movie['id'], m.id)
        self.assertEqual(movie['runtime_display'], '1h 15m')
        self.assertEqual(movie['poster'], 'https://image.tmdb.org/t/p/w342/abc.jpg')
        self.assertEqual(movie['url'], f'/movies/{m.tmdb_id}/')
        self.assertTrue(movie['on_watchlist'])
        self.assertFalse(movie['watched'])
        self.assertEqual(movie['year'], 2001)

    def test_quick_pick_endpoint_validation_and_empty(self):
        self.assertEqual(self.client.get(reverse('movie_quick_pick'), {'mode': 'nope'}).status_code, 400)
        data = self.client.get(reverse('movie_quick_pick'), {'mode': 'random'}).json()
        self.assertIsNone(data['movie'])

    def test_quick_pick_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('movie_quick_pick'), {'mode': 'random'})
        self.assertEqual(response.status_code, 302)

    def test_redirect_fallbacks_use_same_picks(self):
        short = self.movie('Short', runtime=80)
        self.watchlist(short)
        response = self.client.get(reverse('shortest_watchlist_movie'))
        self.assertRedirects(response, reverse('movie_page', args=[short.tmdb_id]), fetch_redirect_response=False)

        response = self.client.get(reverse('random_unwatched_movie'))
        self.assertRedirects(response, reverse('movie_page', args=[short.tmdb_id]), fetch_redirect_response=False)

    def test_redirect_fallbacks_with_empty_library(self):
        self.assertRedirects(self.client.get(reverse('shortest_watchlist_movie')),
                             reverse('watchlist_page'), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse('random_unwatched_movie')),
                             reverse('discover_page'), fetch_redirect_response=False)
