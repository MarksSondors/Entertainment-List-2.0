"""Taste-map data for the movie network graph, derived from the trained recommender.

The recommender's iALS factors put movies *and* users in one learned "taste space":
movies liked by the same people sit close together. This module turns that into
graph-sized payloads:

- ``neighbors``: each movie's nearest movies in taste space (cosine on the factors).
  The frontend's "Taste space" layout runs its force layout over these links.
- ``anchors``: for a user, the movies closest to their own factor - where they land
  on the map.
- per-user ``predicted`` ratings, learned taste ``profile`` and top ``picks``.

Computing any of this needs the ~300 MB model, which must not live in every web
worker, so it only ever runs inside Django Q (``warm_graph_taste``). The web view
just reads the cached results via ``get_taste_payload`` and, on a miss, enqueues
the task and answers ``pending`` so the page can poll.
"""
import logging
from typing import Dict, List, Optional

import numpy as np
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache

from custom_auth.models import Review, Watchlist
from movies.models import Movie

logger = logging.getLogger(__name__)

TASTE_NEIGHBORS_K = 5
ANCHORS_K = 12
PICKS_LIMIT = 20
TASTE_CACHE_TTL = 3600
PENDING_TTL = 300  # dedupes the enqueue while a warm task is in flight
FAILED_TTL = 600  # after a failed build, report 'unavailable' for a while instead of retrying
_KNN_CHUNK = 1024


def _node_id(movie_id: int) -> str:
    # must match builders.core._movie_node_id
    return f"movie_{movie_id}"


def _global_key(stamp) -> str:
    return f"graph:taste:global:{stamp}"


def _user_key(user_id: int, stamp) -> str:
    return f"graph:taste:user:{user_id}:{stamp}"


def _failed_key(stamp) -> str:
    return f"graph:taste:failed:{stamp}"


def _graph_movies() -> List[tuple]:
    """(db id, tmdb id) for the same movie set the graph builder seeds from."""
    return list(
        Movie.objects.exclude(release_date__isnull=True)
        .filter(tmdb_id__isnull=False)
        .values_list('id', 'tmdb_id')
    )


def _normalized(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)


def _movie_factor_matrix(recommender, movies: List[tuple]):
    """Stack normalised factors for the graph movies that have a trustworthy one.

    Cold-start vectors are only kept when the catalog knows the movie's genres -
    otherwise the head returns a near-constant vector and every unknown movie would
    become everyone else's nearest neighbour.
    """
    factors = recommender.get_item_factors([tmdb for _, tmdb in movies])
    known = recommender.item_to_idx
    genres = recommender.catalog.tmdb_to_genres or {}
    kept_ids, rows = [], []
    for movie_id, tmdb in movies:
        vec = factors.get(int(tmdb))
        if vec is None or (int(tmdb) not in known and not genres.get(int(tmdb))):
            continue
        kept_ids.append(movie_id)
        rows.append(vec)
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    return kept_ids, _normalized(np.vstack(rows).astype(np.float32))


def _knn(matrix: np.ndarray, ids: List[int], k: int) -> Dict[str, list]:
    """Top-k cosine neighbours per row, chunked so memory stays O(chunk x N)."""
    neighbors = {}
    n = len(ids)
    k = min(k, n - 1)
    if k <= 0:
        return neighbors
    for start in range(0, n, _KNN_CHUNK):
        block = matrix[start:start + _KNN_CHUNK] @ matrix.T
        for offset in range(block.shape[0]):
            block[offset, start + offset] = -np.inf  # not your own neighbour
        top = np.argpartition(-block, k, axis=1)[:, :k]
        for offset, cols in enumerate(top):
            row = block[offset]
            cols = cols[np.argsort(-row[cols])]
            neighbors[_node_id(ids[start + offset])] = [
                [_node_id(ids[c]), round(float(row[c]), 3)] for c in cols
            ]
    return neighbors


def _anchors(user_factor: Optional[np.ndarray], matrix: np.ndarray, ids: List[int]) -> list:
    """The graph movies closest to a user's factor, with weights for placing them."""
    if user_factor is None or not len(ids):
        return []
    u = np.asarray(user_factor, dtype=np.float32)
    sims = matrix @ (u / max(float(np.linalg.norm(u)), 1e-9))
    k = min(ANCHORS_K, len(ids))
    top = np.argpartition(-sims, k - 1)[:k]
    top = top[np.argsort(-sims[top])]
    floor = float(sims[top[-1]])
    # weight by how far above the k-th best each anchor is, so the nearest dominate
    return [[_node_id(ids[i]), round(float(sims[i]) - floor + 0.01, 4)] for i in top]


