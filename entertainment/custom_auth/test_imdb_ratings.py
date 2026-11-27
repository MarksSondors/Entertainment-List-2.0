"""IMDb rating: daily sync from IMDb's dataset; TMDB + IMDb badges on movie/TV pages; count filters."""

import datetime
import gzip
import io
from decimal import Decimal
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from custom_auth.models import Person
from custom_auth.services.imdb_ratings import sync_imdb_ratings
from custom_auth.templatetags.rating_extras import compact_count, thousands
from movies.models import Movie
from tvshows.models import TVShow

User = get_user_model()
LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
HEADER = 'tconst\taverageRating\tnumVotes\n'


def ratings_file(rows, header=HEADER):
    """A gzipped TSV in IMDb's title.ratings format, as a file object."""
    body = header + ''.join(f'{tconst}\t{rating}\t{votes}\n' for tconst, rating, votes in rows)
    return io.BytesIO(gzip.compress(body.encode()))


def make_movie(tmdb_id, imdb_id, **kwargs):
    return Movie.objects.create(title=f'M{tmdb_id}', original_title=f'M{tmdb_id}', tmdb_id=tmdb_id,
                                imdb_id=imdb_id, runtime=100, rating=7.0, **kwargs)


class CountFilterTests(SimpleTestCase):
    def test_compact_count(self):
        cases = {812: '812', 1000: '1K', 1234: '1.2K', 9960: '10K', 45321: '45K', 999_999: '1M',
                 1_000_000: '1M', 2_134_567: '2.1M', 12_600_000: '13M', None: '', 'x': ''}
        for value, expected in cases.items():
            self.assertEqual(compact_count(value), expected, value)

    def test_thousands(self):
        self.assertEqual(thousands(2134567), '2,134,567')
        self.assertEqual(thousands(None), '')


