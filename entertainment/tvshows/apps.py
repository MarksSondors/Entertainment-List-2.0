from django.apps import AppConfig


class TvshowsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tvshows'

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_setup_tvshow_schedules, sender=self)


def _setup_tvshow_schedules(sender, **kwargs):
    """
    Set up scheduled tasks after migrations complete.
    Using post_migrate avoids DB access during app initialization (Gunicorn worker boot).
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
        pass
