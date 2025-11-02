"""
Test script to manually check for new movie releases and send notifications.
This can be run from Django shell or as a standalone script.
"""

def test_movie_notifications(date_str=None):
    """
    Test the movie notification system.
    
    Args:
        date_str: Optional date string in format 'YYYY-MM-DD' to check specific date
    """
    from django.utils import timezone
    from movies.tasks import check_new_movies_today
    from movies.models import Movie
    from datetime import datetime
    
    if date_str:
        check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        print(f"\n🔍 Checking for movies releasing on {check_date}...")
        
        # Show movies releasing on that date
        movies = Movie.objects.filter(release_date=check_date)
        print(f"Found {movies.count()} movie(s) on {check_date}")
        
        if movies.exists():
            print("\nMovies found:")
            for movie in movies[:5]:  # Show first 5
                print(f"  - {movie.title} ({movie.release_date})")
    else:
        print(f"\n🔍 Checking for movies releasing today ({timezone.now().date()})...")
    
    # Run the task
    result = check_new_movies_today()
    
    print(f"\n✅ Notification Results:")
    print(f"  Movies released: {result.get('movies_released', 0)}")
    print(f"  Notifications sent: {result.get('notifications_sent', 0)}")
    print(f"  Users notified: {result.get('notified_users', 0)}")
    
    return result


if __name__ == "__main__":
    import os
    import django
    
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'entertainment.settings')
    django.setup()
    
    import sys
    if len(sys.argv) > 1:
        test_movie_notifications(sys.argv[1])
    else:
        test_movie_notifications()
