"""
Test script to manually check for new episodes and send notifications.
This can be run from Django shell or as a standalone script.
"""

def test_episode_notifications(date_str=None):
    """
    Test the episode notification system.
    
    Args:
        date_str: Optional date string in format 'YYYY-MM-DD' to check specific date
    """
    from django.utils import timezone
    from tvshows.tasks import check_new_episodes_today
    from tvshows.models import Episode
    from datetime import datetime
    
    if date_str:
        check_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        print(f"\n🔍 Checking for episodes on {check_date}...")
        
        # Temporarily modify Episode air_dates for testing
        episodes = Episode.objects.filter(air_date=check_date)
        print(f"Found {episodes.count()} episode(s) on {check_date}")
        
        if episodes.exists():
            print("\nEpisodes found:")
            for ep in episodes[:5]:  # Show first 5
                print(f"  - {ep}")
    else:
        print(f"\n🔍 Checking for episodes today ({timezone.now().date()})...")
    
    # Run the task
    result = check_new_episodes_today()
    
    print(f"\n✅ Notification Results:")
    print(f"  Shows with new episodes: {result.get('shows_with_episodes', 0)}")
    print(f"  Total episodes: {result.get('total_episodes', 0)}")
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
        test_episode_notifications(sys.argv[1])
    else:
        test_episode_notifications()
