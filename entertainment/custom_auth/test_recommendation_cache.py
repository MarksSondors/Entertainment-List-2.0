"""The recommendation endpoints must not rebuild the 400 MB model per request.

The movie recommender is a ~397 MB pickle: unpickling it takes ~7 s and ~2.1 GB
of resident memory, and the web endpoints used to do that on every request.
The result is cached per user instead, and the cache is invalidated when the
user's ratings change.

CI has no Redis service; keep the cache in-process.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from custom_auth.models import Review
from movies.models import Movie


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class RecommendationCacheTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='rec-user', email='rec@example.test', password='x')
        self.movie = Movie.objects.create(
            title='Cached Movie', tmdb_id=990001, runtime=100, rating=7.5,
            release_date=timezone.now().date(),
        )

    def _fake_recommender(self, counter):
        movie = self.movie

        class FakeRecommender:
            def __init__(self):
                counter['built'] += 1

            def get_recommendations_for_user(self, user_id, limit, scope='local'):
                counter['scored'] += 1
                out = [movie]
                for m in out:
                    m.predicted_rating = 8.0
                return out

        return FakeRecommender

    def _review(self, rating=8.0):
        return Review.objects.create(
            user=self.user,
            content_type=ContentType.objects.get_for_model(Movie),
            object_id=self.movie.pk,
            rating=rating,
        )

    def test_second_request_is_served_from_the_cache(self):
        counter = {'built': 0, 'scored': 0}
        with patch('movies.views.MovieRecommender', self._fake_recommender(counter)):
            self.client.force_login(self.user)
            first = self.client.get('/movies/recommendations/', secure=True)
            second = self.client.get('/movies/recommendations/', secure=True)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(counter['built'], 1, 'the recommender must only be built once')
        self.assertEqual(counter['scored'], 1)

    def test_a_new_rating_invalidates_the_cache(self):
        counter = {'built': 0, 'scored': 0}
        with patch('movies.views.MovieRecommender', self._fake_recommender(counter)):
            self.client.force_login(self.user)
            self.client.get('/movies/recommendations/', secure=True)
            self._review()
            self.client.get('/movies/recommendations/', secure=True)

        self.assertEqual(counter['built'], 2, 'a new rating must bust the cache')

    def test_deleting_a_rating_invalidates_the_cache(self):
        counter = {'built': 0, 'scored': 0}
        review = self._review()
        with patch('movies.views.MovieRecommender', self._fake_recommender(counter)):
            self.client.force_login(self.user)
            self.client.get('/movies/recommendations/', secure=True)
            review.delete()
            self.client.get('/movies/recommendations/', secure=True)

        self.assertEqual(counter['built'], 2, 'deleting a rating must bust the cache')

    def test_the_model_version_is_part_of_the_key(self):
        from custom_auth.services import recommendation_cache

        with patch.object(recommendation_cache, 'model_version', return_value='v1'):
            first = recommendation_cache.cache_key('movies', self.user.pk, 10)
        with patch.object(recommendation_cache, 'model_version', return_value='v2'):
            second = recommendation_cache.cache_key('movies', self.user.pk, 10)
        self.assertNotEqual(first, second)

    def test_invalidate_user_makes_the_old_entries_unreachable(self):
        from custom_auth.services import recommendation_cache

        key = recommendation_cache.cache_key('movies', self.user.pk, 10)
        cache.set(key, ['stale'], 60)
        self.assertEqual(cache.get(key), ['stale'])

        recommendation_cache.invalidate_user(self.user.pk)

        self.assertIsNone(cache.get(recommendation_cache.cache_key('movies', self.user.pk, 10)))

    def test_combined_recommendations_are_cached(self):
        counter = {'built': 0, 'scored': 0}
        movie = self.movie

        class FakeCrossMedia:
            def __init__(self):
                counter['built'] += 1

            def get_discovery_feed(self, user, total_items=20, media_types=None):
                return [{'id': movie.pk, 'title': movie.title}]

        with patch('custom_auth.views.CrossMediaRecommender', FakeCrossMedia):
            self.client.force_login(self.user)
            first = self.client.get('/api/recommendations/combined/', secure=True)
            second = self.client.get('/api/recommendations/combined/', secure=True)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(counter['built'], 1, 'the cross-media recommender must only be built once')

    def test_external_recommendations_are_cached(self):
        counter = {'built': 0, 'scored': 0}

        class FakeRecommender:
            def __init__(self):
                counter['built'] += 1

            def get_recommendations_for_user(self, user_id, limit, scope='local'):
                counter['scored'] += 1
                return []

        with patch('movies.views.MovieRecommender', FakeRecommender):
            self.client.force_login(self.user)
            first = self.client.get('/movies/recommendations/external/', secure=True)
            second = self.client.get('/movies/recommendations/external/', secure=True)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), [])
        self.assertEqual(counter['built'], 1, 'the recommender must only be built once')


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class PersonFullTextIndexTests(TestCase):
    """A people search used to seq-scan 73k rows (~290 ms) for every query."""

    def test_the_gin_index_exists(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                "select indexdef from pg_indexes where indexname = 'custom_auth_person_name_fts_gin'"
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row, 'the people full-text index is missing')
        self.assertIn('gin', row[0].lower())

    def test_discover_people_search_batches_credits_and_reviews(self):
        from custom_auth.models import MediaPerson, Person

        User = get_user_model()
        user = User.objects.create_user(username='searcher', email='searcher@example.test', password='x')
        movie_ct = ContentType.objects.get_for_model(Movie)
        movie = Movie.objects.create(
            title='Inception', tmdb_id=990055, runtime=148, rating=8.8,
            release_date=timezone.now().date(),
        )
        Review.objects.create(user=user, content_type=movie_ct, object_id=movie.id, rating=9.0)

        for i in range(8):
            p = Person.objects.create(name=f'Nolan Testperson {i}')
            MediaPerson.objects.create(person=p, content_type=movie_ct, object_id=movie.id, role='Director')

        self.client.force_login(user)
        # 8 people must not trigger 16+ N+1 queries (session + user + people + MediaPerson + Review + last_active = 6)
        with self.assertNumQueries(6):
            res = self.client.get('/discover/search/', {'search': 'Nolan', 'type': 'people'}, secure=True)
        self.assertEqual(res.status_code, 200)
        results = res.json()['results']
        self.assertEqual(len(results), 8)
        self.assertEqual(results[0]['avg_rating'], 9.0)
        self.assertEqual(results[0]['rating_count'], 1)
