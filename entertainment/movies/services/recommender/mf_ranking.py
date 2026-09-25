"""iALS ranking head with optional CUDA.

Ranking-time scores come from a proper implicit-feedback ALS. Positive
interactions are ratings >= ``positive_threshold``; their confidence is defined by
``weights.ConfidenceRecipe`` (shared with the per-user fold-in).

GPU is opt-in via ``use_gpu=True``. Factors are *always* coerced to plain numpy
arrays before returning so the pickle is loadable on CPU-only hosts (no CuPy
or ``implicit.gpu.Matrix`` references).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix, csr_matrix

from .weights import ConfidenceRecipe

logger = logging.getLogger(__name__)


@dataclass
class RankingModel:
    user_factors: np.ndarray   # (n_users, k) float32
    item_factors: np.ndarray   # (n_items, k) float32
    user_to_idx: dict[str, int]
    item_to_idx: dict[int, int]
    factors: int
    regularization: float
    iterations: int
    alpha: float
    positive_threshold: float
    trained_with_gpu: bool
    model_type: str = "ials"  # "ials" or "bpr" — see train_bpr()


def _gpu_available() -> bool:
    """Return True iff implicit's CUDA backend is present on this host."""
    try:
        import implicit.gpu  # noqa: F401
        return bool(getattr(__import__("implicit.gpu", fromlist=["HAS_CUDA"]), "HAS_CUDA", False))
    except Exception:
        return False


def gpu_diagnostics() -> dict:
    """Detailed GPU availability check. Returns a dict explaining each gate."""
    info: dict = {
        "implicit_gpu_module": False,
        "implicit_has_cuda": False,
        "cupy_importable": False,
        "cupy_runtime_ok": False,
        "device_count": 0,
        "error": None,
    }
    try:
        import implicit.gpu as ig
        info["implicit_gpu_module"] = True
        info["implicit_has_cuda"] = bool(getattr(ig, "HAS_CUDA", False))
    except Exception as e:
        info["error"] = f"implicit.gpu import failed: {e}"
        return info
    try:
        import cupy  # type: ignore
        info["cupy_importable"] = True
        try:
            info["device_count"] = int(cupy.cuda.runtime.getDeviceCount())
            info["cupy_runtime_ok"] = info["device_count"] > 0
        except Exception as e:
            info["error"] = f"cupy CUDA runtime error: {e}"
    except Exception as e:
        info["error"] = f"cupy import failed: {e}"
    return info


def _single_threaded_blas():
    """implicit parallelizes over users/items itself; a multi-threaded BLAS underneath
    oversubscribes the cores (implicit warns about this at fit time)."""
    try:
        from threadpoolctl import threadpool_limits
        return threadpool_limits(1, "blas")
    except ImportError:
        import contextlib
        return contextlib.nullcontext()


def _to_numpy(arr) -> np.ndarray:
    """Coerce factors to ``np.ndarray`` regardless of the implicit backend.

    ``implicit.gpu.Matrix`` exposes ``.to_numpy()``; CuPy arrays expose ``.get()``.
    Falling back to ``np.asarray`` handles plain numpy.
    """
    if hasattr(arr, "to_numpy"):
        arr = arr.to_numpy()
    elif hasattr(arr, "get") and not isinstance(arr, np.ndarray):
        try:
            arr = arr.get()
        except Exception:
            pass
    out = np.asarray(arr, dtype=np.float32)
    assert isinstance(out, np.ndarray), f"factor coercion failed, got {type(arr)}"
    return out


