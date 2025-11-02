"""
Management command to check for new movie releases and notify users.
Usage: python manage.py notify_new_movies
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from movies.models import Movie
from custom_auth.models import Watchlist
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for new movie releases and notify users who have the movie in their watchlist'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Check for movies releasing on a specific date (YYYY-MM-DD). Defaults to today.',
        )
    
    def handle(self, *args, **options):
        from notifications.utils import send_notification_to_user
        
        # Determine which date to check
        if options['date']:
            from datetime import datetime
            check_date = datetime.strptime(options['date'], '%Y-%m-%d').date()
        else:
            check_date = timezone.now().date()
        
        self.stdout.write(f'Checking for movies releasing on {check_date}...')
        
        # Get all movies releasing today
        movies_today = Movie.objects.filter(
            release_date=check_date
        ).prefetch_related('genres')
        
        if not movies_today.exists():
            self.stdout.write(self.style.WARNING(f'No movies found releasing on {check_date}'))
            return
        
        self.stdout.write(f'Found {movies_today.count()} movie(s) releasing today')
        
        # Get ContentType for Movie
        movie_content_type = ContentType.objects.get_for_model(Movie)
        
        # Track notification stats
        total_notifications = 0
        total_users = 0
        
        # For each movie releasing today
        for movie in movies_today:
            self.stdout.write(f'\nProcessing: {movie.title}')
            
            # Get all users who have this movie in their watchlist
            watchlist_users = Watchlist.objects.filter(
                content_type=movie_content_type,
                object_id=movie.id
            ).select_related('user')
            
            if not watchlist_users.exists():
                self.stdout.write(f'  No users watching this movie')
                continue
            
            self.stdout.write(f'  Notifying {watchlist_users.count()} user(s)')
            
            # Create notification message
            title = f"🎬 New Release: {movie.title}"
            
            # Add genre info if available
            genres = movie.genres.all()[:2]  # First 2 genres
            if genres:
                genre_text = ", ".join([g.name for g in genres])
                body = f"{movie.title} is now available! ({genre_text})"
            else:
                body = f"{movie.title} is now available!"
            
            # Add runtime if available
            if movie.runtime:
                body += f" • {movie.minutes_to_hours()}"
            
            url = movie.get_absolute_url()
            
            # Send notification to each user
            for watchlist_item in watchlist_users:
                result = send_notification_to_user(
                    user_id=watchlist_item.user.id,
                    title=title,
                    body=body,
                    notification_type='new_release',
                    url=url,
                    icon=movie.poster or '/static/favicon/web-app-manifest-192x192.png',
                    content_type=movie_content_type,
                    object_id=movie.id,
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
