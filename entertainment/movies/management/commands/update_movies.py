from django.core.management.base import BaseCommand
from movies.tasks import update_unreleased_movies, update_random_movies, update_movie_collections, setup_scheduled_tasks

class Command(BaseCommand):
    help = 'Updates information for movies from TMDB'

    def add_arguments(self, parser):
        parser.add_argument(
            '--setup',
            action='store_true',
            help='Set up scheduled tasks',
        )
        parser.add_argument(
            '--random',
            action='store_true',
            help='Update 10 random movies',
        )
        parser.add_argument(
            '--collections',
            action='store_true',
            help='Update movie collections',
        )

    def handle(self, *args, **options):
        if options['setup']:
            result = setup_scheduled_tasks()
            self.stdout.write(self.style.SUCCESS(result))
        elif options['random']:
            result = update_random_movies()
            self.stdout.write(self.style.SUCCESS(f"Random update process complete: {result}"))
        elif options['collections']:
            result = update_movie_collections()
            self.stdout.write(self.style.SUCCESS(f"Collections update process complete: {result}"))
        else:
            result = update_unreleased_movies()
            self.stdout.write(self.style.SUCCESS(f"Update process complete: {result}"))