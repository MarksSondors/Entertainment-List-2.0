"""Evaluation: stratified temporal split + RMSE/MAE + ranking metrics."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix, csr_matrix

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    rmse: float = 0.0
    mae: float = 0.0
    ndcg_at_k: float = 0.0
    recall_at_k: float = 0.0
    hit_rate_at_k: float = 0.0
    mrr: float = 0.0
    coverage_at_k: float = 0.0
    n_test_users: int = 0
    n_test_ratings: int = 0
    per_cohort: dict[str, "EvalResult"] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "rmse": self.rmse,
            "mae": self.mae,
            "ndcg_at_k": self.ndcg_at_k,
            "recall_at_k": self.recall_at_k,
            "hit_rate_at_k": self.hit_rate_at_k,
            "mrr": self.mrr,
            "coverage_at_k": self.coverage_at_k,
            "n_test_users": self.n_test_users,
            "n_test_ratings": self.n_test_ratings,
        }
        if self.per_cohort:
            d["per_cohort"] = {k: v.to_dict() for k, v in self.per_cohort.items()}
        return d


def stratified_temporal_split(
    df: pd.DataFrame,
    *,
    val_fraction: float = 0.2,
    min_user_ratings_for_val: int = 2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """For each user with >= ``min_user_ratings_for_val`` ratings, hold out their most
    recent ``val_fraction`` (at least 1 row) into the validation set. Users with fewer
    ratings stay entirely in train. Avoids the cold-out problem with global quantile splits.
    """
    df = df.sort_values("timestamp").reset_index(drop=True)
    val_indices: list[int] = []

    for _, group in df.groupby("user_id", sort=False):
        n = len(group)
        if n < min_user_ratings_for_val:
            continue
        n_val = max(1, int(round(n * val_fraction)))
        # take the *latest* rows
        val_indices.extend(group.index[-n_val:].tolist())

    val_set = set(val_indices)
    val_mask = df.index.isin(val_set)
    train_df = df.loc[~val_mask].reset_index(drop=True)
    val_df = df.loc[val_mask].reset_index(drop=True)
    logger.info("Stratified split: %d train / %d val (%.1f%%)",
                len(train_df), len(val_df), 100.0 * len(val_df) / max(len(df), 1))
    return train_df, val_df


def _ndcg_at_k(preds: np.ndarray, hits: np.ndarray, k: int) -> float:
    """preds: (n, k) item ids; hits: (n, k) bool. Ideal DCG assumes all hits at top."""
    if preds.size == 0:
        return 0.0
    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    dcg = (hits.astype(np.float32) * discounts[:hits.shape[1]]).sum(axis=1)
    n_relevant = hits.sum(axis=1)
    ideal = np.array([discounts[:int(min(n, k))].sum() for n in n_relevant], dtype=np.float32)
    ndcg = np.divide(dcg, ideal, out=np.zeros_like(dcg), where=ideal > 0)
    return float(ndcg.mean())


def predict_explicit(
    df: pd.DataFrame,
    biases: dict,
    *,
    user_to_idx: Optional[dict] = None,
    item_to_idx: Optional[dict] = None,
    user_factors: Optional[np.ndarray] = None,
    item_factors: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Predict explicit ratings using bias hierarchy + (optional) factor dot product.

    Mirrors the legacy bias-prediction path so RMSE numbers stay comparable across runs.
    """
    n = len(df)
    pred = np.full(n, biases["global_mean"], dtype=np.float32)
    pred += df["year"].map(biases["year_biases"]).fillna(0).astype(np.float32).values
    pred += df["tmdb_id"].map(biases["item_biases"]).fillna(0).astype(np.float32).values
    pred += df["user_id"].map(biases["user_biases"]).fillna(0).astype(np.float32).values

    # Genre (multi-hot)
    if biases.get("user_genre_biases"):
        for g, bias_map in biases["user_genre_biases"].items():
            mask = df["genres"].apply(lambda gs: g in gs if gs else False).values
            if mask.any():
                pred[mask] += df.loc[mask, "user_id"].map(bias_map).fillna(0).astype(np.float32).values

    # Decade
    if biases.get("user_decade_biases"):
        for d, bias_map in biases["user_decade_biases"].items():
            mask = (df["decade"] == d).values
            if mask.any():
                pred[mask] += df.loc[mask, "user_id"].map(bias_map).fillna(0).astype(np.float32).values

    # Language
    if biases.get("user_language_biases") and "language" in df.columns:
        for l, bias_map in biases["user_language_biases"].items():
            mask = (df["language"] == l).values
            if mask.any():
                pred[mask] += df.loc[mask, "user_id"].map(bias_map).fillna(0).astype(np.float32).values

    # Runtime
    if biases.get("user_runtime_biases") and "runtime_bucket" in df.columns:
        for r, bias_map in biases["user_runtime_biases"].items():
            mask = (df["runtime_bucket"] == r).values
            if mask.any():
                pred[mask] += df.loc[mask, "user_id"].map(bias_map).fillna(0).astype(np.float32).values

    # Optional factor dot product (used when factors come from a residual-MF; iALS factors
    # are typically NOT added to explicit predictions — pass None there).
    if user_factors is not None and item_factors is not None and user_to_idx and item_to_idx:
        u_idx = df["user_id"].map(user_to_idx).values
        i_idx = df["tmdb_id"].map(item_to_idx).values
        valid = (~pd.isna(u_idx)) & (~pd.isna(i_idx))
        if valid.any():
            uv = u_idx[valid].astype(int)
            iv = i_idx[valid].astype(int)
            inter = np.einsum("ij,ij->i", user_factors[uv], item_factors[iv]).astype(np.float32)
            pred_arr = pred.copy()
            pred_arr[valid] += inter
            pred = pred_arr
    return pred


