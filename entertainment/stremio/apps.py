from django.apps import AppConfig


class StremioConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'stremio'
    verbose_name = 'Stremio Addon'

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_setup_stremio_schedules, sender=self)


def _setup_stremio_schedules(sender, **kwargs):
    """
    Set up scheduled tasks after migrations complete.
    Using post_migrate avoids DB access during app initialization (Gunicorn worker boot).
    """
    from django_q.models import Schedule
    from django.db.utils import OperationalError, ProgrammingError

    try:
        # Keep poster overlays warm so they're already cached before a user opens Stremio
        Schedule.objects.get_or_create(
            func='stremio.tasks.warm_all_posters',
            defaults={
                'name': 'Warm Stremio Poster Cache',
                'schedule_type': Schedule.MINUTES,
                'minutes': 20,
                'repeats': -1,  # Run indefinitely
            }
        )
        # Refresh the (1h TTL) Discover cache before it expires, so it's never built on-demand
        Schedule.objects.get_or_create(
            func='stremio.tasks.warm_all_discover_external',
            defaults={
                'name': 'Warm Stremio Discover Cache',
                'schedule_type': Schedule.MINUTES,
                'minutes': 45,
                'repeats': -1,  # Run indefinitely
            }
        )
        # Refresh the (1h TTL) ML recommendations cache before it expires
        Schedule.objects.get_or_create(
            func='stremio.tasks.warm_all_recommendations',
            defaults={
                'name': 'Warm Stremio Recommendations Cache',
                'schedule_type': Schedule.MINUTES,
                'minutes': 45,
                'repeats': -1,  # Run indefinitely
            }
        )
    except (OperationalError, ProgrammingError):
        pass
