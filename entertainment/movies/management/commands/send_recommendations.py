"""
Management command to manually trigger bi-weekly recommendation notifications.
Usage: python manage.py send_recommendations
"""
from django.core.management.base import BaseCommand
from movies.tasks import send_biweekly_recommendations


class Command(BaseCommand):
    help = 'Send bi-weekly movie recommendations to users'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting bi-weekly recommendation notifications...'))
        
        result = send_biweekly_recommendations()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'✓ Sent {result.get("recommendations_sent", 0)} recommendations to {result.get("users_count", 0)} users'
            )
        )
