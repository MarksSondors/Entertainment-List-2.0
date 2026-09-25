"""Per-user temporal splits.

Evaluation protocol (``EVAL_PROTOCOL``):

    A  - each user's oldest ratings           -> fit models used for *tuning*
    B  - the next ``b_frac`` of their ratings -> score tuning candidates (Optuna, λs, reranker labels)
    C  - their most recent ``c_frac``         -> the reported / gated test set, never used for tuning

A∪B is exactly the historical ``train_df`` and C the historical ``val_df`` of
``stratified_temporal_split`` (modulo tie order between identical timestamps), so
numbers stay comparable with earlier runs. Users with fewer than
``min_user_ratings`` ratings stay entirely in A.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Bump when split semantics change so cached per-stage artifacts keyed on it are invalidated.
SPLIT_VERSION = 1


@dataclass
class Splits:
    """A sorted frame plus boolean masks; the part frames are materialized on demand
    (not cached) so callers can drop each one as soon as they're done with it."""
    df: pd.DataFrame
    is_b: np.ndarray
    is_c: np.ndarray

    def a(self) -> pd.DataFrame:
        return self.df.loc[~(self.is_b | self.is_c)].reset_index(drop=True)

    def b(self) -> pd.DataFrame:
        return self.df.loc[self.is_b].reset_index(drop=True)

    def c(self) -> pd.DataFrame:
        return self.df.loc[self.is_c].reset_index(drop=True)

    def ab(self) -> pd.DataFrame:
        return self.df.loc[~self.is_c].reset_index(drop=True)


def three_way_temporal_split(
    df: pd.DataFrame,
    *,
    c_frac: float = 0.20,
    b_frac: float = 0.15,
    min_user_ratings: int = 2,
) -> Splits:
    """Split every user's history by time into A (oldest) / B / C (latest).

    Per user with n >= ``min_user_ratings`` ratings: C holds the latest
    ``max(1, round(n * c_frac))`` rows and B the ``max(1, round(n * b_frac))`` rows
    before that (0 when ``b_frac == 0``), capped so A always keeps at least one row.
    """
    df = df.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    grouped = df.groupby("user_id", sort=False, observed=True)["timestamp"]
    pos = grouped.cumcount().to_numpy()
    n = grouped.transform("size").to_numpy()

    eligible = n >= min_user_ratings
    # np.rint rounds half to even, matching the builtin round() the old loop used.
    n_c = np.where(eligible, np.maximum(1, np.rint(n * c_frac)), 0).astype(np.int64)
    if b_frac > 0:
        n_b = np.where(eligible, np.maximum(1, np.rint(n * b_frac)), 0).astype(np.int64)
        n_b = np.minimum(n_b, np.maximum(n - n_c - 1, 0))
    else:
        n_b = np.zeros_like(n_c)

    is_c = pos >= n - n_c
    is_b = ~is_c & (pos >= n - n_c - n_b)

    total = max(len(df), 1)
    logger.info(
        "Temporal split: A=%d (%.1f%%) B=%d (%.1f%%) C=%d (%.1f%%)",
        int((~(is_b | is_c)).sum()), 100.0 * (~(is_b | is_c)).sum() / total,
        int(is_b.sum()), 100.0 * is_b.sum() / total,
        int(is_c.sum()), 100.0 * is_c.sum() / total,
    )
    return Splits(df=df, is_b=is_b, is_c=is_c)
