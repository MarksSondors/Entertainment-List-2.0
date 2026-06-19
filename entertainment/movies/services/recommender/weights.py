"""Per-row weighting helpers.

- IPS (popularity debiasing)
- Exponential time decay (recent ratings matter more)
- Source weight (local users vs MovieLens)
- iALS confidence weights derived from positive interactions
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_ips_weights(item_ids: pd.Series, clip_min: float = 0.1, clip_max: float = 5.0) -> np.ndarray:
    """Inverse Propensity Scoring per row.

    propensity(i) = count(i) / max_count;   ips(i) = 1/propensity, clipped.
    Returns float32 array aligned to item_ids.
    """
    counts = item_ids.value_counts()
    max_count = float(counts.max()) if len(counts) else 1.0
    propensity = counts / max_count
    ips = (1.0 / propensity).clip(lower=clip_min, upper=clip_max)
    return item_ids.map(ips).fillna(1.0).astype(np.float32).values


def time_decay(timestamps: np.ndarray, half_life_days: int = 365 * 3, floor: float = 0.1) -> np.ndarray:
    """Exponential decay; rating 1 half-life ago has weight 0.5. Floored to ``floor``."""
    if len(timestamps) == 0:
        return np.zeros(0, dtype=np.float32)
    max_ts = float(np.max(timestamps))
    half_life_seconds = half_life_days * 86400.0
    decay = np.exp(-np.log(2.0) * (max_ts - timestamps) / half_life_seconds)
    return np.clip(decay, floor, 1.0).astype(np.float32)


def source_weights(user_ids: pd.Series, local_weight: float = 3.0, ml_weight: float = 1.0) -> np.ndarray:
    """Replace row-duplication boost: local users get higher weight than ML users."""
    is_local = user_ids.astype(str).str.startswith("loc_").values
    return np.where(is_local, local_weight, ml_weight).astype(np.float32)


def combine_sample_weights(*weights: np.ndarray) -> np.ndarray:
    """Multiply weight arrays element-wise; treats None / empty as 1."""
    out = None
    for w in weights:
        if w is None:
            continue
        w = np.asarray(w, dtype=np.float32)
        out = w if out is None else out * w
    return out if out is not None else np.ones(0, dtype=np.float32)


def confidence_from_rating(rating: np.ndarray, threshold: float, alpha: float = 40.0) -> np.ndarray:
    """iALS confidence: C = 1 + alpha * positive_strength.

    Positive strength = max(0, rating - threshold) / (5.0 - threshold) so a 5/5 rating
    above threshold=3.5 contributes the full alpha; ratings below threshold are zero.
    """
    span = max(5.0 - threshold, 1e-3)
    strength = np.clip((rating - threshold) / span, 0.0, 1.0)
    return (1.0 + alpha * strength).astype(np.float32)
