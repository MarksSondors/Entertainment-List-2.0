from django.core.management.base import BaseCommand
from tvshows.tasks import update_ongoing_tvshows, update_random_tvshows, update_episode_groups, setup_scheduled_tasks

class Command(BaseCommand):
    help = 'Updates information for TV shows from TMDB'

    def add_arguments(self, parser):
        parser.add_argument(
            '--setup',
            action='store_true',
            help='Set up scheduled tasks',
        )
        parser.add_argument(
            '--random',
            action='store_true',
            help='Update 10 random TV shows',
        )
        parser.add_argument(
            '--groups',
            action='store_true',
            help='Update episode groups for all TV shows',
        )
        parser.add_argument(
            '--id',
            type=int,
            help='Update a specific TV show by ID',
        )
        parser.add_argument(
            '--tmdb',
            type=int,
            help='Update a specific TV show by TMDB ID',
        )

    def handle(self, *args, **options):
        if options['setup']:
            result = setup_scheduled_tasks()
            self.stdout.write(self.style.SUCCESS(result))
        elif options['random']:
            result = update_random_tvshows()
            self.stdout.write(self.style.SUCCESS(f"Random update process complete: {result}"))
        elif options['groups']:
            from tvshows.models import TVShow
            count = 0
            for tvshow in TVShow.objects.all():
                try:
                    update_episode_groups(tvshow.id)
                    count += 1
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error updating groups for {tvshow.title}: {e}"))
            self.stdout.write(self.style.SUCCESS(f"Episode groups update process complete for {count} TV shows"))
        elif options['id']:
            from tvshows.tasks import update_single_tvshow
            result = update_single_tvshow(options['id'])
            self.stdout.write(self.style.SUCCESS(f"Update process complete: {result}"))
        elif options['tmdb']:
            from tvshows.models import TVShow
            from tvshows.tasks import update_single_tvshow
            try:
                tvshow = TVShow.objects.get(tmdb_id=options['tmdb'])
                result = update_single_tvshow(tvshow.id)
                self.stdout.write(self.style.SUCCESS(f"Update process complete: {result}"))
            except TVShow.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"TV show with TMDB ID {options['tmdb']} not found"))
        else:
            result = update_ongoing_tvshows()
            self.stdout.write(self.style.SUCCESS(f"Update process complete: {result}"))