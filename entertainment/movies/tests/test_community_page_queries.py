"""Regression test for an N+1 on the community page.

``community_page`` used to fetch ``MovieOfWeekPick`` rows without
``select_related('movie')``, while the template reads ``pick.movie`` for every
row. With 25 completed picks the page issued 25 extra movie SELECTs (33 queries
total, 7 distinct). The fix brings the page to a constant number of queries.

The assertion deliberately tests *scaling* (queries do not grow with the number
of picks) instead of a magic constant, so it stays meaningful as the page grows.
"""
import datetime

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from movies.models import Movie, MovieOfWeekPick

User = get_user_model()


class CommunityPageQueryScalingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='community-query-tester', password='irrelevant-pw'
        )
        self.client.force_login(self.user)

    def _add_completed_picks(self, count, offset=0):
        for i in range(count):
            movie = Movie.objects.create(
                title=f'Pick Movie {offset + i}',
                original_title=f'Pick Movie {offset + i}',
                tmdb_id=900000 + offset + i,
                description='desc',
                rating=7.0,
                status='Released',
                runtime=100,
                release_date=datetime.date(2020, 1, 1),
            )
            MovieOfWeekPick.objects.create(
                movie=movie,
                suggested_by=self.user,
                status='completed',
                start_date=timezone.now() - datetime.timedelta(days=7),
                end_date=timezone.now() - datetime.timedelta(days=1),
            )

    def _query_count(self):
        # Warm caches (session, user, template loading) before counting.
        self.client.get(reverse('community_page'))
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse('community_page'))
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_query_count_does_not_grow_with_completed_picks(self):
        self._add_completed_picks(2)
        small = self._query_count()

        self._add_completed_picks(20, offset=100)
        large = self._query_count()

        self.assertLessEqual(
            large, small,
            f'community_page issues more queries as picks are added '
            f'({small} with 2 picks, {large} with 22) - the pick rows are not '
            f'using select_related, so pick.movie costs one query each.',
        )
