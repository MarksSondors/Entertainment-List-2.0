from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.utils import timezone

from custom_auth.views import DISCOVER_SEARCH_TYPES
from movies.models import Movie
from custom_auth.models import Review


LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE)   # CI has no Redis; saving a Review clears cache keys
class DiscoverSearchAllTests(TestCase):
    """/discover/search/?type=all answers every type in one response (Discover's search)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='finder', email='finder@example.test', password='***')
        self.movie = Movie.objects.create(
            title='Zanzibar Nights', tmdb_id=990077, runtime=100, rating=7.0,
            release_date=timezone.now().date(),
        )
        Review.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(Movie),
                              object_id=self.movie.id, rating=8.0)
        from custom_auth.models import Person
        Person.objects.create(name='Zanzibar Testperson')
        self.client.force_login(self.user)

    def get(self, **params):
        return self.client.get('/discover/search/', params, secure=True)

    def test_all_returns_every_type_with_counts(self):
        res = self.get(search='Zanzibar', type='all')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['search_type'], 'all')
        self.assertEqual(set(data['groups']), set(DISCOVER_SEARCH_TYPES))
        self.assertEqual(data['counts'], {t: len(r) for t, r in data['groups'].items()})
        self.assertEqual(data['count'], sum(data['counts'].values()))
        self.assertEqual([m['title'] for m in data['groups']['movies']], ['Zanzibar Nights'])
        self.assertEqual(data['groups']['movies'][0]['user_rating'], 8.0)
        self.assertEqual([p['name'] for p in data['groups']['people']], ['Zanzibar Testperson'])
        self.assertEqual(data['counts']['games'], 0)

    def test_all_matches_single_type_results(self):
        one = self.get(search='Zanzibar', type='movies').json()
        every = self.get(search='Zanzibar', type='all').json()
        self.assertEqual(one['results'], every['groups']['movies'])
        self.assertEqual(one['count'], every['counts']['movies'])

    def test_single_type_response_shape_unchanged(self):
        data = self.get(search='Zanzibar', type='movies').json()
        self.assertEqual(set(data), {'success', 'results', 'search_type', 'query', 'count'})

    def test_invalid_type_and_empty_query_are_400(self):
        self.assertEqual(self.get(search='Zanzibar', type='planets').status_code, 400)
        self.assertEqual(self.get(search='   ', type='all').status_code, 400)
