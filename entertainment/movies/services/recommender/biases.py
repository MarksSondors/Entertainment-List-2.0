"""Bias decomposition.

Hierarchy preserved from the legacy trainer:

    rating ≈ global + b_year[y] + b_item[i] + b_user[u]
             + b_user_genre[u, g (multi-hot)] + b_user_decade[u, d]
             + b_user_lang[u, l] + b_user_runtime[u, r]

Differences from legacy:
- The four user-conditioned category bias dicts are solved **jointly** per user
  via a single weighted ridge regression. Eliminates pass-order dependence and
  the 3-iteration convergence loop.
- Optional per-user mean centering (off by default) absorbs the ML 0.5–5 vs
  local 0–10/2 scale heterogeneity.
"""
from __future__ import annotations

import gc
import logging

import numpy as np
import pandas as pd

from .data_loading import RUNTIME_BUCKETS, TMDB_GENRES

logger = logging.getLogger(__name__)


def extrapolate_year_biases(year_biases: dict[int, float], max_future_year: int = 2030) -> dict[int, float]:
    """Forward-fill year biases past the training data using the avg of last 5 known years."""
    known = sorted(y for y in year_biases if y >= 1950)
    if not known:
        return year_biases
    max_known = max(known)
    recent = [year_biases[y] for y in known if y >= max_known - 4]
    avg = float(np.mean(recent)) if recent else 0.0
    for y in range(max_known + 1, max_future_year + 1):
        year_biases[y] = avg
    if max_future_year > max_known:
        logger.info("Extrapolated year biases %d-%d (avg=%.4f)", max_known + 1, max_future_year, avg)
    return year_biases


def _damped_groupby_mean(values: np.ndarray, weights: np.ndarray, group: pd.Series, damping: float) -> dict:
    """sum(w * v) / (sum(w) + damping), grouped by ``group`` index."""
    weighted_v = pd.Series(values * weights, index=group.index)
    w = pd.Series(weights, index=group.index)
    num = weighted_v.groupby(group).sum()
    den = w.groupby(group).sum() + damping
    return (num / den).to_dict()


def compute_base_biases(
    df: pd.DataFrame,
    weights: np.ndarray,
    damping: float,
) -> tuple[float, dict, dict, dict, np.ndarray]:
    """Compute global / year / item / user biases sequentially with damping.

    Returns (global_mean, year_biases, item_biases, user_biases, base_residual)
    where ``base_residual = rating - global - year - item - user`` (unweighted).
    """
    rating = df["rating"].values.astype(np.float32)
    weights = weights.astype(np.float32)

    global_mean = float(np.average(rating, weights=weights))

    year_resid = rating - global_mean
    year_biases = _damped_groupby_mean(year_resid, weights, df["year"], damping)
    year_biases = {int(k): float(v) for k, v in year_biases.items()}
    year_biases = extrapolate_year_biases(year_biases)
    y_bias = df["year"].map(year_biases).fillna(0).astype(np.float32).values

    item_resid = rating - global_mean - y_bias
    item_biases = _damped_groupby_mean(item_resid, weights, df["tmdb_id"], damping)
    item_biases = {int(k): float(v) for k, v in item_biases.items()}
    i_bias = df["tmdb_id"].map(item_biases).fillna(0).astype(np.float32).values

    user_resid = rating - global_mean - y_bias - i_bias
    user_biases = _damped_groupby_mean(user_resid, weights, df["user_id"], damping)
    user_biases = {str(k): float(v) for k, v in user_biases.items()}
    u_bias = df["user_id"].map(user_biases).fillna(0).astype(np.float32).values

    base_residual = (rating - global_mean - y_bias - i_bias - u_bias).astype(np.float32)
    return global_mean, year_biases, item_biases, user_biases, base_residual


def _build_feature_blocks(df: pd.DataFrame) -> tuple[np.ndarray, list[tuple[str, list]]]:
    """Build the dense per-row feature matrix X (N, F) for category biases.

    Returns:
        X (N, F) float32 with columns:
            [genre[0]..genre[G-1], decade[0]..decade[D-1],
             lang[0]..lang[L-1],   runtime[0]..runtime[R-1]]
        block_spec: ordered list of (block_name, list_of_keys) describing the column ranges.
    """
    n = len(df)

    # Genres (multi-hot, restricted to TMDB_GENRES so the feature space is fixed)
    genre_idx = {g: i for i, g in enumerate(TMDB_GENRES)}
    genre_block = np.zeros((n, len(TMDB_GENRES)), dtype=np.float32)
    for row_i, genres in enumerate(df["genres"].values):
        if genres:
            for g in genres:
                col = genre_idx.get(g)
                if col is not None:
                    genre_block[row_i, col] = 1.0

    # Decades
    decades = sorted(int(d) for d in df["decade"].unique())
    decade_idx = {d: i for i, d in enumerate(decades)}
    decade_block = np.zeros((n, len(decades)), dtype=np.float32)
    decade_block[np.arange(n), df["decade"].astype(int).map(decade_idx).values] = 1.0

    # Languages — restrict to those with >= 100 ratings, others fold into 'other'
    lang_counts = df["language"].value_counts()
    keep_langs = list(lang_counts[lang_counts >= 100].index)
    lang_idx = {lng: i for i, lng in enumerate(keep_langs)}
    lang_block = np.zeros((n, len(keep_langs)), dtype=np.float32)
    if keep_langs:
        col_idx = df["language"].map(lang_idx)
        valid = col_idx.notna()
        lang_block[np.where(valid)[0], col_idx[valid].astype(int).values] = 1.0

    # Runtime buckets
    rt_idx = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}
    rt_block = np.zeros((n, len(RUNTIME_BUCKETS)), dtype=np.float32)
    rt_block[np.arange(n), df["runtime_bucket"].map(rt_idx).fillna(rt_idx["standard"]).astype(int).values] = 1.0

    X = np.concatenate([genre_block, decade_block, lang_block, rt_block], axis=1)
    spec = [
        ("genre", list(TMDB_GENRES)),
        ("decade", decades),
        ("language", keep_langs),
        ("runtime", list(RUNTIME_BUCKETS)),
    ]
    return X, spec


