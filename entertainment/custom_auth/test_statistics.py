"""Regression tests for cross-media statistics and period boundaries."""
from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from books.models import Book
from custom_auth.models import CustomUser, MediaPerson, Person, Review, WatchlistEvent
from custom_auth.services import statistics as stats
from movies.models import Movie
from music.models import Album
from tvshows.models import Episode, Season, TVShow, WatchedEpisode


class StatisticsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(username='stats', email='stats@example.com')
        self.movie = Movie.objects.create(
            title='Film', original_title='Film', tmdb_id=9001, runtime=120, rating=8,
        )

    def review(self, obj, rating=8, year=2025):
        review = Review.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(obj),
            object_id=obj.pk, rating=rating,
        )
        Review.objects.filter(pk=review.pk).update(
            date_added=datetime(year, 6, 10, 12, tzinfo=dt_timezone.utc),
        )
        return review

    def test_books_and_music_are_included(self):
        author = Person.objects.create(name='Author')
        book = Book.objects.create(title='Book', original_title='Book', hardcover_id=91001)
        book.authors.add(author)
        album = Album.objects.create(
            title='Album', original_title='Album', primary_artist=author, runtime=45,
        )
        album.featured_artists.add(author)
        self.review(book, 9)
        self.review(album, 7)
        result = stats.get_all_stats(self.user)
        self.assertEqual(set(result), {'movies', 'tvshows', 'games', 'books', 'music'})
        self.assertEqual(result['books']['summary']['total_rated'], 1)
        self.assertEqual(result['books']['top_creators'][0]['person_id'], author.pk)
        self.assertEqual(result['music']['summary']['total_hours'], 0.8)
        self.assertEqual(result['music']['top_creators'][0]['count'], 1)

    def test_people_use_ids_and_distinct_titles(self):
        ct = ContentType.objects.get_for_model(self.movie)
        first = Person.objects.create(name='Same Name')
        second = Person.objects.create(name='Same Name')
        for person, order in [(first, 0), (first, 1), (second, 2)]:
            MediaPerson.objects.create(
                content_type=ct, object_id=self.movie.pk, person=person,
                role='Actor', order=order,
            )
        self.review(self.movie)
        people = stats.get_all_stats(self.user)['movies']['top_actors']
        self.assertEqual(len(people), 2)
        self.assertEqual({p['person_id'] for p in people}, {first.pk, second.pk})
        self.assertEqual([p['count'] for p in people], [1, 1])

    def test_yearly_tv_time_uses_watch_dates_not_rating_dates(self):
        ctx = stats._build_review_context([{
            'object_id': 1, 'rating': 9,
            'date_added': datetime(2025, 6, 1, tzinfo=dt_timezone.utc),
        }])
        data = {
            'runtimes': {},
            'tv_watch_monthly': [
                {'month': datetime(2024, 6, 1, tzinfo=dt_timezone.utc), 'total_mins': 120},
                {'month': datetime(2025, 6, 1, tzinfo=dt_timezone.utc), 'total_mins': 60},
            ],
        }
        summary = stats._compute_summary(ctx, data, TVShow, True, year=2025)
        self.assertEqual(summary['total_hours'], 1)
        self.assertEqual(stats._compute_summary(ctx, data, TVShow, True)['total_hours'], 3)
        empty = stats._build_review_context([])
        self.assertEqual(stats._compute_summary(empty, data, TVShow, True)['total_hours'], 3)

    def test_review_counts_and_title_weighting_are_distinct(self):
        ctx = stats._build_review_context([
            {'object_id': oid, 'rating': rating, 'date_added': datetime(2025, 1, day, tzinfo=dt_timezone.utc)}
            for oid, rating, day in [(1, 10, 1), (1, 6, 2), (2, 4, 2)]
        ])
        summary = stats._compute_summary(ctx, {'tv_watch_monthly': [], 'runtimes': {}}, TVShow, True)
        self.assertEqual(summary['total_rated'], 2)
        self.assertEqual(summary['review_count'], 3)
        self.assertEqual(summary['avg_rating'], 6)
        self.assertEqual(summary['median_rating'], 6)
        self.assertEqual(summary['active_days'], 2)
        self.assertEqual(stats._compute_activity(ctx), [{'month': '2025-01', 'reviews': 3, 'titles': 2}])

    def test_unrated_tv_watches_are_included_in_year_slices(self):
        show = TVShow.objects.create(title='Show', original_title='Show', tmdb_id=98001)
        season = Season.objects.create(show=show, season_number=1)
        for year, runtime, number in [(2024, 90, 1), (2025, 30, 2)]:
            episode = Episode.objects.create(
                season=season, title=f'Episode {number}', episode_number=number, runtime=runtime,
            )
            watched = WatchedEpisode.objects.create(user=self.user, episode=episode)
            WatchedEpisode.objects.filter(pk=watched.pk).update(
                watched_date=datetime(year, 6, 1, 12, tzinfo=dt_timezone.utc),
            )
        result = stats.get_all_stats(self.user)['tvshows']
        self.assertEqual(result['years'], [2025, 2024])
        self.assertEqual(result['summary']['total_rated'], 0)
        self.assertEqual(result['summary']['total_hours'], 2)
        self.assertEqual(result['yearly']['2024']['summary']['total_hours'], 1.5)
        self.assertEqual(result['yearly']['2025']['summary']['total_hours'], 0.5)

    def test_reviews_use_the_current_timezone_at_year_boundaries(self):
        review = self.review(self.movie)
        Review.objects.filter(pk=review.pk).update(
            date_added=datetime(2024, 12, 31, 23, 30, tzinfo=dt_timezone.utc),
        )
        from django.utils import timezone
        with timezone.override('Europe/Riga'):
            rows = stats._fetch_all_reviews(self.user, ContentType.objects.get_for_model(self.movie))
        self.assertEqual(rows[0]['date_added'].year, 2025)

    def test_watchlist_only_years_and_opening_balance(self):
        ct = ContentType.objects.get_for_model(self.movie)
        for year, action in [(2023, 'add'), (2023, 'add'), (2024, 'remove')]:
            event = WatchlistEvent.objects.create(
                user=self.user, content_type=ct, object_id=self.movie.pk, action=action,
            )
            WatchlistEvent.objects.filter(pk=event.pk).update(
                date=datetime(year, 6, 1, 12, tzinfo=dt_timezone.utc),
            )
        result = stats.get_all_stats(self.user)['movies']
        self.assertEqual(result['years'], [2024, 2023])
        year = result['yearly']['2024']
        self.assertEqual(year['watchlist_opening_balance'], 2)
        self.assertEqual(year['watchlist_by_month'][0]['balance'], 1)
        self.assertEqual(year['summary']['total_rated'], 0)

    def test_review_updates_invalidate_cache(self):
        review = self.review(self.movie)
        self.assertEqual(stats.get_all_stats(self.user)['movies']['summary']['avg_rating'], 8)
        review.rating = 5
        review.save()
        self.assertEqual(stats.get_all_stats(self.user)['movies']['summary']['avg_rating'], 5)

    def test_watch_history_signals_invalidate_cache(self):
        from django.db.models.signals import post_delete, post_save

        for signal in (post_save, post_delete):
            cache.set(stats._stats_cache_key(self.user.pk), {'stale': True})
            signal.send(
                sender=WatchedEpisode, instance=WatchedEpisode(user=self.user),
                created=True,
            )
            self.assertIsNone(cache.get(stats._stats_cache_key(self.user.pk)))

    def test_deleted_media_reviews_do_not_inflate_totals(self):
        self.review(self.movie)
        self.movie.delete()
        self.assertEqual(stats.get_all_stats(self.user)['movies']['summary']['total_rated'], 0)

    def test_page_uses_safe_json_and_has_all_media_tabs(self):
        person = Person.objects.create(name='</script><script>alert(1)</script>')
        MediaPerson.objects.create(
            content_type=ContentType.objects.get_for_model(self.movie),
            object_id=self.movie.pk, person=person, role='Director',
        )
        self.review(self.movie)
        self.client.force_login(self.user)
        response = self.client.get(reverse('statistics_page'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-media="books"')
        self.assertContains(response, 'data-media="music"')
        self.assertNotContains(response, '</script><script>alert(1)</script>')
        self.assertContains(response, 'id="statistics-data" type="application/json"')
