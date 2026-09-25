"""Versioned pickle save/load with rotation + overlay layering.

The "base" pickle contains the full v5.0 bundle. The "overlay" pickle is a
small sidecar that fold-in updates write between manual full retrains; it
holds per-user bias edits and per-user ranking-factor rows for users with
new local reviews since ``base.metadata.trained_at``.

All arrays in both files are plain ``np.ndarray``; CuPy / implicit.gpu types
must never be persisted (asserted on save).
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from django.conf import settings

from . import MODEL_VERSION
from .cold_start import ColdStartHead, UserColdStartHead
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
    user_cold = bundle.get("user_cold_start")
    if user_cold is not None:
        _assert_numpy("user_cold_start.coef", user_cold["coef"])
        _assert_numpy("user_cold_start.intercept", user_cold["intercept"])
    ease = bundle.get("ease")
    if ease is not None:
        for key in ("item_ids", "indptr", "indices", "data"):
            _assert_numpy(f"ease.{key}", ease[key])


def build_bundle(
    *,
    biases: dict,
    catalog: CatalogLookups,
    ranking: RankingModel,
    cold_start: Optional[ColdStartHead],
    metadata: dict,
    user_cold_start: Optional[UserColdStartHead] = None,
    confidence: Optional[dict] = None,
    item_counts: Optional[np.ndarray] = None,
    ease: Optional[dict] = None,
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
            "model_type": ranking.model_type,
            # Full confidence recipe (weights.ConfidenceRecipe) so the per-user fold-in
            # solves against exactly the weighting the item factors were trained with.
            "confidence": dict(confidence or {}),
        },
        "catalog": {
            "tmdb_to_genres": dict(catalog.tmdb_to_genres),
            "tmdb_to_language": dict(catalog.tmdb_to_language),
            "tmdb_to_runtime_bucket": dict(catalog.tmdb_to_runtime_bucket),
            "tmdb_to_year": dict(catalog.tmdb_to_year),
            "tmdb_vote_data": dict(catalog.tmdb_vote_data),
            "tmdb_to_director": dict(catalog.tmdb_to_director),
            "tmdb_to_top_cast": dict(catalog.tmdb_to_top_cast),
        },
        "known_tmdb_ids": list(ranking.item_to_idx.keys()),
    }
    if ease is not None:
        bundle["ease"] = ease   # EaseModel.to_bundle(): sparse item-item weights keyed by TMDB id
    if item_counts is not None:
        bundle["item_stats"] = {"interaction_counts": np.asarray(item_counts, dtype=np.int32)}
    if cold_start is not None:
        bundle["cold_start"] = {
            "coef": cold_start.coef,
            "intercept": cold_start.intercept,
            "decades": list(cold_start.decades),
            "languages": list(cold_start.languages),
            "feature_dim": int(cold_start.feature_dim),
            "directors": list(cold_start.directors),
            "top_cast": list(cold_start.top_cast),
        }
    if user_cold_start is not None:
        bundle["user_cold_start"] = {
            "coef": user_cold_start.coef,
            "intercept": user_cold_start.intercept,
            "feature_dim": int(user_cold_start.feature_dim),
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


@dataclass
class SaveResult:
    path: Path
    promoted: bool
    reason: str


# A challenger whose clean NDCG@10 is more than this fraction below the current
# champion's (same eval protocol) is saved but not promoted to svd_model_latest.pkl.
PROMOTION_TOLERANCE = 0.02

LATEST_META = "svd_model_latest.meta.json"


def _meta_path(pickle_path: Path) -> Path:
    return pickle_path.with_suffix(".meta.json")


def write_meta(pickle_path: Path, metadata: dict) -> Path:
    """Metadata sidecar so gates/UIs can inspect a model without unpickling ~300 MB."""
    p = _meta_path(pickle_path)
    p.write_text(json.dumps(metadata, indent=2, default=str))
    return p


def read_meta(pickle_path: Path) -> Optional[dict]:
    p = _meta_path(pickle_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def promotion_decision(new_meta: dict, champion_meta: Optional[dict],
                       tolerance: float = PROMOTION_TOLERANCE) -> tuple[bool, str]:
    """(promote?, reason). Only models evaluated under the same protocol are compared;
    anything else (no champion, legacy/leaked champion metrics) promotes."""
    if not champion_meta:
        return True, "no current champion metadata"
    new_proto = new_meta.get("eval_protocol")
    old_proto = champion_meta.get("eval_protocol")
    if new_proto is None or new_proto != old_proto:
        return True, f"champion eval protocol {old_proto!r} not comparable with {new_proto!r}"
    new_ndcg = float((new_meta.get("eval") or {}).get("ndcg_at_k", 0.0))
    old_ndcg = float((champion_meta.get("eval") or {}).get("ndcg_at_k", 0.0))
    if old_ndcg <= 0:
        return True, "champion has no NDCG@10"
    rel = (new_ndcg - old_ndcg) / old_ndcg
    if rel < -tolerance:
        return False, (f"NDCG@10 {new_ndcg:.4f} is {-100 * rel:.1f}% below champion {old_ndcg:.4f} "
                       f"(tolerance {100 * tolerance:.0f}%)")
    return True, f"NDCG@10 {new_ndcg:.4f} vs champion {old_ndcg:.4f} ({100 * rel:+.1f}%)"


def save_bundle(bundle: dict, *, keep_versions: int = 5, force_promote: bool = False,
                directory: Optional[Path] = None) -> SaveResult:
    """Pickle ``bundle`` as a versioned snapshot (+ ``.meta.json`` sidecar), then promote
    it to svd_model_latest.pkl / svd_model.pkl unless it regresses against the current
    champion (see ``promotion_decision``), and prune old versions beyond ``keep_versions``.
    """
    assert_pickle_safe(bundle)
    d = Path(directory) if directory else model_dir()
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    versioned = d / f"svd_model_{ts}.pkl"
    with open(versioned, "wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
    metadata = bundle.get("metadata", {}) or {}
    write_meta(versioned, metadata)

    champion_meta = None
    latest_meta_path = d / LATEST_META
    if latest_meta_path.exists():
        try:
            champion_meta = json.loads(latest_meta_path.read_text())
        except (OSError, ValueError):
            champion_meta = None
    promote, reason = promotion_decision(metadata, champion_meta)
    if force_promote and not promote:
        promote, reason = True, f"forced ({reason})"

    if promote:
        shutil.copy2(versioned, d / "svd_model_latest.pkl")
        shutil.copy2(versioned, d / "svd_model.pkl")  # back-compat name
        shutil.copy2(_meta_path(versioned), latest_meta_path)
        logger.info("Promoted %s to svd_model_latest.pkl: %s", versioned.name, reason)
    else:
        logger.warning("NOT promoting %s: %s", versioned.name, reason)
    _rotate(d, keep_versions=keep_versions)

    size_mb = versioned.stat().st_size / 1024 / 1024
    logger.info("Saved %s (%.2f MB)", versioned.name, size_mb)
    return SaveResult(path=versioned, promoted=promote, reason=reason)


def _rotate(d: Path, *, keep_versions: int) -> None:
    versioned = sorted(d.glob("svd_model_2*.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in versioned[keep_versions:]:
        try:
            old.unlink()
            meta = _meta_path(old)
            if meta.exists():
                meta.unlink()
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