def build_confidence_matrix(
    df: pd.DataFrame,
    *,
    recipe: Optional[ConfidenceRecipe] = None,
    watchlist_df: Optional[pd.DataFrame] = None,
) -> tuple[csr_matrix, dict[str, int], dict[int, int]]:
    """Build a (n_users, n_items) confidence-weighted CSR for iALS.

    Only rows with rating >= ``recipe.positive_threshold`` are kept (implicit
    positives); their confidence comes from ``ConfidenceRecipe.rating_confidence``.

    ``watchlist_df`` (columns ``user_id``, ``tmdb_id`` — see
    ``data_loading.load_watchlist_pairs``) adds extra low-confidence implicit
    positives at ``recipe.watchlist_confidence``, scoped to pairs whose user *and*
    item already appear in the rating-derived vocabulary. Where a pair is both
    rated-positive and watchlisted, confidences add (``sum_duplicates``).
    """
    recipe = recipe or ConfidenceRecipe()
    pos = df[df["rating"] >= recipe.positive_threshold]
    if pos.empty:
        raise ValueError(f"No positive interactions at threshold={recipe.positive_threshold}")

    # astype(str) materializes user_id's string labels (df may store it as categorical).
    user_series = pos["user_id"].astype(str)
    user_ids = user_series.to_numpy()
    item_ids = pos["tmdb_id"].to_numpy()

    user_to_idx = {u: i for i, u in enumerate(pd.unique(user_ids))}
    item_to_idx = {int(t): i for i, t in enumerate(pd.unique(item_ids))}

    u_idx = user_series.map(user_to_idx).to_numpy(dtype=np.int32)
    i_idx = pos["tmdb_id"].map(item_to_idx).to_numpy(dtype=np.int32)

    timestamps = pos["timestamp"].to_numpy() if "timestamp" in pos.columns else None
    confidence = recipe.rating_confidence(
        pos["rating"].to_numpy(dtype=np.float32),
        is_local=user_series.str.startswith("loc_").to_numpy(),
        timestamps=timestamps,
    )

    n_users = len(user_to_idx)
    n_items = len(item_to_idx)

    rows, cols, vals = [u_idx], [i_idx], [confidence]
    if watchlist_df is not None and not watchlist_df.empty:
        wl = watchlist_df[
            watchlist_df["user_id"].isin(user_to_idx) & watchlist_df["tmdb_id"].isin(item_to_idx)
        ]
        if not wl.empty:
            rows.append(wl["user_id"].map(user_to_idx).to_numpy(dtype=np.int32))
            cols.append(wl["tmdb_id"].map(item_to_idx).to_numpy(dtype=np.int32))
            vals.append(np.full(len(wl), recipe.watchlist_confidence, dtype=np.float32))
            logger.info("Adding %d watchlist positives to the confidence matrix", len(wl))

    R = coo_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
        shape=(n_users, n_items), dtype=np.float32,
    ).tocsr()
    R.sum_duplicates()
    logger.info("iALS confidence matrix: %d users x %d items, nnz=%d", n_users, n_items, R.nnz)
    return R, user_to_idx, item_to_idx


def train_ials(
    R_user_item: csr_matrix,
    user_to_idx: dict[str, int],
    item_to_idx: dict[int, int],
    *,
    factors: int = 64,
    regularization: float = 0.05,
    iterations: int = 20,
    alpha: float = 1.0,
    use_gpu: bool = False,
    positive_threshold: float = 3.5,
    random_state: int = 42,
) -> RankingModel:
    """Fit ``implicit.AlternatingLeastSquares`` and return a numpy-only ``RankingModel``.

    ``alpha`` here is implicit's outer multiplier (additional scaling on top of the
    confidence matrix). Confidence per row is already shaped by
    ``build_confidence_matrix``; the outer alpha is left at 1.0 by default.
    """
    from implicit.als import AlternatingLeastSquares

    requested_gpu = use_gpu
    if requested_gpu and not _gpu_available():
        logger.warning("--gpu requested but implicit CUDA backend unavailable; falling back to CPU")
        use_gpu = False

    logger.info(
        "Fitting iALS: factors=%d reg=%.4f iters=%d alpha=%.2f gpu=%s",
        factors, regularization, iterations, alpha, use_gpu,
    )
    with _single_threaded_blas():
        model = AlternatingLeastSquares(
            factors=factors,
            regularization=regularization,
            iterations=iterations,
            alpha=alpha,
            use_gpu=use_gpu,
            random_state=random_state,
        )
        model.fit(R_user_item, show_progress=False)

    user_factors = _to_numpy(model.user_factors)
    item_factors = _to_numpy(model.item_factors)
    # Final safety: refuse to return CuPy / wrapper types
    if user_factors.__class__.__module__.startswith(("cupy", "implicit.gpu")):
        raise RuntimeError("user_factors is not a numpy array post-coercion")
    if item_factors.__class__.__module__.startswith(("cupy", "implicit.gpu")):
        raise RuntimeError("item_factors is not a numpy array post-coercion")

    return RankingModel(
        user_factors=user_factors,
        item_factors=item_factors,
        user_to_idx=user_to_idx,
        item_to_idx=item_to_idx,
        factors=int(factors),
        regularization=float(regularization),
        iterations=int(iterations),
        alpha=float(alpha),
        positive_threshold=float(positive_threshold),
        trained_with_gpu=bool(use_gpu),
    )


