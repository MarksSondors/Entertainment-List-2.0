"""
Management command to test bi-weekly recommendations for a user.
Usage: python manage.py test_recommendations <username>
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg
from movies.models import Movie, MovieOfWeekPick
from movies.services.recommendation import MovieRecommender
from custom_auth.models import Review
import random

User = get_user_model()


class Command(BaseCommand):
    help = 'Test bi-weekly movie recommendations for a specific user'

    def add_arguments(self, parser):
        parser.add_argument('username', type=str, help='Username to test recommendations for')

    def handle(self, *args, **options):
        username = options['username']
        
        try:
            user = User.objects.get(username=username)
            
            self.stdout.write(f"\n{'='*60}")
            self.stdout.write(self.style.SUCCESS(f"Testing recommendations for: {user.username}"))
            self.stdout.write(f"{'='*60}\n")
            
            movie_content_type = ContentType.objects.get_for_model(Movie)
            recommender = MovieRecommender()
            
            # Test personalized recommendations
            self.stdout.write(self.style.WARNING("🎬 PERSONALIZED RECOMMENDATIONS:"))
            self.stdout.write("-" * 60)
            recommendations = recommender.get_recommendations_for_user(user.id, max_recommendations=5)
            if recommendations:
                for i, movie in enumerate(recommendations, 1):
                    # Get average user rating from reviews
                    avg_rating = Review.objects.filter(
                        content_type=movie_content_type,
                        object_id=movie.id
                    ).aggregate(Avg('rating'))['rating__avg']
                    
                    if avg_rating:
                        rating = f"⭐ {avg_rating:.1f}/10 (user reviews)"
                    else:
                        rating = "No user reviews yet"
                    
                    genres = ", ".join([g.name for g in movie.genres.all()[:2]]) if movie.genres.exists() else "N/A"
                    self.stdout.write(f"{i}. {movie.title}")
                    self.stdout.write(f"   {rating} | {genres}")
                    self.stdout.write("")
            else:
                self.stdout.write("No personalized recommendations available.\n")
            
            # Test Movie of the Week recommendations
            self.stdout.write(self.style.WARNING("\n🏆 UNWATCHED MOVIE OF THE WEEK PICKS:"))
            self.stdout.write("-" * 60)
            motw_pick = self._get_unwatched_movie_of_week(user, movie_content_type)
            if motw_pick:
                # Get average user rating from reviews
                avg_rating = Review.objects.filter(
                    content_type=movie_content_type,
                    object_id=motw_pick.id
                ).aggregate(Avg('rating'))['rating__avg']
                
                if avg_rating:
                    rating = f"⭐ {avg_rating:.1f}/10 (user reviews)"
                else:
                    rating = "No user reviews yet"
                
                genres = ", ".join([g.name for g in motw_pick.genres.all()[:2]]) if motw_pick.genres.exists() else "N/A"
                self.stdout.write(f"• {motw_pick.title}")
                self.stdout.write(f"  {rating} | {genres}")
                self.stdout.write("")
            else:
                self.stdout.write("No unwatched Movie of the Week picks available.\n")
            
            # Show stats
            self.stdout.write(self.style.WARNING("\n📊 USER STATS:"))
            self.stdout.write("-" * 60)
            total_reviews = Review.objects.filter(user=user, content_type=movie_content_type).count()
            self.stdout.write(f"Total movie reviews: {total_reviews}")
            
            total_motw_watched = MovieOfWeekPick.objects.filter(watched_by=user).count()
            total_motw_completed = MovieOfWeekPick.objects.filter(status='completed').count()
            self.stdout.write(f"Movie of the Week watched: {total_motw_watched}/{total_motw_completed}")
            
            prefs = user.notification_preferences
            self.stdout.write(f"Recommendations enabled: {prefs.recommendations}")
            self.stdout.write(f"Quiet hours: {'Yes' if prefs.quiet_hours_enabled else 'No'}")
            if prefs.quiet_hours_enabled:
                self.stdout.write(f"Quiet hours window: {prefs.quiet_hours_start} - {prefs.quiet_hours_end}")
            
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User '{username}' not found."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            import traceback
            traceback.print_exc()
    
    def _get_unwatched_movie_of_week(self, user, movie_content_type):
        """Get a previous Movie of the Week that the user hasn't watched or reviewed."""
        # Get user's watched movie IDs (from reviews)
        reviewed_movie_ids = set(
            Review.objects.filter(
                user=user,
                content_type=movie_content_type
            ).values_list('object_id', flat=True)
        )
        
        # Get completed Movie of the Week picks
        completed_picks = MovieOfWeekPick.objects.filter(
            status='completed'
        ).exclude(
            watched_by=user
        ).exclude(
            movie_id__in=reviewed_movie_ids
        ).select_related('movie').order_by('-end_date')
        
        if completed_picks.exists():
            recent_unwatched = list(completed_picks[:10])
            return random.choice(recent_unwatched).movie
        
        return None
