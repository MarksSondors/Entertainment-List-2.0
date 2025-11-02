"""
Management command to manually trigger notification cleanup.
Usage: python manage.py cleanup_notifications
"""
from django.core.management.base import BaseCommand
from notifications.tasks import cleanup_all_notifications


class Command(BaseCommand):
    help = 'Clean up old/inactive notifications, subscriptions, and logs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--subscriptions-only',
            action='store_true',
            help='Only clean up inactive subscriptions',
        )
        parser.add_argument(
            '--queued-only',
            action='store_true',
            help='Only clean up old queued notifications',
        )
        parser.add_argument(
            '--logs-only',
            action='store_true',
            help='Only clean up old notification logs',
        )

    def handle(self, *args, **options):
        from notifications.tasks import (
            cleanup_inactive_subscriptions,
            cleanup_old_queued_notifications,
            cleanup_old_notification_logs,
        )
        
        self.stdout.write(self.style.SUCCESS('Starting notification cleanup...'))
        
        if options['subscriptions_only']:
            result = cleanup_inactive_subscriptions()
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Deleted {result["deleted_count"]} inactive subscriptions'
                )
            )
        elif options['queued_only']:
            result = cleanup_old_queued_notifications()
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Deleted {result["deleted_count"]} old queued notifications'
                )
            )
        elif options['logs_only']:
            result = cleanup_old_notification_logs()
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Deleted {result["deleted_count"]} old notification logs'
                )
            )
        else:
            # Run all cleanup tasks
            results = cleanup_all_notifications()
            
            total = sum(r['deleted_count'] for r in results.values())
            
            self.stdout.write(self.style.SUCCESS('\nCleanup Summary:'))
            self.stdout.write(f'  Inactive subscriptions: {results["inactive_subscriptions"]["deleted_count"]}')
            self.stdout.write(f'  Old queued notifications: {results["old_queued_notifications"]["deleted_count"]}')
            self.stdout.write(f'  Old notification logs: {results["old_notification_logs"]["deleted_count"]}')
            self.stdout.write(self.style.SUCCESS(f'\n✓ Total items deleted: {total}'))
