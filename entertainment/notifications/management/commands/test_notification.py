"""
Management command to send a test notification to a user.
Usage: python manage.py test_notification <user_id>
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from notifications.utils import send_notification_to_user

User = get_user_model()


class Command(BaseCommand):
    help = 'Send a test push notification to a user'

    def add_arguments(self, parser):
        parser.add_argument(
            'user_id',
            type=int,
            help='User ID to send notification to'
        )
        parser.add_argument(
            '--title',
            type=str,
            default='Test Notification',
            help='Notification title'
        )
        parser.add_argument(
            '--body',
            type=str,
            default='This is a test notification from Entertainment List',
            help='Notification body'
        )
        parser.add_argument(
            '--url',
            type=str,
            default='/',
            help='URL to navigate to when clicked'
        )

    def handle(self, *args, **options):
        user_id = options['user_id']
        title = options['title']
        body = options['body']
        url = options['url']
        
        # Check if user exists
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise CommandError(f'User with ID {user_id} does not exist')
        
        # Check if user has subscriptions
        from notifications.models import PushSubscription
        subscriptions = PushSubscription.objects.filter(user=user, is_active=True)
        
        if not subscriptions.exists():
            self.stdout.write(
                self.style.WARNING(
                    f'\n⚠️  User {user.username} has no active push subscriptions.\n'
                    f'They need to subscribe at: /api/notifications/test-page/\n'
                )
            )
            return
        
        self.stdout.write(f'\nSending test notification to {user.username}...')
        self.stdout.write(f'  Title: {title}')
        self.stdout.write(f'  Body: {body}')
        self.stdout.write(f'  URL: {url}')
        self.stdout.write(f'  Active subscriptions: {subscriptions.count()}\n')
        
        # Send notification
        result = send_notification_to_user(
            user_id=user_id,
            title=title,
            body=body,
            notification_type='system',
            url=url,
        )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\n✅ Notification sent!'
                f'\n   Successful: {result.get("success", 0)}'
                f'\n   Failed: {result.get("failed", 0)}'
            )
        )
        
        if result.get('failed', 0) > 0:
            self.stdout.write(
                self.style.WARNING(
                    '\nℹ️  Check /admin/notifications/notificationlog/ for error details'
                )
            )
