"""Per-user generation counter for the outer Stremio catalog cache.

Catalog pages are cached for CATALOG_CACHE_TTL under keys that include this
counter. Bumping it on a rating / watchlist / watched-episode change makes every
earlier page key for that user unreachable at once, so Stremio sees the change on
its next poll instead of up to two minutes later. Stale entries expire on their
own TTL (the plain Django Redis backend has no delete_pattern).
"""
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


def _key(user_id: int) -> str:
    return f"stremio_catalog_gen_{user_id}"


def catalog_generation(user_id: int) -> int:
    key = _key(user_id)
    value = cache.get(key)
    if value is None:
        cache.add(key, 1, None)
        value = cache.get(key)
    return int(value or 1)


def bump_catalog_generation(user_id: int | None) -> None:
    """Invalidate a user's cached catalog pages. Never raises: a cache outage must not break a save."""
    if not user_id:
        return
    key = _key(user_id)
    try:
        try:
            cache.incr(key)
        except ValueError:  # no generation yet; the next read would create 1, so start above it
            cache.set(key, 2, None)
    except Exception:
        logger.warning("stremio catalog cache not invalidated for user %s", user_id, exc_info=True)
