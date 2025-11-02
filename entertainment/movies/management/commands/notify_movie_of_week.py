"""
Management command to manually trigger Movie of the Week notifications.
Usage: python manage.py notify_movie_of_week
"""
from django.core.management.base import BaseCommand
from movies.tasks import notify_active_movie_of_week


class Command(BaseCommand):
    help = 'Send Movie of the Week reminder notifications to users who haven\'t watched it'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Checking for active Movie of the Week...'))
        
        result = notify_active_movie_of_week()
        
        if result.get('reason') == 'no_active_movie':
            self.stdout.write(self.style.WARNING('⚠ No active Movie of the Week found'))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Sent {result.get("notified_users", 0)} notifications for "{result.get("movie_title", "Unknown")}"'
                )
            )