def train_bpr(
    R_user_item: csr_matrix,
    user_to_idx: dict[str, int],
    item_to_idx: dict[int, int],
    *,
    factors: int = 64,
    regularization: float = 0.05,
    iterations: int = 100,
    learning_rate: float = 0.01,
    use_gpu: bool = False,
    positive_threshold: float = 3.5,
    random_state: int = 42,
) -> RankingModel:
    """Fit ``implicit.bpr.BayesianPersonalizedRanking`` as an alternative ranking head.

    BPR directly optimizes a pairwise ranking loss (rank observed-positive items
    above unobserved ones), which is closer to what NDCG@K actually measures than
    iALS's weighted-MSE objective — worth comparing against iALS on the same eval
    harness (see train_recommender.py's Optuna search, which tries both).

    Only the nonzero *pattern* of ``R_user_item`` is used (BPR treats any nonzero
    entry as an equally-weighted positive and samples pairwise from there) — the
    same confidence-weighted matrix built by ``build_confidence_matrix`` works fine
    as input; the confidence magnitudes themselves are ignored.
    """
    from implicit.bpr import BayesianPersonalizedRanking

    requested_gpu = use_gpu
    if requested_gpu and not _gpu_available():
        logger.warning("--gpu requested but implicit CUDA backend unavailable; falling back to CPU")
        use_gpu = False

    logger.info(
        "Fitting BPR: factors=%d reg=%.4f iters=%d lr=%.4f gpu=%s",
        factors, regularization, iterations, learning_rate, use_gpu,
    )
    with _single_threaded_blas():
        model = BayesianPersonalizedRanking(
            factors=factors,
            regularization=regularization,
            iterations=iterations,
            learning_rate=learning_rate,
            use_gpu=use_gpu,
            random_state=random_state,
        )
        model.fit(R_user_item, show_progress=False)

    user_factors = _to_numpy(model.user_factors)
    item_factors = _to_numpy(model.item_factors)
    if user_factors.__class__.__module__.startswith(("cupy", "implicit.gpu")):
        raise RuntimeError("user_factors is not a numpy array post-coercion")
    if item_factors.__class__.__module__.startswith(("cupy", "implicit.gpu")):
        raise RuntimeError("item_factors is not a numpy array post-coercion")

    return RankingModel(
        user_factors=user_factors,
        item_factors=item_factors,
        user_to_idx=user_to_idx,
        item_to_idx=item_to_idx,
        factors=int(factors),
        regularization=float(regularization),
        iterations=int(iterations),
        alpha=float(learning_rate),  # repurposed field: BPR has no "alpha", store the learning rate
        positive_threshold=float(positive_threshold),
        trained_with_gpu=bool(use_gpu),
        model_type="bpr",
    )


