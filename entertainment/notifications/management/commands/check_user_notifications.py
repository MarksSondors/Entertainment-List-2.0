"""
Management command to check a user's notification preferences.
Usage: python manage.py check_user_notifications <username>
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from notifications.models import NotificationPreference, PushSubscription

User = get_user_model()


class Command(BaseCommand):
    help = 'Check notification preferences for a specific user'
    
    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Username to check')
    
    def handle(self, *args, **options):
        username = options['username']
        
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'User "{username}" not found'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'\n📊 Notification Settings for: {username}'))
        self.stdout.write('=' * 60)
        
        # Check notification preferences
        try:
            prefs = user.notification_preferences
            
            self.stdout.write('\n🔔 Notification Types:')
            self.stdout.write(f'  New Releases:         {"✅ Enabled" if prefs.new_releases else "❌ Disabled"}')
            self.stdout.write(f'  Watchlist Updates:    {"✅ Enabled" if prefs.watchlist_updates else "❌ Disabled"}')
            self.stdout.write(f'  Recommendations:      {"✅ Enabled" if prefs.recommendations else "❌ Disabled"}')
            self.stdout.write(f'  System Notifications: {"✅ Enabled" if prefs.system_notifications else "❌ Disabled"}')
            
            self.stdout.write('\n🌙 Quiet Hours:')
            if prefs.quiet_hours_enabled:
                self.stdout.write(f'  Status: ✅ Enabled')
                self.stdout.write(f'  From:   {prefs.quiet_hours_start}')
                self.stdout.write(f'  To:     {prefs.quiet_hours_end}')
                
                # Check if currently in quiet hours
                from notifications.utils import is_in_quiet_hours
                if is_in_quiet_hours(user):
                    self.stdout.write(self.style.WARNING('  ⚠️  Currently IN quiet hours - notifications will be queued'))
                else:
                    self.stdout.write('  ✅ Currently NOT in quiet hours')
            else:
                self.stdout.write('  Status: ❌ Disabled')
            
        except NotificationPreference.DoesNotExist:
            self.stdout.write(self.style.WARNING('\n⚠️  No notification preferences found (will use defaults)'))
        
        # Check push subscriptions
        subscriptions = PushSubscription.objects.filter(user=user)
        active_subs = subscriptions.filter(is_active=True)
        
        self.stdout.write('\n📱 Push Subscriptions:')
        self.stdout.write(f'  Total:  {subscriptions.count()}')
        self.stdout.write(f'  Active: {active_subs.count()}')
        
        if active_subs.exists():
            self.stdout.write('\n  Active Devices:')
            for sub in active_subs:
                device = sub.device_name or 'Unknown Device'
                self.stdout.write(f'    • {device} (added {sub.created_at.strftime("%Y-%m-%d")})')
        else:
            self.stdout.write(self.style.WARNING('  ⚠️  No active push subscriptions - user will not receive push notifications'))
        
        # Summary
        self.stdout.write('\n' + '=' * 60)
        can_receive = (
            active_subs.exists() and 
            (not hasattr(user, 'notification_preferences') or user.notification_preferences.new_releases)
        )
        
        if can_receive:
            self.stdout.write(self.style.SUCCESS('✅ User CAN receive new episode notifications'))
        else:
            reasons = []
            if not active_subs.exists():
                reasons.append('No active push subscriptions')
            if hasattr(user, 'notification_preferences') and not user.notification_preferences.new_releases:
                reasons.append('New releases disabled')
            
            self.stdout.write(self.style.ERROR(f'❌ User CANNOT receive notifications: {", ".join(reasons)}'))
        
        self.stdout.write('')
