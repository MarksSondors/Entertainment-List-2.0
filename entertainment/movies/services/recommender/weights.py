"""Per-row weighting helpers.

- IPS (popularity debiasing)
- Exponential time decay (recent ratings matter more)
- Source weight (local users vs MovieLens)
- iALS confidence weights derived from positive interactions
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

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


def time_decay(
    timestamps: np.ndarray,
    half_life_days: float = 365 * 3,
    floor: float = 0.1,
    reference_ts: Optional[float] = None,
) -> np.ndarray:
    """Exponential decay; rating 1 half-life before ``reference_ts`` (default: the
    newest timestamp given) has weight 0.5. Floored to ``floor``."""
    if len(timestamps) == 0:
        return np.zeros(0, dtype=np.float32)
    max_ts = float(np.max(timestamps)) if reference_ts is None else float(reference_ts)
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


@dataclass
class ConfidenceRecipe:
    """Everything that determines an interaction's iALS confidence.

    Shared by the trainer (``mf_ranking.build_confidence_matrix``) and the per-user
    fold-in (``update_recommender``) and shipped in the bundle, so a folded-in user
    factor is solved against exactly the weighting the item factors were trained with.

    implicit multiplies the whole matrix by its ``alpha`` (``outer_alpha`` here)
    before solving, and uses ``regularization`` unscaled, so the effective confidence
    of an interaction is ``outer_alpha * stored_confidence``.
    """
    positive_threshold: float = 3.5
    conf_alpha: float = 40.0
    outer_alpha: float = 1.0
    local_user_weight: float = 3.0
    watchlist_confidence: float = 2.0
    regularization: float = 0.05
    recency_half_life_days: Optional[float] = None
    reference_ts: Optional[float] = None   # "now" for recency decay: newest timestamp at fit time

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ConfidenceRecipe":
        if not d:
            return cls()
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})

    def rating_confidence(
        self,
        rating: np.ndarray,
        is_local: np.ndarray,
        timestamps: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Stored (pre-``outer_alpha``) confidence for positive rated interactions."""
        conf = confidence_from_rating(np.asarray(rating, dtype=np.float32),
                                      threshold=self.positive_threshold, alpha=self.conf_alpha)
        conf = conf * np.where(np.asarray(is_local, dtype=bool), self.local_user_weight, 1.0).astype(np.float32)
        if self.recency_half_life_days and timestamps is not None and len(timestamps):
            conf = conf * time_decay(np.asarray(timestamps, dtype=np.float64),
                                     half_life_days=self.recency_half_life_days,
                                     reference_ts=self.reference_ts)
        return conf.astype(np.float32)
