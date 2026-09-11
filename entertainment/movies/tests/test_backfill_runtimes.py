"""Tests for the runtime backfill repair command."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from movies.models import Movie


class BackfillMovieRuntimesTests(TestCase):
    def setUp(self):
        self.zero = Movie.objects.create(
            title='M3GAN 2.0', original_title='M3GAN 2.0', tmdb_id=1071585,
            runtime=0, rating=7.5, status='Released',
        )
        self.good = Movie.objects.create(
            title='Alien', original_title='Alien', tmdb_id=348,
            runtime=117, rating=8.4, status='Released',
        )
        self.unknown = Movie.objects.create(
            title='Coco 2', original_title='Coco 2', tmdb_id=1451567,
            runtime=0, rating=0.0, status='In Production',
        )

    def _run(self, dry_run=False):
        runtimes = {1071585: {'runtime': 120}, 348: {'runtime': 117}, 1451567: {'runtime': 0}}
        with patch('movies.management.commands.backfill_movie_runtimes.MoviesService') as cls:
            cls.return_value.get_movie_details.side_effect = lambda tid: runtimes[tid]
            out = StringIO()
            call_command('backfill_movie_runtimes', dry_run=dry_run, stdout=out)
        return out.getvalue()

    def test_fills_zero_runtime(self):
        self._run()
        self.zero.refresh_from_db()
        self.assertEqual(self.zero.runtime, 120)
        self.assertEqual(self.zero.minutes_to_hours(), '2h 0m')

    def test_leaves_good_runtime_alone(self):
        """A movie with a real runtime is not even a candidate, so it is untouched."""
        self._run()
        self.good.refresh_from_db()
        self.assertEqual(self.good.runtime, 117)

    def test_does_not_write_zero_from_tmdb(self):
        """TMDB returning 0 leaves the row as-is instead of confirming a bogus value."""
        self._run()
        self.unknown.refresh_from_db()
        self.assertEqual(self.unknown.runtime, 0)
        self.assertEqual(self.unknown.minutes_to_hours(), '0h 0m')

    def test_dry_run_writes_nothing(self):
        out = self._run(dry_run=True)
        self.zero.refresh_from_db()
        self.assertEqual(self.zero.runtime, 0)
        self.assertIn('Would update 1 movie(s)', out)

    def test_idempotent_on_second_run(self):
        self._run()
        out = self._run()
        self.zero.refresh_from_db()
        self.assertEqual(self.zero.runtime, 120)
        self.assertIn('Updated 0 movie(s)', out)
