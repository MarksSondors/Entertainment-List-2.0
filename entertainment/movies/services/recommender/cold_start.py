"""Content-based cold-start head.

Trains a ridge regression mapping item content features → iALS item factors.
At inference time, items unseen during training (e.g. movies released after the
last training run) get factors via this regression instead of being skipped.

Features used (one-hot / numeric, all CPU/numpy):
    - TMDB genre indicator (one column per ``TMDB_GENRES``)
    - Decade indicator (one column per decade present in the catalog)
    - Language indicator (top-K languages, rest -> 'other')
    - Runtime bucket indicator
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

    def predict(self, features: np.ndarray) -> np.ndarray:
        return features @ self.coef + self.intercept


def _content_features_for(
    tmdb_id: int,
    catalog: CatalogLookups,
    *,
    genre_idx: dict[str, int],
    decade_idx: dict[int, int],
    lang_idx: dict[str, int],
    rt_idx: dict[str, int],
    feature_dim: int,
) -> np.ndarray:
    x = np.zeros(feature_dim, dtype=np.float32)
    genres = catalog.tmdb_to_genres.get(tmdb_id, []) or []
    for g in genres:
        col = genre_idx.get(g)
        if col is not None:
            x[col] = 1.0
    g_off = len(genre_idx)

    year = catalog.tmdb_to_year.get(tmdb_id)
    if year is not None:
        decade = (int(year) // 10) * 10
        col = decade_idx.get(decade)
        if col is not None:
            x[g_off + col] = 1.0
    d_off = g_off + len(decade_idx)

    lang = catalog.tmdb_to_language.get(tmdb_id, "en")
    col = lang_idx.get(lang, lang_idx.get("__other__"))
    if col is not None:
        x[d_off + col] = 1.0
    l_off = d_off + len(lang_idx)

    rb = catalog.tmdb_to_runtime_bucket.get(tmdb_id, "standard")
    col = rt_idx.get(rb, rt_idx.get("standard"))
    if col is not None:
        x[l_off + col] = 1.0
    r_off = l_off + len(rt_idx)

    vote = catalog.tmdb_vote_data.get(tmdb_id)
    if vote is not None:
        avg, count = vote
        x[r_off] = float(np.log1p(max(0, count)))
        x[r_off + 1] = float(avg) / 10.0
    return x


def fit_cold_start_head(
    item_factors: np.ndarray,
    item_to_idx: dict[int, int],
    catalog: CatalogLookups,
    *,
    ridge_lambda: float = 5.0,
    top_languages: int = 12,
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

    feature_dim = (
        len(genre_idx) + len(decade_idx) + len(lang_idx) + len(rt_idx) + 2  # +2 for vote count + avg
    )
    logger.info("Cold-start feature dim: %d (G=%d D=%d L=%d R=%d + 2 numeric)",
                feature_dim, len(genre_idx), len(decade_idx), len(lang_idx), len(rt_idx))

    # Build training matrix from items the model actually learned factors for
    n = len(item_to_idx)
    X = np.zeros((n, feature_dim), dtype=np.float32)
    Y = np.zeros((n, item_factors.shape[1]), dtype=np.float32)
    for tmdb_id, idx in item_to_idx.items():
        X[idx] = _content_features_for(
            int(tmdb_id), catalog,
            genre_idx=genre_idx, decade_idx=decade_idx,
            lang_idx=lang_idx, rt_idx=rt_idx, feature_dim=feature_dim,
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

    ids = list(tmdb_ids)
    if not ids:
        return np.zeros((0, head.intercept.shape[0]), dtype=np.float32)
    X = np.zeros((len(ids), head.feature_dim), dtype=np.float32)
    for row, tid in enumerate(ids):
        X[row] = _content_features_for(
            int(tid), catalog,
            genre_idx=genre_idx, decade_idx=decade_idx,
            lang_idx=lang_idx, rt_idx=rt_idx, feature_dim=head.feature_dim,
        )
    return head.predict(X)
