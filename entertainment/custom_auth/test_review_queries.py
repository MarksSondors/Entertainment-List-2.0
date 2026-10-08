from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from custom_auth.models import Review
from movies.models import Movie
from tvshows.models import Season, TVShow

User = get_user_model()

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class RecentReviewsTests(TestCase):
    def setUp(self):
        self.movie_ct = ContentType.objects.get_for_model(Movie)
        self.tv_ct = ContentType.objects.get_for_model(TVShow)

    def _add_reviews(self, count, offset):
        for i in range(offset, offset + count):
            user = User.objects.create_user(username=f'reviewer{i}', email=f'reviewer{i}@example.com', password='pw')
            movie = Movie.objects.create(title=f'Movie {i}', original_title=f'Movie {i}', tmdb_id=10_000 + i,
                                         runtime=100, rating=7.0, status='Released', description='desc')
            show = TVShow.objects.create(title=f'Show {i}', original_title=f'Show {i}', tmdb_id=20_000 + i)
            season = Season.objects.create(show=show, season_number=1)
            # bulk_create skips Review.save()'s "watched every episode" guard for TV fixtures
            Review.objects.bulk_create([
                Review(user=user, content_type=self.movie_ct, object_id=movie.id, rating=7),
                Review(user=user, content_type=self.tv_ct, object_id=show.id, season=season, rating=8),
            ])

    def test_query_count_is_constant_and_titles_are_formatted(self):
        self._add_reviews(1, offset=0)
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse('recent_reviews'))

        self._add_reviews(5, offset=1)
        with CaptureQueriesContext(connection) as many:
            data = self.client.get(reverse('recent_reviews')).json()

        self.assertEqual(len(data), 10)
        self.assertEqual(len(many), len(few))
        tv_titles = [r['title'] for r in data if r['content_type'] == 'TV Show']
        self.assertTrue(tv_titles)
        for title in tv_titles:
            self.assertRegex(title, r'^Show \d+ - Season 1$')


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class ProfileFavoriteShowsTests(TestCase):
    def test_favorite_shows_are_averaged_per_show_and_sorted(self):
        user = User.objects.create_user(username='fan', password='pw')
        self.client.force_login(user)
        tv_ct = ContentType.objects.get_for_model(TVShow)

        reviews = []
        for tmdb_id, ratings in ((1, [9, 7]), (2, [10]), (3, [4, 6, 8])):
            show = TVShow.objects.create(title=f'Show {tmdb_id}', original_title='x', tmdb_id=tmdb_id)
            for n, rating in enumerate(ratings, start=1):
                season = Season.objects.create(show=show, season_number=n)
                reviews.append(Review(user=user, content_type=tv_ct, object_id=show.id, season=season, rating=rating))
        Review.objects.bulk_create(reviews)

        response = self.client.get(reverse('profile_page'))

        shows = response.context['favorite_shows']
        self.assertEqual([s.title for s in shows], ['Show 2', 'Show 1', 'Show 3'])
        self.assertEqual([s.user_rating for s in shows], [10.0, 8.0, 6.0])
        self.assertEqual([s.review_count for s in shows], [1, 2, 3])
