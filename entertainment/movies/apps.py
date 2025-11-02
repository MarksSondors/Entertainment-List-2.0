from django.apps import AppConfig


class MoviesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'movies'
    
    def ready(self):
        """
        Set up scheduled tasks when the app is ready.
        """
        from django_q.models import Schedule
        from django.db.utils import OperationalError, ProgrammingError
        
        try:
            # Create scheduled task to check for new movie releases daily at 9 AM
            Schedule.objects.get_or_create(
                func='movies.tasks.check_new_movies_today',
                defaults={
                    'name': 'Check New Movie Releases',
                    'schedule_type': Schedule.DAILY,
                    'repeats': -1,  # Run indefinitely
                    'next_run': None,  # Will calculate based on schedule
                }
            )
            
            # Create scheduled task to send bi-weekly recommendations
            # Run every 14 days at 10 AM
            from datetime import timedelta
            from django.utils import timezone
            
            Schedule.objects.get_or_create(
                func='movies.tasks.send_biweekly_recommendations',
                defaults={
                    'name': 'Send Bi-Weekly Movie Recommendations',
                    'schedule_type': Schedule.DAILY,
                    'repeats': -1,  # Run indefinitely
                    'minutes': 14 * 24 * 60,  # 14 days in minutes (20160)
                    'next_run': None,  # Will calculate based on schedule
                }
            )
            
            # Create scheduled task to notify about Movie of the Week
            # Run every 2 days at 5 PM (17:00)
            Schedule.objects.get_or_create(
                func='movies.tasks.notify_active_movie_of_week',
                defaults={
                    'name': 'Notify Movie of the Week',
                    'schedule_type': Schedule.DAILY,
                    'repeats': -1,  # Run indefinitely
                    'minutes': 2 * 24 * 60,  # 2 days in minutes (2880)
                    'next_run': None,  # Will calculate based on schedule
                }
            )
        except (OperationalError, ProgrammingError):
            # Database not ready yet (e.g., during migrations)
            pass