def _build_global(recommender, stamp) -> dict:
    movies = _graph_movies()
    ids, matrix = _movie_factor_matrix(recommender, movies)
    payload = {'neighbors': _knn(matrix, ids, TASTE_NEIGHBORS_K), 'users': {}}

    # local site users that have a factor (MovieLens users aren't on the site)
    from django.contrib.auth import get_user_model
    User = get_user_model()
    movie_ct = ContentType.objects.get_for_model(Movie)
    reviewer_ids = set(Review.objects.filter(content_type=movie_ct).values_list('user_id', flat=True))
    for user in User.objects.filter(id__in=reviewer_ids).only('id', 'username', 'profile_picture'):
        anchors = _anchors(recommender.get_user_factor(user.id), matrix, ids)
        if anchors:
            payload['users'][f"user_{user.id}"] = {
                'username': user.username,
                'avatar': user.get_profile_picture(),
                'anchors': anchors,
            }
    cache.set(_global_key(stamp), payload, TASTE_CACHE_TTL)
    return payload


def _build_user(recommender, user, stamp) -> dict:
    movies = _graph_movies()
    ids, matrix = _movie_factor_matrix(recommender, movies)
    user_id_str = f"loc_{user.id}"
    factor = recommender.get_user_factor(user.id)
    # predict_rating needs the cold-start factor explicitly for users not in the index
    override = None if recommender._user_factor(user_id_str) is not None else factor

    predicted = {}
    if factor is not None:
        years = dict(Movie.objects.filter(id__in=[m for m, _ in movies]).values_list('id', 'release_date__year'))
        for movie_id, tmdb in movies:
            est = recommender.predict_rating(user_id_str, int(tmdb), year=years.get(movie_id), user_factor_override=override)
            if est:
                # same clamp + 0-10 display scale as get_recommendations_for_user
                predicted[_node_id(movie_id)] = round(max(0.5, min(5.0, est)) * 2, 1)

    # reuse the Stremio addon's cached recommendations when they're warm
    pick_ids = cache.get(f"stremio_recommendations_{user.id}")
    if pick_ids is None:
        recs = recommender.get_recommendations_for_user(user.id, max_recommendations=PICKS_LIMIT)
        pick_ids = [m.id for m in recs if isinstance(m, Movie)]

    payload = {
        'predicted': predicted,
        'picks': [_node_id(mid) for mid in pick_ids],
        'profile': recommender.get_user_taste_profile(user_id_str) if factor is not None else {},
        'anchors': _anchors(factor, matrix, ids),
    }
    cache.set(_user_key(user.id, stamp), payload, TASTE_CACHE_TTL)
    return payload


def warm_graph_taste(user_id: int) -> None:
    """Django Q task: build (whatever is missing of) the global + per-user taste caches."""
    from django.contrib.auth import get_user_model
    from movies.services.recommendation import get_shared_recommender, model_stamp

    stamp = model_stamp()
    try:
        if stamp is None:
            return
        recommender = get_shared_recommender()
        if not recommender.model_data:
            raise RuntimeError("recommender model failed to load")
        if cache.get(_global_key(stamp)) is None:
            _build_global(recommender, stamp)
        user = get_user_model().objects.filter(id=user_id).first()
        if user and cache.get(_user_key(user_id, stamp)) is None:
            _build_user(recommender, user, stamp)
    except Exception:
        logger.exception("Building graph taste data failed for user_id=%s", user_id)
        # without this the page's polling would re-enqueue a failing task every few seconds
        cache.set(_failed_key(stamp), 1, FAILED_TTL)
    finally:
        cache.delete(f"graph:taste:pending:{user_id}")


def _live_user_state(user) -> dict:
    """Cheap, always-fresh per-user facts: own ratings + watchlist, keyed by node id."""
    movie_ct = ContentType.objects.get_for_model(Movie)
    ratings = {
        _node_id(object_id): rating
        for object_id, rating in Review.objects.filter(user=user, content_type=movie_ct).values_list('object_id', 'rating')
    }
    watchlist = [
        _node_id(object_id)
        for object_id in Watchlist.objects.filter(user=user, content_type=movie_ct).values_list('object_id', flat=True)
    ]
    return {
        'user_node_id': f"user_{user.id}",
        'username': user.username,
        'avatar': user.get_profile_picture(),
        'ratings': ratings,
        'watchlist': watchlist,
    }


def get_taste_payload(user) -> dict:
    """Web-side entry point: cached model data + live user state; never loads the model.

    status: 'ready' (model data included), 'pending' (warm task enqueued, poll again)
    or 'unavailable' (no trained model). Ratings/watchlist are always included so the
    "you" features work even without a model.
    """
    from movies.services.recommendation import model_stamp

    payload = {'me': _live_user_state(user)}
    stamp = model_stamp()
    if stamp is None or cache.get(_failed_key(stamp)):
        payload['status'] = 'unavailable'
        return payload

    global_data = cache.get(_global_key(stamp))
    user_data = cache.get(_user_key(user.id, stamp))
    if global_data is None or user_data is None:
        if cache.add(f"graph:taste:pending:{user.id}", 1, PENDING_TTL):
            from django_q.tasks import async_task
            async_task('movies.services.network_graph.taste.warm_graph_taste', user.id)
        payload['status'] = 'pending'
        return payload

    payload['status'] = 'ready'
    payload['neighbors'] = global_data['neighbors']
    payload['users'] = {k: v for k, v in global_data['users'].items() if k != payload['me']['user_node_id']}
    payload['me'].update(user_data)
    return payload
