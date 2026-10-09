from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tvshows.models import Episode, EpisodeGroup, EpisodeSubGroup, Season, TVShow, WatchedEpisode

User = get_user_model()

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def make_show(tmdb_id=100, seasons=2, episodes_per_season=5):
    show = TVShow.objects.create(title=f'Show {tmdb_id}', original_title=f'Show {tmdb_id}', tmdb_id=tmdb_id)
    for s in range(1, seasons + 1):
        season = Season.objects.create(show=show, season_number=s)
        Episode.objects.bulk_create([
            Episode(season=season, episode_number=e, title=f'S{s}E{e}')
            for e in range(1, episodes_per_season + 1)
        ])
    return show


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class EpisodeWatchedViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='watcher', email='watcher@example.com', password='pw')
        self.client.force_login(self.user)
        self.show = make_show()
        self.season1 = self.show.seasons.get(season_number=1)
        self.ep3 = self.season1.episodes.get(episode_number=3)

    def _toggle(self, episode, **data):
        payload = {'watched': True, 'season_number': episode.season.season_number,
                   'episode_number': episode.episode_number, **data}
        return self.client.post(
            reverse('episode_watched', args=[episode.id]), payload, content_type='application/json'
        )

    def test_mark_previous_marks_earlier_episodes_and_reports_progress(self):
        WatchedEpisode.objects.create(user=self.user, episode=self.season1.episodes.get(episode_number=1))

        data = self._toggle(self.ep3, mark_previous=True).json()

        ep2 = self.season1.episodes.get(episode_number=2)
        self.assertEqual(data['marked_episodes'], [ep2.id])  # ep1 was already watched
        self.assertEqual(
            set(WatchedEpisode.objects.filter(user=self.user).values_list('episode__episode_number', flat=True)),
            {1, 2, 3},
        )
        self.assertEqual(data['season_progress'][str(self.season1.id)], {'total': 5, 'watched': 3, 'percentage': 60.0})
        self.assertEqual(data['show_progress'], {'total': 10, 'watched': 3, 'percentage': 30.0})

    def test_unwatch_removes_episode(self):
        WatchedEpisode.objects.create(user=self.user, episode=self.ep3)
        data = self._toggle(self.ep3, watched=False).json()
        self.assertFalse(WatchedEpisode.objects.filter(user=self.user, episode=self.ep3).exists())
        self.assertEqual(data['show_progress']['watched'], 0)

    def test_subgroup_progress_counts_whole_subgroup(self):
        group = EpisodeGroup.objects.create(name='Arcs', show=self.show)
        arc = EpisodeSubGroup.objects.create(name='Arc 1', parent_group=group)
        arc.episodes.set(self.season1.episodes.filter(episode_number__lte=4))
        WatchedEpisode.objects.create(user=self.user, episode=self.season1.episodes.get(episode_number=1))
        # Another user's watch must not count
        other = User.objects.create_user(username='other', email='other@example.com', password='pw')
        WatchedEpisode.objects.create(user=other, episode=self.season1.episodes.get(episode_number=2))

        data = self._toggle(self.ep3).json()

        self.assertEqual(data['subgroup_progress'][str(arc.id)], {'total': 4, 'watched': 2, 'percentage': 50.0})

    def test_query_count_does_not_grow_with_seasons(self):
        # warm-up (session, content types); both measured calls then mark an unwatched
        # episode plus its unwatched predecessors, so they take the same code path
        self._toggle(self.season1.episodes.get(episode_number=5))
        with CaptureQueriesContext(connection) as small:
            self._toggle(self.ep3, mark_previous=True)

        big_show = make_show(tmdb_id=200, seasons=12, episodes_per_season=10)
        big_ep = big_show.seasons.get(season_number=1).episodes.get(episode_number=8)
        with CaptureQueriesContext(connection) as big:
            self._toggle(big_ep, mark_previous=True)

        self.assertEqual(len(big), len(small))


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class TVShowImportPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='importer', password='pw')
        self.client.force_login(self.user)
        self.url = reverse('tv_show_page', args=[555])

    def tearDown(self):
        cache.clear()

    @mock.patch('tvshows.views.create_tvshow_async', return_value='task-abc')
    def test_missing_show_queues_one_import_and_renders_importing_page(self, create_async):
        first = self.client.get(self.url)
        second = self.client.get(self.url)

        self.assertEqual(first.status_code, 200)
        self.assertTemplateUsed(first, 'tv_show_importing.html')
        self.assertContains(first, 'http-equiv="refresh"')
        self.assertTemplateUsed(second, 'tv_show_importing.html')
        create_async.assert_called_once_with(555, user_id=self.user.id, add_to_watchlist=False)

    @mock.patch('tvshows.views.create_tvshow_async', return_value='task-failed')
    def test_finished_import_without_show_returns_404(self, _create_async):
        from django_q.models import Task

        self.client.get(self.url)
        Task.objects.create(id='task-failed', name='t', func='f', started='2026-01-01T00:00Z',
                            stopped='2026-01-01T00:00Z', success=False)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, 'tmdb_404.html')
        self.assertIsNone(cache.get('tvshow_import:555'))

    def test_task_status_reports_queued_task_as_pending(self):
        # Django Q has no Task row until the task finishes; polling must not 404 meanwhile
        response = self.client.get(reverse('task-status'), {'task_id': 'still-queued'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'complete': False, 'success': None})

    @mock.patch('tvshows.views.create_tvshow_async', return_value='task-ok')
    def test_finished_import_redirects_to_show(self, _create_async):
        from django_q.models import Task

        self.client.get(self.url)
        make_show(tmdb_id=555, seasons=1, episodes_per_season=1)
        Task.objects.create(id='task-ok', name='t', func='f', started='2026-01-01T00:00Z',
                            stopped='2026-01-01T00:00Z', success=True)

        # Show exists now, so the page renders directly
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'tv_show_page.html')
