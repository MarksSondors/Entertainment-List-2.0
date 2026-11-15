"""Pre-render Stremio posters when what's drawn on them changes, instead of on a timer.

Poster cache keys are content hashes (see poster.content_version), so nothing here
invalidates anything: these handlers only get the new image rendered before Stremio
asks for it. If one is missed, the catalog request queues the render anyway.
"""
import logging

from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

EVENT_DEDUPE_TTL = 30  # collapse bursts (e.g. "mark season watched") into one render


def _queue(media_type: str, imdb_id: str, user_id: int, ctx: str | None) -> None:
    if not imdb_id:
        return

    def enqueue():
        # Deduping and enqueueing touch Redis; never let a cache outage break the save itself.
        try:
            if not cache.add(f"stremio:evtq:{media_type}:{imdb_id}:{user_id}:{ctx or ''}", 1, EVENT_DEDUPE_TTL):
                return
            from django_q.tasks import async_task
            async_task('stremio.tasks.warm_poster', media_type, imdb_id, user_id, ctx)
        except Exception:
            logger.warning("poster pre-render not queued for %s %s", media_type, imdb_id, exc_info=True)

    # after commit, so the task sees the new rating/progress, and nothing runs inside the
    # caller's transaction (a slow Redis must not hold a DB transaction open)
    transaction.on_commit(enqueue)


def _addon_users_watching(media):
    """Addon users who have this title on their watchlist (the only ones whose grid shows it)."""
    from django.contrib.contenttypes.models import ContentType
    from django.db.models import Q

    from custom_auth.models import Watchlist

    ct = ContentType.objects.get_for_model(media.__class__)
    return (Watchlist.objects.filter(content_type=ct, object_id=media.id)
            .exclude(Q(user__api_key__isnull=True) | Q(user__api_key=''))
            .values_list('user_id', flat=True))


def review_changed(sender, instance, **kwargs):
    """A rating moved the community average: re-render this title for everyone watching it."""
    try:
        media = instance.media
    except Exception:
        return
    if media is None or not getattr(media, 'imdb_id', None):
        return
    media_type = 'series' if media.__class__.__name__ == 'TVShow' else 'movie'
    for user_id in _addon_users_watching(media):
        _queue(media_type, media.imdb_id, user_id, None)
        if media_type == 'series':
            _queue(media_type, media.imdb_id, user_id, 'cw')


def watched_episode_changed(sender, instance, **kwargs):
    """Progress moved: re-render this user's show poster (progress chip and next-episode chip)."""
    try:
        show = instance.episode.season.show
    except Exception:  # unsaved/partial instance (or episode already gone on cascade delete)
        return
    if not show.imdb_id:
        return
    _queue('series', show.imdb_id, instance.user_id, 'cw')
    _queue('series', show.imdb_id, instance.user_id, None)


def connect():
    from django.db.models.signals import post_delete, post_save

    from custom_auth.models import Review
    from tvshows.models import WatchedEpisode

    for signal in (post_save, post_delete):
        signal.connect(review_changed, sender=Review, dispatch_uid=f'stremio_poster_review_{signal is post_save}')
        signal.connect(watched_episode_changed, sender=WatchedEpisode,
                       dispatch_uid=f'stremio_poster_watched_{signal is post_save}')
