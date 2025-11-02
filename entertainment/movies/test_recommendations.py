"""
Test script to preview bi-weekly recommendations for a user.
Usage: python manage.py shell < test_recommendations.py
"""
import random
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from movies.models import Movie, MovieOfWeekPick
from movies.services.recommendation import MovieRecommender
from custom_auth.models import Review

User = get_user_model()


def get_unwatched_movie_of_week(user, movie_content_type):
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


def test_recommendations(username):
    """Test recommendations for a specific user."""
    try:
        user = User.objects.get(username=username)
        print(f"\n{'='*60}")
        print(f"Testing recommendations for: {user.username}")
        print(f"{'='*60}\n")
        
        movie_content_type = ContentType.objects.get_for_model(Movie)
        recommender = MovieRecommender()
        
        # Test personalized recommendations
        print("🎬 PERSONALIZED RECOMMENDATIONS:")
        print("-" * 60)
        recommendations = recommender.get_recommendations_for_user(user.id, max_recommendations=5)
        if recommendations:
            for i, movie in enumerate(recommendations, 1):
                rating = f"⭐ {movie.vote_average:.1f}/10" if movie.vote_average else "No rating"
                genres = ", ".join([g.name for g in movie.genres.all()[:2]]) if movie.genres.exists() else "N/A"
                print(f"{i}. {movie.title}")
                print(f"   {rating} | {genres}")
                if movie.tagline:
                    print(f'   "{movie.tagline}"')
                print()
        else:
            print("No personalized recommendations available.\n")
        
        # Test Movie of the Week recommendations
        print("\n🏆 UNWATCHED MOVIE OF THE WEEK PICKS:")
        print("-" * 60)
        motw_pick = get_unwatched_movie_of_week(user, movie_content_type)
        if motw_pick:
            rating = f"⭐ {motw_pick.vote_average:.1f}/10" if motw_pick.vote_average else "No rating"
            genres = ", ".join([g.name for g in motw_pick.genres.all()[:2]]) if motw_pick.genres.exists() else "N/A"
            print(f"• {motw_pick.title}")
            print(f"  {rating} | {genres}")
            if motw_pick.tagline:
                print(f'  "{motw_pick.tagline}"')
            print()
        else:
            print("No unwatched Movie of the Week picks available.\n")
        
        # Show stats
        print("\n📊 USER STATS:")
        print("-" * 60)
        total_reviews = Review.objects.filter(user=user, content_type=movie_content_type).count()
        print(f"Total movie reviews: {total_reviews}")
        
        total_motw_watched = MovieOfWeekPick.objects.filter(watched_by=user).count()
        total_motw_completed = MovieOfWeekPick.objects.filter(status='completed').count()
        print(f"Movie of the Week watched: {total_motw_watched}/{total_motw_completed}")
        
        prefs = user.notification_preferences
        print(f"Recommendations enabled: {prefs.recommendations}")
        print(f"Quiet hours: {'Yes' if prefs.quiet_hours_enabled else 'No'}")
        if prefs.quiet_hours_enabled:
            print(f"Quiet hours window: {prefs.quiet_hours_start} - {prefs.quiet_hours_end}")
        
    except User.DoesNotExist:
        print(f"User '{username}' not found.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        test_recommendations(sys.argv[1])
    else:
        # Default test user
        test_recommendations("marks")
