"""iALS ranking head with optional CUDA.

Ranking-time scores come from a proper implicit-feedback ALS. Positive
interactions are ratings >= ``positive_threshold``. Confidence:

    C_ui = 1 + alpha * positive_strength(rating) * source_weight

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

from .weights import confidence_from_rating

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
    positive_threshold: float = 3.5,
    alpha: float = 40.0,
    local_user_weight: float = 3.0,
) -> tuple[csr_matrix, dict[str, int], dict[int, int]]:
    """Build a (n_users, n_items) confidence-weighted CSR for iALS.

    Only rows with rating >= ``positive_threshold`` are kept (implicit positives).
    """
    pos = df[df["rating"] >= positive_threshold].copy()
    if pos.empty:
        raise ValueError(f"No positive interactions at threshold={positive_threshold}")

    user_ids = pos["user_id"].values
    item_ids = pos["tmdb_id"].values

    user_to_idx = {u: i for i, u in enumerate(pd.unique(user_ids))}
    item_to_idx = {int(t): i for i, t in enumerate(pd.unique(item_ids))}

    u_idx = np.fromiter((user_to_idx[u] for u in user_ids), dtype=np.int32, count=len(user_ids))
    i_idx = np.fromiter((item_to_idx[int(t)] for t in item_ids), dtype=np.int32, count=len(item_ids))

    confidence = confidence_from_rating(pos["rating"].values.astype(np.float32),
                                        threshold=positive_threshold, alpha=alpha)
    is_local = np.array([str(u).startswith("loc_") for u in user_ids])
    confidence = confidence * np.where(is_local, local_user_weight, 1.0).astype(np.float32)

    n_users = len(user_to_idx)
    n_items = len(item_to_idx)
    R = coo_matrix((confidence, (u_idx, i_idx)), shape=(n_users, n_items), dtype=np.float32).tocsr()
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
