"""Regression tests for the movie metadata refresh path.

Covers the bug where ``update_single_movie`` refreshed every other TMDB field but
never wrote ``runtime``, leaving movies stuck on a stale duration (usually ``0h 0m``)
even after TMDB published a real value.
"""
from unittest.mock import patch

from django.test import TestCase

from movies.models import Movie
from movies.tasks import update_single_movie


def _tmdb_payload(**overrides):
    """Minimal TMDB payload that passes through update_single_movie unchanged."""
    data = {
        'status': 'Released',
        'overview': 'Overview',
        'vote_average': 7.5,
        'runtime': 120,
        'title': 'Test Movie',
        'original_title': 'Test Movie',
        'release_date': '2020-01-01',
    }
    data.update(overrides)
    return data


class UpdateSingleMovieRuntimeTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            title='M3GAN 2.0',
            original_title='M3GAN 2.0',
            description='Overview',
            tmdb_id=1071585,
            runtime=0,
            rating=7.5,
            status='Released',
        )

    def _run_update(self, payload):
        with patch('movies.tasks.MoviesService') as service_cls:
            service_cls.return_value.get_movie_details.return_value = payload
            update_single_movie(self.movie.id)
        self.movie.refresh_from_db()

    def test_changed_runtime_is_written(self):
        """A runtime that differs from the stored value is persisted."""
        self._run_update(_tmdb_payload(runtime=120))
        self.assertEqual(self.movie.runtime, 120)

    def test_unchanged_runtime_is_left_alone(self):
        """A matching runtime is a no-op (no pointless write)."""
        self.movie.runtime = 99
        self.movie.save(update_fields=['runtime'])

        self._run_update(_tmdb_payload(runtime=99))
        self.assertEqual(self.movie.runtime, 99)

    def test_missing_runtime_does_not_overwrite(self):
        """A None payload must not wipe a runtime we already have."""
        self.movie.runtime = 105
        self.movie.save(update_fields=['runtime'])

        self._run_update(_tmdb_payload(runtime=None))
        self.assertEqual(self.movie.runtime, 105)

    def test_zero_runtime_does_not_overwrite(self):
        """TMDB returns 0 for unannounced titles; that must not clobber a real value."""
        self.movie.runtime = 105
        self.movie.save(update_fields=['runtime'])

        self._run_update(_tmdb_payload(runtime=0))
        self.assertEqual(self.movie.runtime, 105)

    def test_zero_runtime_is_replaced_once_tmdb_knows(self):
        """The reported production case: stored 0, TMDB now returns the real length."""
        self.assertEqual(self.movie.runtime, 0)
        self.assertEqual(self.movie.minutes_to_hours(), '0h 0m')

        self._run_update(_tmdb_payload(runtime=90))

        self.assertEqual(self.movie.runtime, 90)
        self.assertEqual(self.movie.minutes_to_hours(), '1h 30m')
