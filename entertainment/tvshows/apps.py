from django.apps import AppConfig


class TvshowsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tvshows'
    
    def ready(self):
        """
        Set up scheduled tasks when the app is ready.
        """
        from django_q.models import Schedule
        from django.db.utils import OperationalError, ProgrammingError
        
        try:
            # Create scheduled task to check for new episodes daily at 9 AM
            Schedule.objects.get_or_create(
                func='tvshows.tasks.check_new_episodes_today',
                defaults={
                    'name': 'Check New TV Episodes',
                    'schedule_type': Schedule.DAILY,
                    'repeats': -1,  # Run indefinitely
                    'next_run': None,  # Will calculate based on schedule
                }
            )
        except (OperationalError, ProgrammingError):
            # Database not ready yet (e.g., during migrations)
            pass
