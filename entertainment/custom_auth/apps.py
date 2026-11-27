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


def _next_sunday_at(hour, minute):
    """Next Sunday at the given local (TIME_ZONE) time, as an aware datetime."""
    from datetime import timedelta
    from django.utils import timezone

    now = timezone.localtime()
    run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    run += timedelta(days=(6 - now.weekday()) % 7)
    if run <= now:
        run += timedelta(days=7)
    return run


def _next_daily_at(hour, minute):
    """Next occurrence of the given local (TIME_ZONE) time, as an aware datetime."""
    from datetime import timedelta
    from django.utils import timezone

    now = timezone.localtime()
    run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return run


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
        # Weekly removal of people no longer linked to any media, at a quiet
        # hour so it doesn't race imports that create a Person before its credit.
        Schedule.objects.get_or_create(
            func='custom_auth.tasks.cleanup_orphan_people',
            defaults={
                'name': 'Clean up orphaned people',
                'schedule_type': Schedule.WEEKLY,
                'repeats': -1,
                'next_run': _next_sunday_at(4, 30),
            },
        )
        # Daily IMDb ratings from IMDb's dataset, which is regenerated overnight (about 01:00 UTC).
        Schedule.objects.get_or_create(
            func='custom_auth.tasks.sync_imdb_ratings',
            defaults={
                'name': 'Sync IMDb ratings',
                'schedule_type': Schedule.DAILY,
                'repeats': -1,
                'next_run': _next_daily_at(5, 15),
            },
        )
        # Weekly removal of expired login sessions.
        Schedule.objects.get_or_create(
            func='custom_auth.tasks.clear_expired_sessions',
            defaults={
                'name': 'Clear expired sessions',
                'schedule_type': Schedule.WEEKLY,
                'repeats': -1,
                'next_run': _next_sunday_at(4, 45),
            },
        )
    except (OperationalError, ProgrammingError):
        # DB not ready yet (first-ever migrate) — the next boot will register.
        pass