def train_ranking_model(
    model_type: str,
    R_user_item: csr_matrix,
    user_to_idx: dict[str, int],
    item_to_idx: dict[int, int],
    *,
    factors: int,
    regularization: float,
    iterations: int,
    alpha: float,
    use_gpu: bool = False,
    positive_threshold: float = 3.5,
    random_state: int = 42,
) -> RankingModel:
    """Dispatch to ``train_ials`` or ``train_bpr`` by ``model_type`` ("ials"/"bpr").

    ``alpha`` is iALS's outer confidence multiplier when ``model_type == "ials"``,
    or BPR's learning rate when ``model_type == "bpr"`` — kept as one hyperparameter
    slot so both model types share the same Optuna search space shape.
    """
    if model_type == "bpr":
        return train_bpr(
            R_user_item, user_to_idx, item_to_idx,
            factors=factors, regularization=regularization, iterations=iterations,
            learning_rate=alpha, use_gpu=use_gpu, positive_threshold=positive_threshold,
            random_state=random_state,
        )
    return train_ials(
        R_user_item, user_to_idx, item_to_idx,
        factors=factors, regularization=regularization, iterations=iterations,
        alpha=alpha, use_gpu=use_gpu, positive_threshold=positive_threshold,
        random_state=random_state,
    )


def score_users_topk(
    ranking: RankingModel,
    user_idxs: np.ndarray,
    k: int = 10,
    exclude: Optional[csr_matrix] = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (top_item_idxs, scores) of shape (len(user_idxs), k) using dot products.

    ``exclude`` is a (n_users, n_items) CSR of seen items to mask out (e.g. training set).
    """
    n_items = ranking.item_factors.shape[0]
    Uf = ranking.user_factors[user_idxs]            # (B, F)
    scores = Uf @ ranking.item_factors.T            # (B, n_items)
    if exclude is not None:
        for row, u in enumerate(user_idxs):
            seen = exclude.indices[exclude.indptr[u]:exclude.indptr[u + 1]]
            scores[row, seen] = -np.inf
    if k >= n_items:
        idx = np.argsort(-scores, axis=1)
    else:
        idx = np.argpartition(-scores, kth=k - 1, axis=1)[:, :k]
        # Sort the top-k slice
        row_scores = np.take_along_axis(scores, idx, axis=1)
        order = np.argsort(-row_scores, axis=1)
        idx = np.take_along_axis(idx, order, axis=1)
    top_scores = np.take_along_axis(scores, idx, axis=1)
    return idx[:, :k], top_scores[:, :k]


def fold_in_user_factor(
    item_factors: np.ndarray,
    item_idx: np.ndarray,
    stored_confidence: np.ndarray,
    recipe: ConfidenceRecipe,
) -> Optional[np.ndarray]:
    """Closed-form iALS user solve with the item factors held fixed:

        u = (VᵀV + Σ_i (c_i - 1) v_i v_iᵀ + λI)⁻¹ Σ_i c_i v_i,   c_i = outer_alpha * stored_i

    which is exactly the per-user normal equation implicit's ALS solves (implicit
    multiplies the confidence matrix by its ``alpha`` and uses ``regularization``
    unscaled). ``stored_confidence`` is what ``build_confidence_matrix`` would have
    put in the user's CSR row (see ``ConfidenceRecipe.rating_confidence``); duplicate
    item indices are summed, like ``sum_duplicates`` does in training.
    """
    item_idx = np.asarray(item_idx, dtype=np.int64)
    if item_idx.size == 0:
        return None
    V = np.asarray(item_factors, dtype=np.float64)
    uniq, inv = np.unique(item_idx, return_inverse=True)
    stored = np.bincount(inv, weights=np.asarray(stored_confidence, dtype=np.float64))
    c = recipe.outer_alpha * stored
    Vi = V[uniq]
    A = V.T @ V + (Vi * (c - 1.0)[:, None]).T @ Vi + recipe.regularization * np.eye(V.shape[1])
    b = (Vi * c[:, None]).sum(axis=0)
    return np.linalg.solve(A, b).astype(np.float32)
