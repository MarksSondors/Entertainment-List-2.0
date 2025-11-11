"""
Management command to test review notifications.
Usage: python manage.py test_review_notification --review-id <id>
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from custom_auth.models import Review
from notifications.models import NotificationPreference
from custom_auth.tasks import process_review_notification

User = get_user_model()


class Command(BaseCommand):
    help = 'Test review notification system by triggering a notification for a specific review'

    def add_arguments(self, parser):
        parser.add_argument(
            '--review-id',
            type=int,
            help='ID of the review to test notification for'
        )
        parser.add_argument(
            '--list-reviews',
            action='store_true',
            help='List recent reviews with their IDs'
        )

    def handle(self, *args, **options):
        if options['list_reviews']:
            self.stdout.write('\n📝 Recent Reviews:')
            self.stdout.write('=' * 80)
            
            reviews = Review.objects.select_related(
                'user', 'content_type'
            ).order_by('-date_added')[:10]
            
            for review in reviews:
                media = review.media
                media_title = getattr(media, 'title', str(media)) if media else 'Unknown'
                
                # Count users with new_reviews enabled
                users_with_preference = User.objects.filter(
                    notification_preferences__new_reviews=True
                ).exclude(id=review.user.id).count()
                
                self.stdout.write(
                    f'\nID: {review.id} | '
                    f'User: {review.user.username} | '
                    f'Rating: {review.rating}/10'
                )
                self.stdout.write(
                    f'Media: {media_title} | '
                    f'Users with new_reviews enabled: {users_with_preference}'
                )
                if review.review_text:
                    snippet = review.review_text[:50] + '...' if len(review.review_text) > 50 else review.review_text
                    self.stdout.write(f'Text: "{snippet}"')
            
            self.stdout.write('\n' + '=' * 80 + '\n')
            return

        review_id = options.get('review_id')
        if not review_id:
            self.stdout.write(
                self.style.ERROR('Please provide --review-id or use --list-reviews')
            )
            return

        try:
            review = Review.objects.get(id=review_id)
        except Review.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'Review with ID {review_id} not found')
            )
            return

        self.stdout.write('\n' + '=' * 80)
        self.stdout.write(f'Testing notification for Review ID: {review_id}')
        self.stdout.write('=' * 80 + '\n')

        # Show review details
        media = review.media
        media_title = getattr(media, 'title', str(media)) if media else 'Unknown'
        
        self.stdout.write(f'📝 Review Details:')
        self.stdout.write(f'  User: {review.user.username}')
        self.stdout.write(f'  Media: {media_title}')
        self.stdout.write(f'  Rating: {review.rating}/10')
        if review.review_text:
            self.stdout.write(f'  Text: "{review.review_text[:100]}..."')
        
        # Show users who will receive notification
        users_with_preference = User.objects.filter(
            notification_preferences__new_reviews=True
        ).exclude(id=review.user.id).select_related('notification_preferences')
        
        self.stdout.write(f'\n👥 Users with new_reviews enabled (excluding reviewer): {users_with_preference.count()}')
        for user in users_with_preference:
            self.stdout.write(f'  - {user.username}')
        
        if not users_with_preference.exists():
            self.stdout.write(
                self.style.WARNING('\n⚠️  No users have new_reviews notifications enabled!')
            )
            self.stdout.write('Notification will not be sent.\n')
            return

        # Process notification
        self.stdout.write('\n🔔 Processing notification...\n')
        
        result = process_review_notification(review_id)
        
        self.stdout.write('\n' + '=' * 80)
        self.stdout.write('✅ Notification Result:')
        self.stdout.write('=' * 80)
        self.stdout.write(f'  Notified Users: {result.get("notified_users", 0)}')
        self.stdout.write(f'  Notifications Sent: {result.get("notifications_sent", 0)}')
        
        if result.get('error'):
            self.stdout.write(self.style.ERROR(f'  Error: {result["error"]}'))
        
        self.stdout.write('\n💡 Check logs for detailed information')
        self.stdout.write('   (Look for 🔔 emoji in logs)\n')
