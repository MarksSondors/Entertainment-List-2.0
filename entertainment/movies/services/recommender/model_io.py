"""Versioned pickle save/load with rotation + overlay layering.

The "base" pickle contains the full v5.0 bundle. The "overlay" pickle is a
small sidecar that fold-in updates write between manual full retrains; it
holds per-user bias edits and per-user ranking-factor rows for users with
new local reviews since ``base.metadata.trained_at``.

All arrays in both files are plain ``np.ndarray``; CuPy / implicit.gpu types
must never be persisted (asserted on save).
"""
from __future__ import annotations

import logging
import os
import pickle
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from django.conf import settings

from . import MODEL_VERSION
from .cold_start import ColdStartHead
from .data_loading import CatalogLookups
from .mf_ranking import RankingModel

logger = logging.getLogger(__name__)


def model_dir() -> Path:
    p = Path(settings.BASE_DIR) / "movies" / "ml_models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _assert_numpy(name: str, arr) -> None:
    if not isinstance(arr, np.ndarray):
        raise TypeError(f"{name} must be a numpy.ndarray for portable serialization, got {type(arr)}")
    mod = type(arr).__module__
    if mod.startswith(("cupy", "implicit.gpu")):
        raise TypeError(f"{name} originates from {mod}; refuse to pickle non-numpy array")


def assert_pickle_safe(bundle: dict) -> None:
    """Walk the bundle and assert every array is plain numpy. Called pre-save."""
    ranking = bundle.get("ranking", {})
    _assert_numpy("ranking.user_factors", ranking.get("user_factors"))
    _assert_numpy("ranking.item_factors", ranking.get("item_factors"))
    cold = bundle.get("cold_start")
    if cold is not None:
        _assert_numpy("cold_start.coef", cold["coef"])
        _assert_numpy("cold_start.intercept", cold["intercept"])


def build_bundle(
    *,
    biases: dict,
    catalog: CatalogLookups,
    ranking: RankingModel,
    cold_start: Optional[ColdStartHead],
    metadata: dict,
) -> dict:
    """Assemble the v5.0 export bundle. Adds legacy-shaped keys for back-compat
    with the existing ``MovieRecommender`` until inference is updated.
    """
    bundle: dict = {
        "model_version": MODEL_VERSION,
        "metadata": metadata,
        "biases": dict(biases),
        "ranking": {
            "user_factors": ranking.user_factors,
            "item_factors": ranking.item_factors,
            "user_to_idx": dict(ranking.user_to_idx),
            "item_to_idx": dict(ranking.item_to_idx),
            "factors": ranking.factors,
            "regularization": ranking.regularization,
            "iterations": ranking.iterations,
            "alpha": ranking.alpha,
            "positive_threshold": ranking.positive_threshold,
            "trained_with_gpu": ranking.trained_with_gpu,
        },
        "catalog": {
            "tmdb_to_genres": dict(catalog.tmdb_to_genres),
            "tmdb_to_language": dict(catalog.tmdb_to_language),
            "tmdb_to_runtime_bucket": dict(catalog.tmdb_to_runtime_bucket),
            "tmdb_to_year": dict(catalog.tmdb_to_year),
            "tmdb_vote_data": dict(catalog.tmdb_vote_data),
        },
        "known_tmdb_ids": list(ranking.item_to_idx.keys()),
    }
    if cold_start is not None:
        bundle["cold_start"] = {
            "coef": cold_start.coef,
            "intercept": cold_start.intercept,
            "decades": list(cold_start.decades),
            "languages": list(cold_start.languages),
            "feature_dim": int(cold_start.feature_dim),
        }

    # Legacy-shaped top-level keys so the existing inference code keeps working
    # until Phase 6 lands. Inference reads from these in addition to v5.0 sections.
    bundle.update({
        "user_factors": ranking.user_factors,
        "item_factors": ranking.item_factors,
        "user_to_idx": dict(ranking.user_to_idx),
        "item_to_idx": dict(ranking.item_to_idx),
        "global_mean": float(biases["global_mean"]),
        "year_biases": dict(biases["year_biases"]),
        "item_biases": dict(biases["item_biases"]),
        "user_biases": dict(biases["user_biases"]),
        "user_genre_biases": dict(biases["user_genre_biases"]),
        "user_decade_biases": dict(biases["user_decade_biases"]),
        "user_language_biases": dict(biases["user_language_biases"]),
        "user_runtime_biases": dict(biases["user_runtime_biases"]),
        "tmdb_to_genres": dict(catalog.tmdb_to_genres),
        "tmdb_to_language": dict(catalog.tmdb_to_language),
        "tmdb_to_runtime_bucket": dict(catalog.tmdb_to_runtime_bucket),
        "tmdb_id_to_year": dict(catalog.tmdb_to_year),
        "tmdb_vote_data": dict(catalog.tmdb_vote_data),
        # Empty mapping signals "TMDB genres are native — pass through verbatim".
        "genre_mapping": {},
    })
    return bundle


