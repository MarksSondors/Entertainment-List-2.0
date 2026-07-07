from django.apps import AppConfig


class AuthConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'custom_auth'

    def ready(self):
        # Register scheduled tasks after migrations complete. Using
        # post_migrate avoids DB access during app initialization
        # (Gunicorn worker boot) — same pattern as movies/apps.py.
        from django.db.models.signals import post_migrate
        post_migrate.connect(_setup_custom_auth_schedules, sender=self)


def _setup_custom_auth_schedules(sender, **kwargs):
    from django.db.utils import OperationalError, ProgrammingError
    from django_q.models import Schedule

    try:
        # Weekly safety net for `Person.media_count`. Signals cover the
        # common paths; this catches drift introduced by bulk_create /
        # queryset.update() / raw SQL that skips signal dispatch.
        Schedule.objects.get_or_create(
            func='custom_auth.tasks.reconcile_person_media_counts',
            defaults={
                'name': 'Reconcile Person.media_count',
                'schedule_type': Schedule.WEEKLY,
                'repeats': -1,
                'next_run': None,
            },
        )
    except (OperationalError, ProgrammingError):
        # DB not ready yet (first-ever migrate) — the next boot will register.
        pass
