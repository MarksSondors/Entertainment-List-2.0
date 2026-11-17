"""Tests for the Discover page's "What should we watch tonight?" group picker."""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse

from custom_auth.models import Genre, Review, Watchlist
from movies.models import Collection, Movie
from movies.services import group_pick

User = get_user_model()

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class GroupPickTests(TestCase):
    def setUp(self):
        self.ann = User.objects.create_user(username='ann', email='ann@example.com', password='pw')
        self.bob = User.objects.create_user(username='bob', email='bob@example.com', password='pw')
        self.client.force_login(self.ann)
        self.ct = ContentType.objects.get_for_model(Movie)
        self.horror = Genre.objects.create(name='Horror')
        self.comedy = Genre.objects.create(name='Comedy')
        self._tmdb = 810000
        self._titles = 0

    def movie(self, title=None, runtime=100, status='Released', rating=7.0, genres=(),
              poster=None, collection=None, release_date=datetime.date(2001, 5, 4)):
        self._tmdb += 1
        if title is None:
            self._titles += 1
            title = f'Untitled {self._titles}'
        movie = Movie.objects.create(
            title=title, original_title=title, tmdb_id=self._tmdb, description=f'{title} plot',
            rating=rating, status=status, runtime=runtime, release_date=release_date, poster=poster,
            collection=collection,
        )
        if genres:
            movie.genres.set(genres)
        return movie

    def rate(self, movie, rating, user=None):
        return Review.objects.create(
            user=user or self.ann, content_type=self.ct, object_id=movie.id, rating=rating,
        )

    def watchlist(self, movie, user=None):
        return Watchlist.objects.create(
            user=user or self.ann, content_type=self.ct, object_id=movie.id,
        )

    def seed_history(self):
        """Both people rate the same films: horror above their average, comedy below."""
        for rating in (9, 9, 10, 9, 8):
            movie = self.movie(genres=[self.horror])
            for user in (self.ann, self.bob):
                self.rate(movie, rating, user=user)
        for rating in (3, 4, 3):
            movie = self.movie(genres=[self.comedy])
            for user in (self.ann, self.bob):
                self.rate(movie, rating, user=user)

    # Ranking --------------------------------------------------------------

    def test_prefers_a_genre_everyone_rates_above_their_average(self):
        self.seed_history()
        horror = self.movie('New Horror', genres=[self.horror])
        comedy = self.movie('New Comedy', genres=[self.comedy])

        picks, meta = group_pick.pick_for_group([self.ann.id, self.bob.id])

        self.assertEqual([p.movie['id'] for p in picks], [horror.id, comedy.id])
        self.assertGreater(picks[0].score, picks[1].score)
        self.assertEqual(meta['members'], ['ann', 'bob'])

    def test_one_persons_dislike_can_sink_a_title(self):
        # ann likes horror; bob rates it below his own average and comedy above it.
        self.rate(self.movie(genres=[self.horror]), 10)
        self.rate(self.movie(genres=[self.horror]), 9)
        self.rate(self.movie(genres=[self.comedy]), 6)
        self.rate(self.movie(genres=[self.horror]), 2, user=self.bob)
        self.rate(self.movie(genres=[self.comedy]), 8, user=self.bob)
        self.rate(self.movie(genres=[self.comedy]), 9, user=self.bob)

        horror = self.movie('Group Horror', genres=[self.horror])
        comedy = self.movie('Group Comedy', genres=[self.comedy])

        picks, _meta = group_pick.pick_for_group([self.ann.id, self.bob.id])

        scores = {p.movie['id']: p.score for p in picks}
        self.assertEqual(picks[0].movie['id'], comedy.id)
        self.assertGreater(scores[comedy.id], scores[horror.id])
        # The horror film ann loves loses because of the vote bob is cold on.
        horror_pick = [p for p in picks if p.movie['id'] == horror.id][0]
        self.assertEqual(horror_pick.weakest['username'], 'bob')
        self.assertLess(horror_pick.weakest['score'], 0)

    def test_excludes_anything_any_member_has_rated(self):
        self.seed_history()
        seen_by_bob = self.movie('Bob Saw It', genres=[self.horror])
        self.rate(seen_by_bob, 8, user=self.bob)
        unseen = self.movie('Nobody Saw It', genres=[self.horror])

        picks, _meta = group_pick.pick_for_group([self.ann.id, self.bob.id])

        ids = [p.movie['id'] for p in picks]
        self.assertNotIn(seen_by_bob.id, ids)
        self.assertIn(unseen.id, ids)

    def test_unreleased_and_runtimeless_films_are_ignored(self):
        self.seed_history()
        self.movie('Coming Soon', status='In Production', genres=[self.horror])
        self.movie('No Runtime', runtime=0, genres=[self.horror])
        candidate = self.movie('Actual Candidate', genres=[self.horror])

        picks, _meta = group_pick.pick_for_group([self.ann.id])

        self.assertEqual([p.movie['id'] for p in picks], [candidate.id])

    def test_watchlist_pushes_a_title_up(self):
        plain = self.movie('Plain')
        wanted = self.movie('Wanted')
        self.watchlist(wanted, user=self.bob)

        picks, _meta = group_pick.pick_for_group([self.ann.id, self.bob.id])

        self.assertEqual(picks[0].movie['id'], wanted.id)
        self.assertEqual(picks[0].watchlisted_by, ['bob'])
        self.assertIn("bob's watchlist", picks[0].reason)

    def test_one_film_per_collection(self):
        collection = Collection.objects.create(name='Saga', tmdb_id=990001)
        first = self.movie('Saga One', collection=collection)
        second = self.movie('Saga Two', collection=collection)
        solo = self.movie('Unrelated')

        picks, _meta = group_pick.pick_for_group([self.ann.id])

        ids = [p.movie['id'] for p in picks]
        self.assertEqual(len([i for i in ids if i in (first.id, second.id)]), 1)
        self.assertIn(solo.id, ids)

    def test_single_member_pick_has_no_weakest_vote(self):
        self.movie('Only Choice')
        picks, _meta = group_pick.pick_for_group([self.ann.id])
        self.assertIsNone(picks[0].weakest)

    def test_empty_pool_explains_itself(self):
        only = self.movie('The Only Film')
        self.rate(only, 8)

        picks, meta = group_pick.pick_for_group([self.ann.id])

        self.assertEqual(picks, [])
        self.assertEqual(meta['pool_size'], 0)
        self.assertIn('ann has not already rated', group_pick.empty_reason(meta))

    def test_exclude_ids_walks_further_down_the_list(self):
        made = [self.movie(f'Film {n}') for n in range(4)]

        first_batch, _ = group_pick.pick_for_group([self.ann.id], limit=2)
        second_batch, _ = group_pick.pick_for_group(
            [self.ann.id], exclude_ids=[p.movie['id'] for p in first_batch], limit=2,
        )

        seen = [p.movie['id'] for p in first_batch] + [p.movie['id'] for p in second_batch]
        self.assertEqual(len(set(seen)), len(made))

    def test_unknown_member_ids_are_dropped(self):
        self.movie('Only Choice')
        picks, meta = group_pick.pick_for_group([self.ann.id, 999999], limit=1)
        self.assertEqual(len(picks), 1)
        self.assertEqual(meta['members'], ['ann'])

    def test_limit_is_capped(self):
        for _ in range(3):
            self.movie()
        picks, _meta = group_pick.pick_for_group([self.ann.id], limit=500)
        self.assertEqual(len(picks), 3)

    # Parsing --------------------------------------------------------------

    def test_parse_member_ids_ignores_junk_and_caps(self):
        self.assertEqual(group_pick.parse_member_ids('1, x,2,,-3,2'), [1, 2])
        self.assertEqual(len(group_pick.parse_member_ids(','.join(str(i) for i in range(1, 51)))), group_pick.MAX_MEMBERS)

    def test_eligible_members_only_lists_people_who_have_rated(self):
        self.rate(self.movie(), 8)
        self.assertEqual([u.username for u in group_pick.eligible_members()], ['ann'])

    # Endpoint -------------------------------------------------------------

    def test_endpoint_payload(self):
        self.seed_history()
        movie = self.movie('The Pick', genres=[self.horror],
                           poster='https://image.tmdb.org/t/p/original/xyz.jpg')

        response = self.client.get(
            reverse('watch_together'), {'members': f'{self.ann.id},{self.bob.id}'},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['members'], ['ann', 'bob'])
        self.assertGreaterEqual(data['pool_size'], 1)
        pick = data['picks'][0]
        self.assertEqual(pick['id'], movie.id)
        self.assertEqual(pick['poster'], 'https://image.tmdb.org/t/p/w342/xyz.jpg')
        self.assertEqual(pick['url'], f'/movies/{movie.tmdb_id}/')
        self.assertEqual(pick['runtime_display'], '1h 40m')
        self.assertTrue(0 < pick['match'] <= 99)
        self.assertTrue(pick['reason'])

    def test_endpoint_rejects_missing_members(self):
        response = self.client.get(reverse('watch_together'))
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    def test_endpoint_honours_limit(self):
        for _ in range(5):
            self.movie()
        data = self.client.get(
            reverse('watch_together'), {'members': str(self.ann.id), 'limit': '2'},
        ).json()
        self.assertEqual(len(data['picks']), 2)

    def test_endpoint_survives_junk_limit(self):
        self.movie()
        data = self.client.get(
            reverse('watch_together'), {'members': str(self.ann.id), 'limit': 'lots'},
        ).json()
        self.assertEqual(data['pool_size'], 1)

    def test_endpoint_reports_an_empty_pool(self):
        self.rate(self.movie('Seen It'), 7)
        data = self.client.get(reverse('watch_together'), {'members': str(self.ann.id)}).json()
        self.assertEqual(data['picks'], [])
        self.assertIn('ann', data['reason'])

    def test_endpoint_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse('watch_together'), {'members': str(self.ann.id)})
        self.assertEqual(response.status_code, 302)

    def test_discover_page_offers_the_picker(self):
        self.rate(self.movie('Rated'), 8)
        response = self.client.get(reverse('discover_page'))
        self.assertContains(response, 'data-watch-together')
        self.assertContains(response, 'Watch Together')