@override_settings(CACHES=LOCMEM_CACHE)
class SyncImdbRatingsTests(TestCase):
    def setUp(self):
        self.movie = make_movie(1, 'tt0000001')
        self.kept = make_movie(2, 'tt0000002', imdb_rating=Decimal('6.0'), imdb_votes=10)
        self.no_id = make_movie(3, None)
        self.show = TVShow.objects.create(title='S', original_title='S', tmdb_id=10, imdb_id='tt0000010')
        self.rows = [('tt0000001', '8.4', 2134567), ('tt0000010', '7.9', 45321), ('tt9999999', '5.0', 7)]

    def test_stores_rating_and_votes_for_matching_titles_only(self):
        before = Movie.objects.get(pk=self.movie.pk).date_updated
        result = sync_imdb_ratings(ratings_file(self.rows), min_rows=1)

        self.movie.refresh_from_db()
        self.show.refresh_from_db()
        self.assertEqual((self.movie.imdb_rating, self.movie.imdb_votes), (Decimal('8.4'), 2134567))
        self.assertEqual((self.show.imdb_rating, self.show.imdb_votes), (Decimal('7.9'), 45321))
        self.assertIsNotNone(self.movie.imdb_rating_updated)
        # bulk_update leaves date_updated alone, so the TMDB refresh rotation is unaffected
        self.assertEqual(self.movie.date_updated, before)
        self.assertEqual((result['updated_movie'], result['updated_tvshow']), (1, 1))

    def test_title_missing_from_file_keeps_its_rating(self):
        sync_imdb_ratings(ratings_file(self.rows), min_rows=1)
        self.kept.refresh_from_db()
        self.assertEqual((self.kept.imdb_rating, self.kept.imdb_votes), (Decimal('6.0'), 10))

    def test_unchanged_values_are_not_rewritten(self):
        sync_imdb_ratings(ratings_file(self.rows), min_rows=1)
        stamp = Movie.objects.get(pk=self.movie.pk).imdb_rating_updated
        result = sync_imdb_ratings(ratings_file(self.rows), min_rows=1)
        self.assertNotIn('updated_movie', result)
        self.assertEqual(Movie.objects.get(pk=self.movie.pk).imdb_rating_updated, stamp)

    def test_short_truncated_or_wrong_files_write_nothing(self):
        full = ratings_file(self.rows).getvalue()
        bad_files = {
            'too few rows for the real floor': (ratings_file(self.rows), {}),
            'truncated gzip': (io.BytesIO(full[:len(full) // 2]), {'min_rows': 1}),
            'wrong header': (ratings_file(self.rows, header='id\tscore\tvotes\n'), {'min_rows': 1}),
            'not gzip': (io.BytesIO(b'<html>blocked</html>'), {'min_rows': 1}),
        }
        for label, (fileobj, kwargs) in bad_files.items():
            result = sync_imdb_ratings(fileobj, **kwargs)
            self.assertIn('error', result, label)
        self.assertFalse(Movie.objects.filter(imdb_rating__isnull=False).exclude(pk=self.kept.pk).exists())
        self.assertFalse(TVShow.objects.filter(imdb_rating__isnull=False).exists())

    def test_rows_with_bad_values_are_skipped(self):
        rows = [('tt0000001', 'N/A', 5), ('tt0000010', '11.0', 5)]
        sync_imdb_ratings(ratings_file(rows), min_rows=1)
        self.assertIsNone(Movie.objects.get(pk=self.movie.pk).imdb_rating)
        self.assertIsNone(TVShow.objects.get(pk=self.show.pk).imdb_rating)

    @mock.patch('custom_auth.services.imdb_ratings.requests.get')
    def test_downloads_imdb_dataset(self, get):
        get.return_value.raw = ratings_file(self.rows)
        sync_imdb_ratings(min_rows=1)
        self.assertEqual(get.call_args.args[0], 'https://datasets.imdbws.com/title.ratings.tsv.gz')
        self.assertEqual(Movie.objects.get(pk=self.movie.pk).imdb_rating, Decimal('8.4'))
        get.return_value.close.assert_called_once()

    @mock.patch('custom_auth.services.imdb_ratings.requests.get', side_effect=requests.ConnectionError('down'))
    def test_download_failure_writes_nothing(self, _get):
        self.assertIn('error', sync_imdb_ratings(min_rows=1))
        self.assertEqual(Movie.objects.get(pk=self.kept.pk).imdb_rating, Decimal('6.0'))

    def test_daily_schedule_is_registered(self):
        from django_q.models import Schedule
        from custom_auth.apps import _setup_custom_auth_schedules

        _setup_custom_auth_schedules(sender=None)
        schedule = Schedule.objects.get(func='custom_auth.tasks.sync_imdb_ratings')
        self.assertEqual(schedule.schedule_type, Schedule.DAILY)


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class RatingBadgeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='badge', email='badge@example.com', password='pw')
        self.client.force_login(self.user)

    def movie_page(self, movie):
        return self.client.get(reverse('movie_page', args=[movie.tmdb_id])).content.decode()

    def test_movie_page_has_tmdb_and_imdb_badges(self):
        movie = make_movie(20, 'tt0111161', imdb_rating=Decimal('9.3'), imdb_votes=3012345,
                           release_date=datetime.date(1994, 9, 23), status='Released')
        Movie.objects.filter(pk=movie.pk).update(rating=8.734)
        html = self.movie_page(movie)
        self.assertIn('css/components/site-rating', html)
        self.assertIn('<a class="site-rating site-rating-tmdb" href="https://www.themoviedb.org/movie/20/" '
                      'target="_blank" rel="noopener noreferrer"', html)
        self.assertIn('<a class="site-rating site-rating-imdb" href="https://www.imdb.com/title/tt0111161/" '
                      'target="_blank" rel="noopener noreferrer"', html)
        self.assertIn('images/tmdb_logo', html)
        self.assertIn('images/imdb_logo', html)
        self.assertIn('<strong>8.7</strong>/10', html)
        self.assertIn('<strong>9.3</strong>/10', html)
        self.assertIn('3M votes', html)
        self.assertIn('TMDB rating 8.7 out of 10, opens TMDB', html)
        self.assertIn('IMDb rating 9.3 out of 10 from 3,012,345 votes, opens IMDb', html)
        self.assertIn('TMDB: 8.7 • IMDb: 9.3', html)  # status bar
        self.assertNotIn('TMDB Rating:', html)

    def test_unrated_titles_still_link_out(self):
        movie = make_movie(21, 'tt0000021', release_date=datetime.date(2030, 1, 1))
        Movie.objects.filter(pk=movie.pk).update(rating=0)
        html = self.movie_page(movie)
        self.assertIn('href="https://www.themoviedb.org/movie/21/"', html)
        self.assertIn('href="https://www.imdb.com/title/tt0000021/"', html)
        self.assertIn('View on TMDB', html)
        self.assertIn('View on IMDb', html)
        self.assertNotIn('IMDb: ', html)

    def test_no_imdb_badge_without_imdb_id(self):
        html = self.movie_page(make_movie(22, None, release_date=datetime.date(2001, 1, 1)))
        self.assertIn('site-rating-tmdb', html)
        self.assertNotIn('site-rating-imdb', html)

    def test_tv_show_page_has_both_badges(self):
        show = TVShow.objects.create(title='Show', original_title='Show', tmdb_id=30, imdb_id='tt0903747',
                                     imdb_rating=Decimal('9.5'), imdb_votes=2_400_000, rating=8.959,
                                     status='Ended')
        html = self.client.get(reverse('tv_show_page', args=[show.tmdb_id])).content.decode()
        self.assertIn('href="https://www.themoviedb.org/tv/30/"', html)
        self.assertIn('href="https://www.imdb.com/title/tt0903747/"', html)
        self.assertIn('<strong>9.0</strong>/10', html)
        self.assertIn('<strong>9.5</strong>/10', html)
        self.assertIn('2.4M votes', html)
        self.assertIn('TMDB: 9.0 • IMDb: 9.5', html)
        # user_progress_section.html turns every .rating-badge on this page into a review-modal
        # trigger (preventDefault), so the outbound links must never carry that class
        self.assertIn("querySelectorAll('.rating-badge')", html)
        self.assertNotRegex(html, r'<a class="[^"]*\brating-badge\b')

    def test_people_page_uses_local_imdb_logo(self):
        person = Person.objects.create(name='Someone', tmdb_id=40, imdb_id='nm0000151')
        html = self.client.get(reverse('person_detail', args=[person.id])).content.decode()
        self.assertIn('href="https://www.imdb.com/name/nm0000151/"', html)
        self.assertIn('images/imdb_logo', html)
        self.assertNotIn('IMDB_Logo_2016.svg', html)
