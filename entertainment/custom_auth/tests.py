from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from custom_auth.models import Review
from custom_auth.tasks import process_review_notification
from notifications.models import NotificationPreference
from datetime import date

from tvshows.models import Episode, Season, TVShow, WatchedEpisode


# CI has no Redis service; keep the cache in-process.
@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ReviewNotificationTitleTests(TestCase):
    """Titles must fit NotificationLog/QueuedNotification.title (max_length=100)."""

    def setUp(self):
        User = get_user_model()
        self.reviewer = User.objects.create_user(username='reviewer', email='reviewer@example.test', password='x')
        self.listener = User.objects.create_user(username='listener', email='listener@example.test', password='x')
        prefs, _ = NotificationPreference.objects.get_or_create(user=self.listener)
        prefs.new_reviews = True
        prefs.save()
        self.show = TVShow.objects.create(
            title='I Think You Should Leave with Tim Robinson',
            original_title='I Think You Should Leave with Tim Robinson',
            tmdb_id=90001, imdb_id='tt9000001',
        )
        self.season = Season.objects.create(show=self.show, season_number=1)
        episode = Episode.objects.create(
            season=self.season, episode_number=1, title='Pilot', air_date=date(2019, 4, 23),
        )
        # Season reviews require the reviewer to have watched every episode.
        WatchedEpisode.objects.create(user=self.reviewer, episode=episode)

    def _review(self, **kwargs):
        return Review.objects.create(
            user=self.reviewer,
            content_type=ContentType.objects.get_for_model(TVShow),
            object_id=self.show.id,
            rating=8.0,
            **kwargs,
        )

    def _sent_titles(self, review):
        with patch('notifications.utils.send_notification_to_user') as send:
            send.return_value = {'success': True}
            process_review_notification(review.id)
        return [c.kwargs['title'] for c in send.call_args_list]

    def test_season_review_title_does_not_repeat_show_name(self):
        titles = self._sent_titles(self._review(season=self.season))
        self.assertTrue(titles, 'no notification was sent')
        for title in titles:
            self.assertEqual(title.count(self.show.title), 1, title)
            self.assertIn('Season 1', title)
            self.assertLessEqual(len(title), 100)

    def test_overlong_title_is_truncated_to_column_size(self):
        self.show.title = 'X' * 150
        self.show.save()
        titles = self._sent_titles(self._review(season=self.season))
        self.assertTrue(titles, 'no notification was sent')
        for title in titles:
            self.assertLessEqual(len(title), 100)


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class ReviewNotificationSignalTests(ReviewNotificationTitleTests):
    """Saving a review must enqueue the notification task (the receiver was once dropped)."""

    def test_new_review_enqueues_notification(self):
        with patch('django_q.tasks.async_task') as enqueue:
            review = self._review(season=self.season)
        calls = [c for c in enqueue.call_args_list
                 if c.args and c.args[0] == 'custom_auth.tasks.process_review_notification']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[1], review.id)

    def test_editing_a_review_does_not_notify_again(self):
        review = self._review(season=self.season)
        with patch('django_q.tasks.async_task') as enqueue:
            review.rating = 9.0
            review.save()
        self.assertFalse([c for c in enqueue.call_args_list
                          if c.args and c.args[0] == 'custom_auth.tasks.process_review_notification'])
