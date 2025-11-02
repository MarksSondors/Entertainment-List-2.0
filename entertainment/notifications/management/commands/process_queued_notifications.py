"""
Management command to process queued notifications.
Usage: python manage.py process_queued_notifications
"""
from django.core.management.base import BaseCommand
from notifications.utils import process_queued_notifications


class Command(BaseCommand):
    help = 'Process all queued notifications for users not in quiet hours'
    
    def handle(self, *args, **options):
        self.stdout.write('Processing queued notifications...')
        
        results = process_queued_notifications()
        
        self.stdout.write(self.style.SUCCESS(
            f'\nProcessing complete:'
            f'\n  ✅ Sent: {results["sent"]}'
            f'\n  ❌ Failed: {results["failed"]}'
            f'\n  ⏰ Still queued: {results["still_queued"]}'
        ))
