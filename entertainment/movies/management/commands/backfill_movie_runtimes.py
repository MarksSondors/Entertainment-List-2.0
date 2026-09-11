"""One-off repair for movies whose runtime was never refreshed from TMDB.

Background: ``update_single_movie`` did not compare or write ``runtime``, so titles
imported before TMDB published a length kept a stored value of 0 and rendered as
"0h 0m" indefinitely. The refresh path is fixed, but existing rows only self-correct
as the rotation job reaches them. This command repairs them in one pass.

Safe to re-run: it only touches rows whose stored runtime is falsy and only writes a
truthy runtime from TMDB, so it can never clobber a good value or wipe one.
"""
from django.core.management.base import BaseCommand

from api.services.movies import MoviesService
from movies.models import Movie


class Command(BaseCommand):
    help = 'Backfill runtime for movies stored with a missing/zero runtime.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing to the database.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        candidates = (
            Movie.objects.filter(runtime__isnull=True)
            | Movie.objects.filter(runtime=0)
        ).order_by('title')

        total = candidates.count()
        self.stdout.write(f'Movies with a missing/zero runtime: {total}')
        if not total:
            self.stdout.write(self.style.SUCCESS('Nothing to backfill.'))
            return

        service = MoviesService()
        updated = []
        still_unknown = 0
        failed = []

        for movie in candidates:
            try:
                data = service.get_movie_details(movie.tmdb_id)
            except Exception as exc:  # network/API failures must not abort the run
                failed.append((movie.title, str(exc)))
                continue

            tmdb_runtime = (data or {}).get('runtime')
            if not tmdb_runtime:
                # TMDB genuinely has no length yet (unannounced title) - leave it.
                still_unknown += 1
                continue

            updated.append((movie.title, movie.runtime, tmdb_runtime))
            if not dry_run:
                movie.runtime = tmdb_runtime
                movie.save(update_fields=['runtime', 'date_updated'])

        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(f'\n{verb} {len(updated)} movie(s):'))
        for title, old, new in updated:
            self.stdout.write(f'  {title}: {old} min -> {new} min')

        self.stdout.write(
            f'\nStill unknown at TMDB (left untouched): {still_unknown}'
        )
        if failed:
            self.stdout.write(self.style.WARNING(f'Failed lookups: {len(failed)}'))
            for title, err in failed:
                self.stdout.write(f'  {title}: {err}')
