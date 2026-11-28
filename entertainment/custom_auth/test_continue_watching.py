import datetime

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from custom_auth.models import Watchlist
from tvshows.models import Episode, Season, TVShow, WatchedEpisode

User = get_user_model()
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
AIRED = datetime.date(2020, 1, 1)


def make_show(tmdb_id, seasons=2, per_season=3, runtime=30, specials=0):
    show = TVShow.objects.create(title=f'Show {tmdb_id}', original_title=f'Show {tmdb_id}', tmdb_id=tmdb_id)
    for s in ([0] if specials else []) + list(range(1, seasons + 1)):
        season = Season.objects.create(show=show, season_number=s)
        for e in range(1, (specials if s == 0 else per_season) + 1):
            Episode.objects.create(season=season, episode_number=e, title=f'S{s}E{e}', air_date=AIRED, runtime=runtime)
    return show


def ep(show, s, e):
    return Episode.objects.get(season__show=show, season__season_number=s, episode_number=e)


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class ContinueWatchingApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cw', email='cw@example.com', password='pw')
        self.client.force_login(self.user)
        self.tv_ct = ContentType.objects.get_for_model(TVShow)

    def watchlist(self, show):
        Watchlist.objects.create(user=self.user, content_type=self.tv_ct, object_id=show.id)

    def watch(self, show, s, e, days_ago=0):
        w = WatchedEpisode.objects.create(user=self.user, episode=ep(show, s, e))
        WatchedEpisode.objects.filter(pk=w.pk).update(watched_date=timezone.now() - datetime.timedelta(days=days_ago))

    def get(self):
        r = self.client.get(reverse('api_watchlist'), {'continue_watching': '1'})
        self.assertEqual(r.status_code, 200)
        return r.json()['continue_watching']

    def test_next_episode_and_time_left(self):
        show = make_show(1, seasons=2, per_season=3, runtime=25)
        self.watchlist(show)
        for e in (1, 2, 3):
            self.watch(show, 1, e)
        [row] = self.get()
        self.assertEqual(row['next_episode']['season'], 2)
        self.assertEqual(row['next_episode']['episode'], 1)
        self.assertEqual(row['next_episode']['title'], 'S2E1')
        self.assertEqual(row['next_episode']['runtime'], 25)
        self.assertEqual(row['next_episode']['id'], ep(show, 2, 1).id)
        self.assertEqual(row['episodes_left'], 3)
        self.assertEqual(row['minutes_left'], 75)
        self.assertAlmostEqual(row['progress'], 50.0)

    def test_next_is_after_the_furthest_watched_episode(self):
        show = make_show(2, seasons=2, per_season=3)
        self.watchlist(show)
        self.watch(show, 1, 1)
        self.watch(show, 2, 1)  # skipped ahead
        [row] = self.get()
        self.assertEqual((row['next_episode']['season'], row['next_episode']['episode']), (2, 2))
        self.assertEqual(row['episodes_left'], 4)

    def test_later_season_watched_first_falls_back_to_first_unwatched(self):
        show = make_show(3, seasons=2, per_season=2)
        self.watchlist(show)
        self.watch(show, 2, 1)
        self.watch(show, 2, 2)
        [row] = self.get()
        self.assertEqual((row['next_episode']['season'], row['next_episode']['episode']), (1, 1))

    def test_specials_and_unaired_episodes_are_ignored(self):
        show = make_show(4, seasons=1, per_season=2, specials=2)
        Episode.objects.create(season=show.seasons.get(season_number=1), episode_number=3, title='future',
                               air_date=timezone.now().date() + datetime.timedelta(days=30), runtime=30)
        self.watchlist(show)
        self.watch(show, 1, 1)
        [row] = self.get()
        self.assertEqual((row['next_episode']['season'], row['next_episode']['episode']), (1, 2))
        self.assertEqual(row['episodes_left'], 1)

    def test_sorted_by_last_watched_not_progress(self):
        old = make_show(5, seasons=1, per_season=4)   # 75% done, watched long ago
        new = make_show(6, seasons=1, per_season=4)   # 25% done, watched today
        self.watchlist(old)
        self.watchlist(new)
        for e in (1, 2, 3):
            self.watch(old, 1, e, days_ago=400)
        self.watch(new, 1, 1, days_ago=0)
        rows = self.get()
        self.assertEqual([r['item']['media_tmdb_id'] for r in rows], [6, 5])
        self.assertGreater(rows[0]['last_watched'], rows[1]['last_watched'])

    def test_finished_and_unstarted_shows_are_left_out(self):
        done = make_show(7, seasons=1, per_season=1)
        fresh = make_show(8, seasons=1, per_season=2)
        self.watchlist(done)
        self.watchlist(fresh)
        self.watch(done, 1, 1)
        self.assertEqual(self.get(), [])
