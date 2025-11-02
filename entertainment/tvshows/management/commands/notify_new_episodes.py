"""
Management command to check for new TV show episodes and notify users.
Usage: python manage.py notify_new_episodes
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from tvshows.models import Episode, TVShow
from custom_auth.models import Watchlist
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for new TV show episodes and notify users who have the show in their watchlist'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Check for episodes on a specific date (YYYY-MM-DD). Defaults to today.',
        )
    
    def handle(self, *args, **options):
        from notifications.utils import send_notification_to_user
        
        # Determine which date to check
        if options['date']:
            from datetime import datetime
            check_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
        else:
            check_date = timezone.now().date()
        
        self.stdout.write(f'Checking for episodes airing on {check_date}...')
        
        # Get all episodes airing today
        episodes_today = Episode.objects.filter(
            air_date=check_date
        ).select_related('season', 'season__show').order_by('season__show', 'season__season_number', 'episode_number')
        
        if not episodes_today.exists():
            self.stdout.write(self.style.WARNING(f'No episodes found for {check_date}'))
            return
        
        self.stdout.write(f'Found {episodes_today.count()} episode(s) airing today')
        
        # Group episodes by show
        episodes_by_show = defaultdict(list)
        for episode in episodes_today:
            episodes_by_show[episode.season.show].append(episode)
        
        # Get ContentType for TVShow
        tvshow_content_type = ContentType.objects.get_for_model(TVShow)
        
        # Track notification stats
        total_notifications = 0
        total_users = 0
        
        # For each show with episodes today
        for show, episodes in episodes_by_show.items():
            self.stdout.write(f'\nProcessing: {show.title} ({len(episodes)} episode(s))')
            
            # Get all users who have this show in their watchlist
            watchlist_users = Watchlist.objects.filter(
                content_type=tvshow_content_type,
                object_id=show.id
            ).select_related('user')
            
            if not watchlist_users.exists():
                self.stdout.write(f'  No users watching this show')
                continue
            
            self.stdout.write(f'  Notifying {watchlist_users.count()} user(s)')
            
            # Create notification message
            if len(episodes) == 1:
                episode = episodes[0]
                title = f"📺 New Episode: {show.title}"
                body = f"S{episode.season.season_number:02d}E{episode.episode_number:02d} - {episode.title} is now available!"
            else:
                title = f"📺 New Episodes: {show.title}"
                episode_list = ", ".join([
                    f"S{ep.season.season_number:02d}E{ep.episode_number:02d}"
                    for ep in episodes
                ])
                body = f"{len(episodes)} new episodes are now available: {episode_list}"
            
            url = show.get_absolute_url()
            
            # Send notification to each user
            for watchlist_item in watchlist_users:
                result = send_notification_to_user(
                    user_id=watchlist_item.user.id,
                    title=title,
                    body=body,
                    notification_type='new_release',
                    url=url,
                    icon=show.poster or '/static/favicon/web-app-manifest-192x192.png',
                    content_type=tvshow_content_type,
                    object_id=show.id,
                )
                
                if result.get('success', 0) > 0:
                    total_notifications += 1
                    self.stdout.write(f'    ✅ Sent to {watchlist_item.user.username}')
                elif result.get('queued'):
                    total_notifications += 1
                    self.stdout.write(f'    ⏰ Queued for {watchlist_item.user.username} (quiet hours)')
                elif result.get('skipped'):
                    self.stdout.write(f'    ⚠️  Skipped {watchlist_item.user.username} (new releases disabled)')
                else:
                    self.stdout.write(f'    ❌ Failed {watchlist_item.user.username}')
            
            total_users += watchlist_users.count()
        
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Complete! Sent {total_notifications} notification(s) to {total_users} user(s)'
        ))