def save_bundle(bundle: dict, *, keep_versions: int = 5) -> Path:
    """Pickle ``bundle`` as a versioned snapshot, update _latest.pkl + svd_model.pkl,
    and prune old versioned files beyond ``keep_versions``.
    """
    assert_pickle_safe(bundle)
    d = model_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    versioned = d / f"svd_model_{ts}.pkl"
    with open(versioned, "wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
    shutil.copy2(versioned, d / "svd_model_latest.pkl")
    shutil.copy2(versioned, d / "svd_model.pkl")  # back-compat name
    _rotate(d, keep_versions=keep_versions)

    size_mb = versioned.stat().st_size / 1024 / 1024
    logger.info("Saved %s (%.2f MB)", versioned.name, size_mb)
    return versioned


def _rotate(d: Path, *, keep_versions: int) -> None:
    versioned = sorted(d.glob("svd_model_2*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in versioned[keep_versions:]:
        try:
            old.unlink()
            logger.info("Rotated out: %s", old.name)
        except OSError as e:
            logger.warning("Failed to rotate %s: %s", old.name, e)


def load_bundle(path: Optional[Path] = None) -> Optional[dict]:
    p = Path(path) if path else (model_dir() / "svd_model_latest.pkl")
    if not p.exists():
        p = model_dir() / "svd_model.pkl"
    if not p.exists():
        return None
    with open(p, "rb") as f:
        return pickle.load(f)


# --- Overlay (per-user fold-in updates) ---

OVERLAY_FILENAME = "svd_overlay_latest.pkl"


def overlay_path() -> Path:
    return model_dir() / OVERLAY_FILENAME


def load_overlay() -> dict:
    """Load the overlay if present, else return an empty container."""
    p = overlay_path()
    if not p.exists():
        return _empty_overlay()
    try:
        with open(p, "rb") as f:
            data = pickle.load(f)
        if not isinstance(data, dict):
            return _empty_overlay()
        return data
    except (pickle.PickleError, EOFError, OSError) as e:
        logger.warning("Failed to load overlay (%s); resetting", e)
        return _empty_overlay()


def save_overlay(overlay: dict) -> None:
    p = overlay_path()
    with open(p, "wb") as f:
        pickle.dump(overlay, f, protocol=pickle.HIGHEST_PROTOCOL)


def _empty_overlay() -> dict:
    return {
        "overlay_version": "1.0",
        "base_trained_at": None,
        "updated_at": None,
        "user_biases": {},                  # user_id -> float
        "user_genre_biases": {},            # genre -> { user_id -> float }
        "user_decade_biases": {},           # decade -> { user_id -> float }
        "user_language_biases": {},
        "user_runtime_biases": {},
        "ranking_user_factors": {},         # user_id -> np.ndarray (factors,)
        "ranking_user_to_idx": {},          # only used to track which loc_ users are in the overlay
    }


def is_overlay_compatible(overlay: dict, base_trained_at: str) -> bool:
    """An overlay is valid only against the base model that produced it.
    After a manual retrain, the overlay's `base_trained_at` must match the
    new base's `metadata.trained_at`; otherwise the overlay is stale.
    """
    return bool(overlay) and overlay.get("base_trained_at") == base_trained_at


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
