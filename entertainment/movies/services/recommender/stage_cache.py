"""Disk cache for expensive intermediate stages (fitted A / A∪B models, ...).

Keyed on a dataset fingerprint + split version + stage name + params hash, so
re-running the experiment harness with unchanged inputs skips refitting.
Artifacts are plain pickles under ``data/.recommender_cache/stages/``.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
from pathlib import Path
from typing import Callable, TypeVar

import pandas as pd
from django.conf import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _stage_dir() -> Path:
    p = Path(settings.BASE_DIR) / "data" / ".recommender_cache" / "stages"
    p.mkdir(parents=True, exist_ok=True)
    return p


def dataset_fingerprint(df: pd.DataFrame) -> str:
    """Cheap content fingerprint of a training frame (row count + column checksums)."""
    parts = [
        str(len(df)),
        str(int(df["timestamp"].sum())),
        f"{float(df['rating'].sum()):.3f}",
        str(int(df["tmdb_id"].sum())),
        str(int(df["user_id"].nunique())),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def stage_key(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def load_or_fit(stage: str, key: str, fn: Callable[[], T], *, enabled: bool = True) -> T:
    """Return the cached artifact for (stage, key), or compute it with ``fn`` and cache it."""
    if not enabled:
        return fn()
    path = _stage_dir() / f"{stage}_{key}.pkl"
    if path.exists():
        try:
            with open(path, "rb") as fh:
                logger.info("Stage cache hit: %s (%s)", stage, key)
                return pickle.load(fh)
        except Exception:
            logger.exception("Stage cache read failed for %s; refitting", path.name)
    value = fn()
    try:
        with open(path, "wb") as fh:
            pickle.dump(value, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        logger.exception("Stage cache write failed for %s (continuing)", path.name)
    return value