def evaluate_pointwise(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    biases: dict,
    *,
    user_to_idx: Optional[dict] = None,
    item_to_idx: Optional[dict] = None,
    user_factors: Optional[np.ndarray] = None,
    item_factors: Optional[np.ndarray] = None,
) -> tuple[float, float]:
    """Return (RMSE, MAE) on val_df."""
    if val_df.empty:
        return 0.0, 0.0
    # Restrict to known users/items for the optional factor path
    pred = predict_explicit(
        val_df, biases,
        user_to_idx=user_to_idx, item_to_idx=item_to_idx,
        user_factors=user_factors, item_factors=item_factors,
    )
    err = pred - val_df["rating"].values.astype(np.float32)
    rmse = float(np.sqrt(np.mean(err ** 2)))
    mae = float(np.mean(np.abs(err)))
    return rmse, mae


def evaluate_ranking(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    *,
    user_to_idx: dict[str, int],
    item_to_idx: dict[int, int],
    user_factors: np.ndarray,
    item_factors: np.ndarray,
    positive_threshold: float = 3.5,
    k: int = 10,
    max_users: int = 10_000,
) -> EvalResult:
    """Compute NDCG@K / Recall@K / HitRate@K / MRR / Coverage@K on val positives.

    Sample-capped at ``max_users`` for tractability on the 32M dataset.
    """
    val_pos = val_df[val_df["rating"] >= positive_threshold]
    val_pos = val_pos[val_pos["user_id"].isin(user_to_idx) & val_pos["tmdb_id"].isin(item_to_idx)]
    if val_pos.empty:
        return EvalResult()

    # Group held-out positives per user
    per_user_pos: dict[int, set[int]] = {}
    for uid, tid in zip(val_pos["user_id"].values, val_pos["tmdb_id"].values):
        u = user_to_idx[uid]
        i = item_to_idx[int(tid)]
        per_user_pos.setdefault(u, set()).add(i)

    # Build train CSR for masking
    tr = train_df[train_df["user_id"].isin(user_to_idx) & train_df["tmdb_id"].isin(item_to_idx)]
    if not tr.empty:
        u_arr = np.fromiter((user_to_idx[u] for u in tr["user_id"].values), dtype=np.int32, count=len(tr))
        i_arr = np.fromiter((item_to_idx[int(t)] for t in tr["tmdb_id"].values), dtype=np.int32, count=len(tr))
        train_csr = coo_matrix(
            (np.ones(len(tr), dtype=np.float32), (u_arr, i_arr)),
            shape=(len(user_to_idx), len(item_to_idx)),
        ).tocsr()
    else:
        train_csr = csr_matrix((len(user_to_idx), len(item_to_idx)), dtype=np.float32)

    user_ids = list(per_user_pos.keys())
    if len(user_ids) > max_users:
        rng = np.random.default_rng(42)
        user_ids = list(rng.choice(user_ids, size=max_users, replace=False))

    n_items = item_factors.shape[0]
    hits_total = 0
    recall_sum = 0.0
    rr_sum = 0.0
    ndcg_rows = []
    seen_recommended: set[int] = set()
    discounts = 1.0 / np.log2(np.arange(2, k + 2))

    # Score in batches
    batch_size = 512
    user_ids_arr = np.array(user_ids, dtype=np.int32)
    for start in range(0, len(user_ids_arr), batch_size):
        batch = user_ids_arr[start:start + batch_size]
        scores = user_factors[batch] @ item_factors.T  # (B, n_items)
        # Mask seen
        for row, u in enumerate(batch):
            seen = train_csr.indices[train_csr.indptr[u]:train_csr.indptr[u + 1]]
            if len(seen):
                scores[row, seen] = -np.inf
        # Top-k
        if k < n_items:
            idx = np.argpartition(-scores, kth=k - 1, axis=1)[:, :k]
            row_scores = np.take_along_axis(scores, idx, axis=1)
            order = np.argsort(-row_scores, axis=1)
            top = np.take_along_axis(idx, order, axis=1)
        else:
            top = np.argsort(-scores, axis=1)[:, :k]

        for row, u in enumerate(batch):
            relevant = per_user_pos[int(u)]
            recs = top[row]
            seen_recommended.update(int(x) for x in recs)
            hit_mask = np.array([int(r) in relevant for r in recs], dtype=np.float32)
            hit_count = float(hit_mask.sum())
            hits_total += int(hit_count > 0)
            recall_sum += hit_count / max(len(relevant), 1)
            # MRR: reciprocal rank of first hit
            if hit_count > 0:
                first_hit = int(np.argmax(hit_mask > 0)) + 1
                rr_sum += 1.0 / first_hit
            # NDCG
            dcg = float((hit_mask * discounts[:len(hit_mask)]).sum())
            ideal = float(discounts[:int(min(len(relevant), k))].sum())
            ndcg_rows.append(dcg / ideal if ideal > 0 else 0.0)

    n = len(user_ids_arr)
    return EvalResult(
        ndcg_at_k=float(np.mean(ndcg_rows)) if ndcg_rows else 0.0,
        recall_at_k=recall_sum / max(n, 1),
        hit_rate_at_k=hits_total / max(n, 1),
        mrr=rr_sum / max(n, 1),
        coverage_at_k=len(seen_recommended) / max(n_items, 1),
        n_test_users=n,
        n_test_ratings=int(val_pos.shape[0]),
    )


