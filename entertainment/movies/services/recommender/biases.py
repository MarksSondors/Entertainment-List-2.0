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
import os

import numpy as np
import pandas as pd
import psutil
from scipy import sparse

from .data_loading import RUNTIME_BUCKETS, TMDB_GENRES

logger = logging.getLogger(__name__)

# Users per chunk when batch-solving the per-user ridge systems (see
# ``compute_user_category_biases_joint``). Bounds the transient (chunk, F, F)
# buffer to a few hundred MB even when F is large, while still amortizing the
# Python/LAPACK call overhead of ``np.linalg.solve`` across many users at once.
_RIDGE_SOLVE_CHUNK = 4096


def _log_rss(label: str) -> None:
    """Best-effort process RSS logger — cheap checkpoints around the memory-heavy
    steps of bias computation so future regressions show up in logs instead of
    a silent OOM kill."""
    try:
        mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
        logger.info("[biases:%s] RSS = %.1f MB", label, mb)
    except Exception:
        pass


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


def build_feature_blocks(df: pd.DataFrame) -> tuple[sparse.csr_matrix, list[tuple[str, list]]]:
    """Build the sparse per-row feature matrix X (N, F) for category biases.

    Built sparse (instead of dense) because N is the full rating count — for
    the MovieLens-32M dataset a dense (N, F) float32 matrix is tens of GB and
    reliably OOM-kills the training process. Each row only has a handful of
    nonzero entries (a few genres + 1 decade + 1 language + 1 runtime bucket),
    so a CSR matrix is orders of magnitude smaller.

    Returns:
        X (N, F) sparse CSR float32 with columns:
            [genre[0]..genre[G-1], decade[0]..decade[D-1],
             lang[0]..lang[L-1],   runtime[0]..runtime[R-1]]
        block_spec: ordered list of (block_name, list_of_keys) describing the column ranges.
    """
    n = len(df)

    def _onehot_block(codes: np.ndarray, n_cols: int) -> sparse.csr_matrix:
        valid = codes >= 0
        rows = np.where(valid)[0]
        cols = codes[valid]
        data = np.ones(len(rows), dtype=np.float32)
        return sparse.csr_matrix((data, (rows, cols)), shape=(n, n_cols))

    # Genres (multi-hot, restricted to TMDB_GENRES so the feature space is fixed)
    genre_idx = {g: i for i, g in enumerate(TMDB_GENRES)}
    genre_rows: list[int] = []
    genre_cols: list[int] = []
    for row_i, genres in enumerate(df["genres"].values):
        # ``genres`` may be a Python list (fresh load) or a numpy array (round-tripped
        # through the Parquet dataset cache, which deserializes list columns as
        # ndarrays) — ``if genres:`` raises ValueError on a multi-element ndarray,
        # so check length explicitly instead of relying on truthiness.
        if genres is not None and len(genres) > 0:
            for g in genres:
                col = genre_idx.get(g)
                if col is not None:
                    genre_rows.append(row_i)
                    genre_cols.append(col)
    genre_block = sparse.csr_matrix(
        (np.ones(len(genre_rows), dtype=np.float32), (genre_rows, genre_cols)),
        shape=(n, len(TMDB_GENRES)),
    )

    # Decades
    decades = sorted(int(d) for d in df["decade"].unique())
    decade_idx = {d: i for i, d in enumerate(decades)}
    decade_codes = df["decade"].astype(int).map(decade_idx).values.astype(np.int64)
    decade_block = _onehot_block(decade_codes, len(decades))

    # Languages — restrict to those with >= 100 ratings, others fold into 'other'
    lang_counts = df["language"].value_counts()
    keep_langs = list(lang_counts[lang_counts >= 100].index)
    lang_idx = {lng: i for i, lng in enumerate(keep_langs)}
    if keep_langs:
        lang_codes = df["language"].map(lang_idx)
        lang_codes = lang_codes.where(lang_codes.notna(), -1).astype(np.int64).values
        lang_block = _onehot_block(lang_codes, len(keep_langs))
    else:
        lang_block = sparse.csr_matrix((n, 0), dtype=np.float32)

    # Runtime buckets
    rt_idx = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}
    rt_codes = df["runtime_bucket"].map(rt_idx).fillna(rt_idx["standard"]).astype(np.int64).values
    rt_block = _onehot_block(rt_codes, len(RUNTIME_BUCKETS))

    X = sparse.hstack([genre_block, decade_block, lang_block, rt_block], format="csr", dtype=np.float32)
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
    X, spec = build_feature_blocks(df)
    F = X.shape[1]
    logger.info("Feature matrix: shape=%s, nnz=%d, ridge_lambda=%.2f", X.shape, X.nnz, ridge_lambda)
    _log_rss("after feature matrix")

    # Sort rows by user to enable contiguous slicing (much faster than groupby in tight loop).
    # X stays sparse throughout — only the small per-user slice is densified in the loop below,
    # since a dense (N, F) copy of the full matrix is what caused OOM kills on ml-32m.
    # .to_numpy() materializes the categorical user_id column's actual string labels
    # (not the int codes) so sorting/grouping semantics match the pre-categorical behavior.
    user_id_arr = df["user_id"].to_numpy()
    order = np.argsort(user_id_arr, kind="stable")
    user_sorted = user_id_arr[order]
    X_sorted = X[order]
    r_sorted = base_residual[order].astype(np.float32)
    w_sorted = weights[order].astype(np.float32)
    del X
    gc.collect()

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

    # Ridge solves are batched in chunks of ``_RIDGE_SOLVE_CHUNK`` users: building each
    # user's (F, F) normal-equation matrix still requires a per-user loop (row counts are
    # ragged), but a single vectorized np.linalg.solve call per chunk amortizes the
    # Python/LAPACK call overhead of hundreds of thousands of individual solve() calls.
    # Chunking (rather than solving all users at once) bounds the transient (chunk, F, F)
    # buffer to a fixed size regardless of how many users there are.
    log_every = max(n_users // 10, 1)
    logged_through = 0
    for chunk_start in range(0, n_users, _RIDGE_SOLVE_CHUNK):
        chunk_end = min(chunk_start + _RIDGE_SOLVE_CHUNK, n_users)
        chunk_len = chunk_end - chunk_start

        A_batch = np.empty((chunk_len, F, F), dtype=np.float32)
        b_batch = np.empty((chunk_len, F), dtype=np.float32)
        for local_idx, u_idx in enumerate(range(chunk_start, chunk_end)):
            s, e = boundaries[u_idx], boundaries[u_idx + 1]
            Xu = X_sorted[s:e].toarray()  # small per-user slice — safe to densify
            ru = r_sorted[s:e]
            wu = w_sorted[s:e]

            # Weighted normal equations:  (X^T diag(w) X + λI) β = X^T diag(w) r
            WX = Xu * wu[:, None]
            A_batch[local_idx] = WX.T @ Xu + eye
            b_batch[local_idx] = WX.T @ ru

        try:
            # np.linalg.solve's batched gufunc requires b to have the same ndim as a
            # (i.e. shape (chunk, F, K), not (chunk, F)) — a bare (chunk, F) b_batch
            # gets misread as a single (chunk, F) matrix rather than `chunk` stacked
            # F-vectors, causing a core-dimension mismatch. Add/remove a trailing
            # K=1 axis to force the correct batched-vector interpretation.
            beta_batch = np.linalg.solve(A_batch, b_batch[..., None])[..., 0]
        except np.linalg.LinAlgError:
            # Rare (ridge regularization keeps A positive-definite in practice) — fall
            # back to solving the chunk row-by-row so a single ill-conditioned user
            # doesn't force lstsq on the whole chunk.
            beta_batch = np.empty_like(b_batch)
            for local_idx in range(chunk_len):
                try:
                    beta_batch[local_idx] = np.linalg.solve(A_batch[local_idx], b_batch[local_idx])
                except np.linalg.LinAlgError:
                    beta_batch[local_idx] = np.linalg.lstsq(
                        A_batch[local_idx], b_batch[local_idx], rcond=None
                    )[0]

        user_weights[chunk_start:chunk_end] = beta_batch

        if chunk_end - logged_through >= log_every:
            logger.info("  ridge progress %d/%d users", chunk_end, n_users)
            logged_through = chunk_end

    _log_rss("after ridge solve")

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

    del X_sorted, r_sorted, w_sorted, user_weights
    gc.collect()
    return user_genre_biases, user_decade_biases, user_language_biases, user_runtime_biases


def compute_user_time_trend_biases(
    df: pd.DataFrame,
    base_residual: np.ndarray,
    weights: np.ndarray,
    ridge_lambda: float = 10.0,
) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    """Per-user linear taste-drift term: rating ≈ ... + slope_u * t_norm.

    ``time_decay()`` (see weights.py) downweights *old* ratings but doesn't model
    that a user's taste can genuinely shift over their rating history — this adds
    a small per-user linear trend on top of the static per-user bias.

    For each user, ``t_norm`` is their rating timestamp min-max normalized to
    [-0.5, 0.5] over that user's own rating history (so the trend is relative to
    *their* timeline, not a global one), and the slope is a 1-D weighted ridge fit:

        slope_u = sum(w_i * t_i * r_i) / (sum(w_i * t_i^2) + ridge_lambda)

    A closed form (rather than the batched matrix solve used for category biases)
    is sufficient since there's only one feature. Fit independently of the category
    joint-ridge above (both regress on the same ``base_residual`` in parallel rather
    than jointly) — a reasonable approximation that keeps this additive and cheap.

    Returns (user_time_trend: user_id -> slope, user_time_norm: user_id -> (t_min, t_max))
    used together at prediction time: contribution = slope_u * clip(norm(t), ...).
    """
    user_id_arr = df["user_id"].to_numpy()
    ts = df["timestamp"].to_numpy().astype(np.float64)

    g = pd.DataFrame({"user_id": user_id_arr, "ts": ts})
    t_min = g.groupby("user_id")["ts"].transform("min").to_numpy()
    t_max = g.groupby("user_id")["ts"].transform("max").to_numpy()
    span = np.where(t_max > t_min, t_max - t_min, 1.0)
    t_norm = ((ts - t_min) / span - 0.5).astype(np.float32)

    num = pd.Series(t_norm * weights * base_residual, index=g.index).groupby(user_id_arr).sum()
    den = pd.Series((t_norm ** 2) * weights, index=g.index).groupby(user_id_arr).sum() + ridge_lambda
    slope = (num / den)

    user_time_trend = {str(k): float(v) for k, v in slope.to_dict().items() if abs(v) > 1e-6}

    norm_df = pd.DataFrame({"user_id": user_id_arr, "t_min": t_min, "t_max": t_max})
    norm_first = norm_df.drop_duplicates("user_id").set_index("user_id")
    user_time_norm = {
        str(uid): (float(row.t_min), float(row.t_max))
        for uid, row in norm_first.iterrows()
    }
    logger.info("Computed time-trend slopes for %d users (%d non-trivial)",
                len(user_time_norm), len(user_time_trend))
    return user_time_trend, user_time_norm


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
    user_time_trend, user_time_norm = compute_user_time_trend_biases(
        df, base_residual, weights, ridge_lambda
    )
    return {
        "global_mean": global_mean,
        "year_biases": year_biases,
        "item_biases": item_biases,
        "user_biases": user_biases,
        "user_genre_biases": g,
        "user_decade_biases": d,
        "user_language_biases": l,
        "user_runtime_biases": r,
        "user_time_trend": user_time_trend,
        "user_time_norm": user_time_norm,
    }
