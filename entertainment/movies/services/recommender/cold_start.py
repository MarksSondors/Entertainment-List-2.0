"""Content-based cold-start heads.

Item head: ridge regression mapping item content features → iALS item factors.
At inference time, items unseen during training (e.g. movies released after the
last training run) get factors via this regression instead of being skipped.

User head (symmetric): ridge regression mapping a *user's* content profile
(weighted average of the content features of the items they've rated) → iALS
user factors. Lets a brand-new user with only a rating or two get a useful
factor instead of falling back straight to popularity.

Features used (one-hot / numeric, all CPU/numpy):
    - TMDB genre indicator (one column per ``TMDB_GENRES``)
    - Decade indicator (one column per decade present in the catalog)
    - Language indicator (top-K languages, rest -> 'other')
    - Runtime bucket indicator
    - Director indicator (top-K frequent directors, rest -> 'other') — sparse:
      only populated for movies present in the local DB (see data_loading.py)
    - Top-cast indicator (top-K frequent actors, multi-hot up to 3 per movie) — same caveat
    - log1p(vote_count)         (log-scaled popularity)
    - vote_average / 10.0       (rating prior in [0,1])
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from .data_loading import RUNTIME_BUCKETS, TMDB_GENRES, CatalogLookups

logger = logging.getLogger(__name__)


@dataclass
class ColdStartHead:
    coef: np.ndarray                  # (D, F) float32 — feature -> factor
    intercept: np.ndarray             # (F,) float32
    decades: list[int]
    languages: list[str]
    feature_dim: int
    directors: list[int] = None       # top-K director tmdb_ids (+ '__other__' handled via -1 sentinel)
    top_cast: list[int] = None        # top-K actor tmdb_ids

    def __post_init__(self):
        if self.directors is None:
            self.directors = []
        if self.top_cast is None:
            self.top_cast = []

    def predict(self, features: np.ndarray) -> np.ndarray:
        return features @ self.coef + self.intercept


def _feature_dim_offsets(
    *,
    genre_idx: dict, decade_idx: dict, lang_idx: dict, rt_idx: dict,
    director_idx: dict, cast_idx: dict,
) -> dict[str, int]:
    """Column offsets for each feature block, in the fixed order used everywhere
    features are built (training + inference must agree on this layout)."""
    off = {}
    off["genre"] = 0
    off["decade"] = off["genre"] + len(genre_idx)
    off["lang"] = off["decade"] + len(decade_idx)
    off["rt"] = off["lang"] + len(lang_idx)
    off["director"] = off["rt"] + len(rt_idx)
    off["cast"] = off["director"] + len(director_idx)
    off["numeric"] = off["cast"] + len(cast_idx)
    return off


def _content_features_for(
    tmdb_id: int,
    catalog: CatalogLookups,
    *,
    genre_idx: dict[str, int],
    decade_idx: dict[int, int],
    lang_idx: dict[str, int],
    rt_idx: dict[str, int],
    director_idx: dict[int, int],
    cast_idx: dict[int, int],
    feature_dim: int,
) -> np.ndarray:
    x = np.zeros(feature_dim, dtype=np.float32)
    offs = _feature_dim_offsets(
        genre_idx=genre_idx, decade_idx=decade_idx, lang_idx=lang_idx, rt_idx=rt_idx,
        director_idx=director_idx, cast_idx=cast_idx,
    )
    genres = catalog.tmdb_to_genres.get(tmdb_id, []) or []
    for g in genres:
        col = genre_idx.get(g)
        if col is not None:
            x[offs["genre"] + col] = 1.0

    year = catalog.tmdb_to_year.get(tmdb_id)
    if year is not None:
        decade = (int(year) // 10) * 10
        col = decade_idx.get(decade)
        if col is not None:
            x[offs["decade"] + col] = 1.0

    lang = catalog.tmdb_to_language.get(tmdb_id, "en")
    col = lang_idx.get(lang, lang_idx.get("__other__"))
    if col is not None:
        x[offs["lang"] + col] = 1.0

    rb = catalog.tmdb_to_runtime_bucket.get(tmdb_id, "standard")
    col = rt_idx.get(rb, rt_idx.get("standard"))
    if col is not None:
        x[offs["rt"] + col] = 1.0

    if director_idx:
        director = catalog.tmdb_to_director.get(tmdb_id)
        col = director_idx.get(director, director_idx.get("__other__")) if director is not None else None
        if col is not None:
            x[offs["director"] + col] = 1.0

    if cast_idx:
        for actor in catalog.tmdb_to_top_cast.get(tmdb_id, []) or []:
            col = cast_idx.get(actor)
            if col is not None:
                x[offs["cast"] + col] = 1.0

    vote = catalog.tmdb_vote_data.get(tmdb_id)
    if vote is not None:
        avg, count = vote
        x[offs["numeric"]] = float(np.log1p(max(0, count)))
        x[offs["numeric"] + 1] = float(avg) / 10.0
    return x


def fit_cold_start_head(
    item_factors: np.ndarray,
    item_to_idx: dict[int, int],
    catalog: CatalogLookups,
    *,
    ridge_lambda: float = 5.0,
    top_languages: int = 12,
    top_directors: int = 50,
    top_cast: int = 100,
) -> ColdStartHead:
    """Fit ridge regression: content_features -> iALS item factors."""
    # Determine vocabularies
    genre_idx = {g: i for i, g in enumerate(TMDB_GENRES)}
    decades = sorted({(int(y) // 10) * 10 for y in catalog.tmdb_to_year.values()})
    decade_idx = {d: i for i, d in enumerate(decades)}

    lang_counts: dict[str, int] = {}
    for lng in catalog.tmdb_to_language.values():
        lang_counts[lng] = lang_counts.get(lng, 0) + 1
    top = sorted(lang_counts.items(), key=lambda kv: -kv[1])[:top_languages]
    languages = [k for k, _ in top]
    lang_idx = {l: i for i, l in enumerate(languages)}
    lang_idx["__other__"] = len(languages)
    languages_with_other = languages + ["__other__"]

    rt_idx = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}

    # Director / cast vocabularies — sparse (only local-DB movies have these), but
    # still worth a small dedicated block since a repeat director/actor is a strong
    # cold-start signal for niche/new movies with no rating history at all.
    director_counts: dict[int, int] = {}
    for d in catalog.tmdb_to_director.values():
        director_counts[d] = director_counts.get(d, 0) + 1
    top_dir = sorted(director_counts.items(), key=lambda kv: -kv[1])[:top_directors]
    directors = [k for k, _ in top_dir]
    director_idx = {d: i for i, d in enumerate(directors)}
    if directors:
        director_idx["__other__"] = len(directors)

    cast_counts: dict[int, int] = {}
    for cast_list in catalog.tmdb_to_top_cast.values():
        for actor in cast_list or []:
            cast_counts[actor] = cast_counts.get(actor, 0) + 1
    top_actors = sorted(cast_counts.items(), key=lambda kv: -kv[1])[:top_cast]
    cast_list_vocab = [k for k, _ in top_actors]
    cast_idx = {a: i for i, a in enumerate(cast_list_vocab)}

    feature_dim = (
        len(genre_idx) + len(decade_idx) + len(lang_idx) + len(rt_idx)
        + len(director_idx) + len(cast_idx)
        + 2  # +2 for vote count + avg
    )
    logger.info(
        "Cold-start feature dim: %d (G=%d D=%d L=%d R=%d Dir=%d Cast=%d + 2 numeric)",
        feature_dim, len(genre_idx), len(decade_idx), len(lang_idx), len(rt_idx),
        len(director_idx), len(cast_idx),
    )

    # Build training matrix from items the model actually learned factors for
    n = len(item_to_idx)
    X = np.zeros((n, feature_dim), dtype=np.float32)
    Y = np.zeros((n, item_factors.shape[1]), dtype=np.float32)
    for tmdb_id, idx in item_to_idx.items():
        X[idx] = _content_features_for(
            int(tmdb_id), catalog,
            genre_idx=genre_idx, decade_idx=decade_idx,
            lang_idx=lang_idx, rt_idx=rt_idx,
            director_idx=director_idx, cast_idx=cast_idx,
            feature_dim=feature_dim,
        )
        Y[idx] = item_factors[idx]

    # Center Y to absorb mean offset in intercept
    y_mean = Y.mean(axis=0)
    Yc = Y - y_mean

    # Ridge: beta = (X^T X + λI)^-1 X^T Yc
    A = X.T @ X + ridge_lambda * np.eye(feature_dim, dtype=np.float32)
    B = X.T @ Yc
    coef = np.linalg.solve(A, B).astype(np.float32)
    intercept = y_mean.astype(np.float32)

    # Quick sanity log: explained variance of factors per dimension
    pred = X @ coef + intercept
    ss_res = float(np.sum((Y - pred) ** 2))
    ss_tot = float(np.sum((Y - y_mean) ** 2)) + 1e-9
    r2 = 1.0 - ss_res / ss_tot
    logger.info("Cold-start ridge R^2 on training items: %.4f", r2)

    return ColdStartHead(
        coef=coef,
        intercept=intercept,
        decades=decades,
        languages=languages_with_other,
        feature_dim=feature_dim,
        directors=directors,
        top_cast=cast_list_vocab,
    )


def predict_factors(
    head: ColdStartHead,
    tmdb_ids: Iterable[int],
    catalog: CatalogLookups,
) -> np.ndarray:
    """Predict iALS item factors for arbitrary TMDB ids using the trained head."""
    genre_idx = {g: i for i, g in enumerate(TMDB_GENRES)}
    decade_idx = {d: i for i, d in enumerate(head.decades)}
    lang_idx = {l: i for i, l in enumerate(head.languages)}
    rt_idx = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}
    director_idx = {d: i for i, d in enumerate(head.directors)}
    if head.directors:
        director_idx["__other__"] = len(head.directors)
    cast_idx = {a: i for i, a in enumerate(head.top_cast)}

    ids = list(tmdb_ids)
    if not ids:
        return np.zeros((0, head.intercept.shape[0]), dtype=np.float32)
    X = np.zeros((len(ids), head.feature_dim), dtype=np.float32)
    for row, tid in enumerate(ids):
        X[row] = _content_features_for(
            int(tid), catalog,
            genre_idx=genre_idx, decade_idx=decade_idx,
            lang_idx=lang_idx, rt_idx=rt_idx,
            director_idx=director_idx, cast_idx=cast_idx,
            feature_dim=head.feature_dim,
        )
    return head.predict(X)


def blend_item_factors_with_content(
    ranking_item_factors: np.ndarray,
    item_to_idx: dict[int, int],
    cold_start_head: ColdStartHead,
    catalog: CatalogLookups,
    interaction_counts: np.ndarray,
    *,
    k_shrinkage: float = 20.0,
) -> np.ndarray:
    """Hybridize every item's factor with its content-predicted factor, not just
    cold-start items — a lightweight stand-in for a full feature-augmented/LightFM-
    style joint model (which would need a different training loop entirely).

    For each item, shrinkage = n_i / (n_i + k_shrinkage) where n_i is the item's
    training interaction count: well-supported items keep ~their learned factor,
    sparse/niche items lean more on the content-based prediction, and there's no
    hard cliff between "seen during training" (pure collaborative) and "cold-start"
    (pure content) — the blend is continuous in interaction count.

        blended_i = shrinkage_i * ials_i + (1 - shrinkage_i) * content_i

    ``interaction_counts`` must be aligned to ``item_to_idx``'s index order (e.g.
    the per-column nnz count of the training confidence matrix).
    """
    ids_in_key_order = list(item_to_idx.keys())
    content_factors = predict_factors(cold_start_head, ids_in_key_order, catalog)
    # predict_factors iterates in the same order as ids_in_key_order; re-derive the
    # row order actually used by item_to_idx's *values* (indices) to align correctly.
    content_by_idx = np.zeros_like(ranking_item_factors)
    for row, tmdb_id in enumerate(ids_in_key_order):
        content_by_idx[item_to_idx[tmdb_id]] = content_factors[row]

    n = np.asarray(interaction_counts, dtype=np.float32)
    shrinkage = (n / (n + k_shrinkage)).reshape(-1, 1)
    blended = shrinkage * ranking_item_factors + (1.0 - shrinkage) * content_by_idx
    return blended.astype(np.float32)


@dataclass
class UserColdStartHead:
    """Symmetric counterpart to ``ColdStartHead``: maps a user's content profile
    (weighted average of the content features of items they've positively rated)
    to an iALS user factor. Reuses the item head's feature vocabulary so both
    heads agree on layout without re-deriving it.
    """
    coef: np.ndarray       # (D, F) float32
    intercept: np.ndarray  # (F,) float32
    feature_dim: int

    def predict(self, features: np.ndarray) -> np.ndarray:
        return features @ self.coef + self.intercept


def _user_profile_features(
    rated_tmdb_ids: list[int],
    rating_weights: list[float],
    catalog: CatalogLookups,
    item_cold_start_head: ColdStartHead,
) -> np.ndarray:
    """Weighted average of item content-feature vectors, weighted by (positive)
    rating strength — approximates "what kind of content this user responds to"
    from as few as 1-2 ratings.
    """
    genre_idx = {g: i for i, g in enumerate(TMDB_GENRES)}
    decade_idx = {d: i for i, d in enumerate(item_cold_start_head.decades)}
    lang_idx = {l: i for i, l in enumerate(item_cold_start_head.languages)}
    rt_idx = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}
    director_idx = {d: i for i, d in enumerate(item_cold_start_head.directors)}
    if item_cold_start_head.directors:
        director_idx["__other__"] = len(item_cold_start_head.directors)
    cast_idx = {a: i for i, a in enumerate(item_cold_start_head.top_cast)}

    if not rated_tmdb_ids:
        return np.zeros(item_cold_start_head.feature_dim, dtype=np.float32)

    acc = np.zeros(item_cold_start_head.feature_dim, dtype=np.float32)
    total_w = 0.0
    for tmdb_id, w in zip(rated_tmdb_ids, rating_weights):
        vec = _content_features_for(
            int(tmdb_id), catalog,
            genre_idx=genre_idx, decade_idx=decade_idx, lang_idx=lang_idx, rt_idx=rt_idx,
            director_idx=director_idx, cast_idx=cast_idx,
            feature_dim=item_cold_start_head.feature_dim,
        )
        acc += w * vec
        total_w += w
    if total_w > 0:
        acc /= total_w
    return acc


def fit_user_cold_start_head(
    user_factors: np.ndarray,
    user_to_idx: dict[str, int],
    ratings_df,  # pd.DataFrame with columns user_id, tmdb_id, rating (positives only, recommended)
    catalog: CatalogLookups,
    item_cold_start_head: ColdStartHead,
    *,
    ridge_lambda: float = 5.0,
    max_items_per_user: int = 50,
) -> UserColdStartHead:
    """Fit ridge regression: user content-profile -> iALS user factor.

    Lets a brand-new user with only a handful of ratings get a useful factor
    (instead of falling straight back to popularity) by predicting what their
    factor "would look like" given the kinds of movies they rated highly.
    """
    import pandas as pd

    feature_dim = item_cold_start_head.feature_dim
    n = len(user_to_idx)
    X = np.zeros((n, feature_dim), dtype=np.float32)
    Y = np.zeros((n, user_factors.shape[1]), dtype=np.float32)

    grouped = ratings_df.groupby("user_id", sort=False)
    filled = 0
    for user_id, idx in user_to_idx.items():
        try:
            group = grouped.get_group(user_id)
        except KeyError:
            continue
        if len(group) > max_items_per_user:
            group = group.nlargest(max_items_per_user, "rating")
        rated_ids = group["tmdb_id"].tolist()
        weights = [max(float(r), 0.1) for r in group["rating"].tolist()]
        X[idx] = _user_profile_features(rated_ids, weights, catalog, item_cold_start_head)
        Y[idx] = user_factors[idx]
        filled += 1

    y_mean = Y.mean(axis=0)
    Yc = Y - y_mean
    A = X.T @ X + ridge_lambda * np.eye(feature_dim, dtype=np.float32)
    B = X.T @ Yc
    coef = np.linalg.solve(A, B).astype(np.float32)
    intercept = y_mean.astype(np.float32)

    pred = X @ coef + intercept
    ss_res = float(np.sum((Y - pred) ** 2))
    ss_tot = float(np.sum((Y - y_mean) ** 2)) + 1e-9
    r2 = 1.0 - ss_res / ss_tot
    logger.info("User cold-start ridge R^2: %.4f (%d/%d users had a rating profile)", r2, filled, n)

    return UserColdStartHead(coef=coef, intercept=intercept, feature_dim=feature_dim)


def predict_user_factor(
    head: UserColdStartHead,
    rated_tmdb_ids: list[int],
    rating_weights: list[float],
    catalog: CatalogLookups,
    item_cold_start_head: ColdStartHead,
) -> np.ndarray:
    """Predict a factor for a user given their (partial) rating history."""
    profile = _user_profile_features(rated_tmdb_ids, rating_weights, catalog, item_cold_start_head)
    return head.predict(profile[None, :])[0]