def compute_user_category_biases_joint(
    df: pd.DataFrame,
    base_residual: np.ndarray,
    weights: np.ndarray,
    ridge_lambda: float = 10.0,
) -> tuple[dict, dict, dict, dict]:
    """Per-user joint weighted-ridge solve for the four category bias dictionaries.

    For each user u, we solve:
        min_w  || sqrt(W_u) (X_u w - r_u) ||^2 + λ ||w||^2
    where X_u is the per-row feature matrix restricted to that user's ratings,
    r_u is base_residual restricted to u, and W_u is diag(weights).
    """
    logger.info("Building category feature matrix...")
    X, spec = _build_feature_blocks(df)
    F = X.shape[1]
    logger.info("Feature matrix: shape=%s, ridge_lambda=%.2f", X.shape, ridge_lambda)

    # Sort rows by user to enable contiguous slicing (much faster than groupby in tight loop)
    order = np.argsort(df["user_id"].values, kind="stable")
    user_sorted = df["user_id"].values[order]
    X_sorted = X[order]
    r_sorted = base_residual[order].astype(np.float32)
    w_sorted = weights[order].astype(np.float32)

    # Find group boundaries
    boundaries = np.concatenate([
        [0],
        np.where(user_sorted[1:] != user_sorted[:-1])[0] + 1,
        [len(user_sorted)],
    ])
    user_ids_unique = user_sorted[boundaries[:-1]]

    # Output: weights per user (n_users, F)
    n_users = len(user_ids_unique)
    user_weights = np.zeros((n_users, F), dtype=np.float32)
    eye = ridge_lambda * np.eye(F, dtype=np.float32)

    log_every = max(n_users // 10, 1)
    for u_idx in range(n_users):
        s, e = boundaries[u_idx], boundaries[u_idx + 1]
        Xu = X_sorted[s:e]
        ru = r_sorted[s:e]
        wu = w_sorted[s:e]

        # Weighted normal equations:  (X^T diag(w) X + λI) β = X^T diag(w) r
        WX = Xu * wu[:, None]
        A = WX.T @ Xu + eye
        b = WX.T @ ru
        try:
            beta = np.linalg.solve(A, b)
        except np.linalg.LinAlgError:
            beta = np.linalg.lstsq(A, b, rcond=None)[0]
        user_weights[u_idx] = beta

        if (u_idx + 1) % log_every == 0:
            logger.info("  ridge progress %d/%d users", u_idx + 1, n_users)

    # Unpack into per-block dicts mirroring the legacy export shape
    user_genre_biases: dict[str, dict] = {}
    user_decade_biases: dict[int, dict] = {}
    user_language_biases: dict[str, dict] = {}
    user_runtime_biases: dict[str, dict] = {}

    col = 0
    for block_name, keys in spec:
        for ki, key in enumerate(keys):
            col_values = user_weights[:, col + ki]
            mapping = {
                str(uid): float(v)
                for uid, v in zip(user_ids_unique, col_values)
                if abs(float(v)) > 1e-6
            }
            if block_name == "genre":
                user_genre_biases[str(key)] = mapping
            elif block_name == "decade":
                user_decade_biases[int(key)] = mapping
            elif block_name == "language":
                user_language_biases[str(key)] = mapping
            elif block_name == "runtime":
                user_runtime_biases[str(key)] = mapping
        col += len(keys)

    del X, X_sorted, r_sorted, w_sorted, user_weights
    gc.collect()
    return user_genre_biases, user_decade_biases, user_language_biases, user_runtime_biases


def compute_all_biases(
    df: pd.DataFrame,
    weights: np.ndarray,
    damping: float = 5.0,
    ridge_lambda: float = 10.0,
) -> dict:
    """One-call wrapper returning the full bias bundle."""
    global_mean, year_biases, item_biases, user_biases, base_residual = compute_base_biases(
        df, weights, damping
    )
    g, d, l, r = compute_user_category_biases_joint(df, base_residual, weights, ridge_lambda)
    return {
        "global_mean": global_mean,
        "year_biases": year_biases,
        "item_biases": item_biases,
        "user_biases": user_biases,
        "user_genre_biases": g,
        "user_decade_biases": d,
        "user_language_biases": l,
        "user_runtime_biases": r,
    }
