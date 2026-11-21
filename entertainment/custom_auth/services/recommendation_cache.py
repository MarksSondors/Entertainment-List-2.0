"""Per-user cache for the recommendation endpoints.

The movie recommender is a 397 MB pickle: unpickling it costs ~7 s and ~2.1 GB
of resident memory (measured in-process: RSS 159 MB -> 2280 MB). The web
endpoints used to rebuild it on every request, so ``/movies/recommendations/``
answered in 7.5-12.7 s (max 41.5 s) with 12 ms of actual database work. The
recommendation *output* depends only on the user's ratings and the trained
model, so the result is cached per user instead of the model.

The model cannot simply be kept resident in the web workers: gunicorn runs
three of them, and three copies of a 2.1 GB model do not fit next to
Postgres/Redis/Traefik on this box.

Mirrors the statistics cache (``custom_auth/services/statistics.py``): a
per-user key, explicit invalidation from the Review signals, and a TTL as a
backstop. The model file's mtime is part of the key, so a retrained model
serves fresh results without anyone having to remember to bust the cache.

Key layout::

    recommendations:<scope>:<model_version>:<user_id>:<generation>:<limit>

``generation`` is a per-user counter bumped on invalidation, so one bump makes
every earlier key unreachable at once (the stale entries fall out of Redis on
their own TTL). That avoids ``delete_pattern``, which the plain Django Redis
cache backend does not provide.
"""
from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# How long a computed recommendation list stays usable. The Stremio addon
# already cached the same model output for an hour, so this does not make the
# site any staler than the addon already is.
RECOMMENDATION_CACHE_TTL = 3600

# The external (TMDB-only) list is rebuilt from live TMDB metadata, so it gets
# a shorter life than the local list.
EXTERNAL_RECOMMENDATION_CACHE_TTL = 900

SCOPE_MOVIES = 'movies'
SCOPE_EXTERNAL = 'movies_external'
SCOPE_COMBINED = 'combined'
SCOPE_STREMIO = 'stremio_recommendations'
SCOPE_TVSHOWS = 'tvshows'
SCOPE_GAMES = 'games'


def model_version() -> str:
    """Version token for the trained model the recommendations come from."""
    from movies.services.recommendation import model_stamp

    stamp = model_stamp()
    return str(int(stamp)) if stamp else 'nomodel'


def _generation_key(user_id: int) -> str:
    return f'recommendations:generation:{user_id}'


def current_generation(user_id: int) -> int:
    """The user's current cache generation (bumped whenever their ratings change)."""
    key = _generation_key(user_id)
    value = cache.get(key)
    if value is None:
        cache.add(key, 1, None)
        value = cache.get(key)
    return int(value or 1)


def cache_key(scope: str, user_id: int, limit: int) -> str:
    return f'recommendations:{scope}:{model_version()}:{user_id}:{current_generation(user_id)}:{limit}'


def get_cached(scope: str, user_id: int, limit: int):
    """The cached payload for this user, or None when there is none."""
    return cache.get(cache_key(scope, user_id, limit))


def set_cached(scope: str, user_id: int, limit: int, payload, ttl: int | None = None):
    """Store a payload and return it, so callers can ``return set_cached(...)``."""
    cache.set(cache_key(scope, user_id, limit), payload, ttl or RECOMMENDATION_CACHE_TTL)
    return payload


def invalidate_user(user_id: int) -> None:
    """Drop every cached recommendation list for a user.

    Bumping the generation makes the previous keys unreachable; the stale
    entries themselves expire on their own TTL.
    """
    key = _generation_key(user_id)
    try:
        cache.incr(key)
    except ValueError:
        # No generation yet: the next read creates one, so start it above 1.
        cache.set(key, 2, None)
    logger.debug('Invalidated recommendation cache for user %s', user_id)
