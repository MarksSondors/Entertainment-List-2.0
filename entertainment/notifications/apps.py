from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    
    def ready(self):
        """
        Set up scheduled tasks when the app is ready.
        """
        # Import here to avoid AppRegistryNotReady error
        from django_q.models import Schedule
        from django.db.utils import OperationalError, ProgrammingError
        
        try:
            # Create scheduled task to process queued notifications every hour
            Schedule.objects.get_or_create(
                func='notifications.tasks.process_notification_queue',
                defaults={
                    'name': 'Process Queued Notifications',
                    'schedule_type': Schedule.HOURLY,
                    'repeats': -1,  # Run indefinitely
                }
            )
            
            # Create scheduled task to clean up old notifications weekly
            Schedule.objects.get_or_create(
                func='notifications.tasks.cleanup_all_notifications',
                defaults={
                    'name': 'Cleanup Old Notifications',
                    'schedule_type': Schedule.WEEKLY,
                    'repeats': -1,  # Run indefinitely
                }
            )
        except (OperationalError, ProgrammingError):
            # Database not ready yet (e.g., during migrations)
            pass