def evaluate_full(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    *,
    biases: dict,
    ranking_user_to_idx: dict[str, int],
    ranking_item_to_idx: dict[int, int],
    ranking_user_factors: np.ndarray,
    ranking_item_factors: np.ndarray,
    positive_threshold: float = 3.5,
    k: int = 10,
) -> EvalResult:
    """Full-suite evaluation. Adds per-cohort breakdown (Local vs ML, Cold vs Warm)."""
    rmse, mae = evaluate_pointwise(train_df, val_df, biases)
    ranking = evaluate_ranking(
        train_df, val_df,
        user_to_idx=ranking_user_to_idx, item_to_idx=ranking_item_to_idx,
        user_factors=ranking_user_factors, item_factors=ranking_item_factors,
        positive_threshold=positive_threshold, k=k,
    )
    overall = EvalResult(
        rmse=rmse, mae=mae,
        ndcg_at_k=ranking.ndcg_at_k, recall_at_k=ranking.recall_at_k,
        hit_rate_at_k=ranking.hit_rate_at_k, mrr=ranking.mrr,
        coverage_at_k=ranking.coverage_at_k,
        n_test_users=ranking.n_test_users, n_test_ratings=ranking.n_test_ratings,
    )

    # Per-cohort: local vs ML
    for prefix, label in [("loc_", "local"), ("ml_", "movielens")]:
        sub_val = val_df[val_df["user_id"].astype(str).str.startswith(prefix)]
        if sub_val.empty:
            continue
        sub_rmse, sub_mae = evaluate_pointwise(train_df, sub_val, biases)
        sub_rank = evaluate_ranking(
            train_df, sub_val,
            user_to_idx=ranking_user_to_idx, item_to_idx=ranking_item_to_idx,
            user_factors=ranking_user_factors, item_factors=ranking_item_factors,
            positive_threshold=positive_threshold, k=k,
        )
        overall.per_cohort[label] = EvalResult(
            rmse=sub_rmse, mae=sub_mae,
            ndcg_at_k=sub_rank.ndcg_at_k, recall_at_k=sub_rank.recall_at_k,
            hit_rate_at_k=sub_rank.hit_rate_at_k, mrr=sub_rank.mrr,
            coverage_at_k=sub_rank.coverage_at_k,
            n_test_users=sub_rank.n_test_users, n_test_ratings=int(sub_val.shape[0]),
        )
    return overall
