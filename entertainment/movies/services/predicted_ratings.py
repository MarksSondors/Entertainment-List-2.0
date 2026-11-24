"""Per-user predicted ratings ("For you: 7.8") for every movie in the database.

The recommender model is ~300 MB on disk and ~2 GB in memory, so web requests
must never load it. The scheduled ``stremio.tasks.warm_all_recommendations``
sweep already holds one model instance; it calls ``build_for_user`` for each
active user and this module caches ``{tmdb_id: rating_0_to_10}`` in Redis.
The movie page only reads that cache: no entry means nothing is shown.
"""
from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# The sweep runs every 50 minutes; keep entries across a couple of missed runs.
CACHE_TTL = 6 * 60 * 60


def _key(user_id: int) -> str:
    return f"predicted_ratings:{user_id}"


def build_for_user(recommender, user) -> int:
    """Compute and cache predictions for ``user``; returns how many were stored."""
    from movies.models import Movie

    user_id_str = f"loc_{user.id}"
    factor = recommender.get_user_factor(user.id)
    if factor is None:
        cache.delete(_key(user.id))
        return 0
    # Users not in the trained index are scored with their cold-start factor.
    override = None if recommender._user_factor(user_id_str) is not None else factor

    predictions = {}
    for tmdb_id, year in Movie.objects.exclude(tmdb_id__isnull=True).values_list('tmdb_id', 'release_date__year'):
        est = recommender.predict_rating(user_id_str, int(tmdb_id), year=year, user_factor_override=override)
        if est:
            # same clamp and 0-10 display scale as the recommendation strips
            predictions[int(tmdb_id)] = round(max(0.5, min(5.0, est)) * 2, 1)
    cache.set(_key(user.id), predictions, CACHE_TTL)
    return len(predictions)


def get_for_movie(user_id: int, tmdb_id) -> float | None:
    """Cached prediction for one movie, or None (not computed yet, or cache down)."""
    try:
        predictions = cache.get(_key(user_id))
    except Exception:  # never break the movie page over Redis
        logger.warning("predicted ratings cache unavailable", exc_info=True)
        return None
    if not predictions:
        return None
    return predictions.get(int(tmdb_id))
