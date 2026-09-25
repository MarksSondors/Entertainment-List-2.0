"""Serving-time ranking transforms shared by evaluation and ``MovieRecommender``.

Every stage (MostPop, iALS, and later EASE / blend / reranker) produces a raw
per-item score vector; ``rank_row`` / ``rank_batch`` then apply the exact same
popularity penalty and MMR diversity re-rank that serving applies, so offline
metrics measure what users are actually shown.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

import numpy as np

from .data_loading import RUNTIME_BUCKETS, TMDB_GENRES, CatalogLookups


@dataclass
class ServingParams:
    """Knobs applied on top of a stage's raw scores.

    ``pop_normalize`` selects how the popularity penalty is applied:
      - True:  z-normalize the user's scores, then subtract ``pop_lambda * z(log1p(votes))``
               (scale-free, so one λ means the same thing for any stage)
      - False: subtract ``pop_lambda * log1p(votes)`` from raw scores (the pre-v5.1 behaviour)
    ``mmr_alpha >= 1`` disables MMR (pure relevance order).
    ``ease_beta`` > 0 ranks by z(iALS) + ease_beta * z(EASE) (needs the bundle's EASE section).
    """
    pop_lambda: float = 0.0
    pop_normalize: bool = True
    mmr_alpha: float = 1.0
    pool_mult: int = 3
    ease_beta: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "ServingParams":
        if not d:
            return cls()
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


# What serving did before v5.1: raw iALS score - 0.05 * log1p(vote_count), top 3N, MMR(0.7).
LEGACY_SERVING = ServingParams(pop_lambda=0.05, pop_normalize=False, mmr_alpha=0.7, pool_mult=3)


@dataclass
class ItemArrays:
    """Per-item metadata aligned to the ranking item index (row i <-> item index i)."""
    log_votes: np.ndarray      # (n,) float32, log1p(TMDB vote_count)
    log_votes_z: np.ndarray    # (n,) float32, log_votes z-scored over the catalog
    genre_mat: np.ndarray      # (n, G) float32 0/1
    language: np.ndarray       # (n,) int32 code (equality only)
    runtime: np.ndarray        # (n,) int32 index into RUNTIME_BUCKETS
    decade: np.ndarray         # (n,) int32, 0 = unknown

    @classmethod
    def build(
        cls,
        tmdb_ids_in_idx_order: Iterable[int],
        catalog: CatalogLookups,
        log_votes_stats: Optional[tuple[float, float]] = None,
    ) -> "ItemArrays":
        """``log_votes_stats`` = (mean, std) to z-score popularity against a reference
        catalog (serving passes the training vocabulary's stats so a candidate subset
        gets the same popularity scale the serving knobs were tuned on)."""
        ids = [int(t) for t in tmdb_ids_in_idx_order]
        n = len(ids)
        genre_col = {g: i for i, g in enumerate(TMDB_GENRES)}
        rt_col = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}
        lang_codes: dict[str, int] = {}

        log_votes = np.zeros(n, dtype=np.float32)
        genre_mat = np.zeros((n, len(TMDB_GENRES)), dtype=np.float32)
        language = np.zeros(n, dtype=np.int32)
        runtime = np.full(n, rt_col["standard"], dtype=np.int32)
        decade = np.zeros(n, dtype=np.int32)
        for row, tid in enumerate(ids):
            vote = catalog.tmdb_vote_data.get(tid)
            if vote is not None:
                log_votes[row] = np.log1p(max(int(vote[1]), 0))
            for g in catalog.tmdb_to_genres.get(tid) or []:
                col = genre_col.get(g)
                if col is not None:
                    genre_mat[row, col] = 1.0
            lang = catalog.tmdb_to_language.get(tid, "en")
            language[row] = lang_codes.setdefault(lang, len(lang_codes))
            runtime[row] = rt_col.get(catalog.tmdb_to_runtime_bucket.get(tid, "standard"), rt_col["standard"])
            year = catalog.tmdb_to_year.get(tid)
            if year:
                decade[row] = (int(year) // 10) * 10

        mean, std = log_votes_stats if log_votes_stats else (float(log_votes.mean()), float(log_votes.std()))
        log_votes_z = ((log_votes - mean) / (std or 1.0)).astype(np.float32)
        return cls(log_votes=log_votes, log_votes_z=log_votes_z, genre_mat=genre_mat,
                   language=language, runtime=runtime, decade=decade)

    def take(self, idx: np.ndarray) -> "ItemArrays":
        """Row subset (language codes stay comparable: they come from the same build)."""
        return ItemArrays(log_votes=self.log_votes[idx], log_votes_z=self.log_votes_z[idx],
                          genre_mat=self.genre_mat[idx], language=self.language[idx],
                          runtime=self.runtime[idx], decade=self.decade[idx])

    @staticmethod
    def log_votes_stats(tmdb_ids: Iterable[int], catalog: CatalogLookups) -> tuple[float, float]:
        v = np.array([np.log1p(max(int(catalog.tmdb_vote_data.get(int(t), (0, 0))[1]), 0)) for t in tmdb_ids],
                     dtype=np.float64)
        return (float(v.mean()), float(v.std())) if v.size else (0.0, 1.0)


def zscore_rows(scores: np.ndarray, stats: Optional[tuple[float, float]] = None) -> np.ndarray:
    """Z-normalize each row over its finite entries; -inf (masked) entries stay -inf.

    ``stats`` = (mean, std) overrides the per-row statistics (single-row serving passes
    the user's stats over the full catalog, which is what eval z-scores over).
    """
    scores = np.atleast_2d(scores).astype(np.float32, copy=True)
    finite = np.isfinite(scores)
    if stats is not None:
        mean, std = stats
        out = (scores - mean) / (std if std > 1e-12 else 1.0)
        out[~finite] = -np.inf
        return out
    cnt = np.maximum(finite.sum(axis=1, keepdims=True), 1)
    filled = np.where(finite, scores, 0.0)
    mean = filled.sum(axis=1, keepdims=True) / cnt
    var = (np.where(finite, filled - mean, 0.0) ** 2).sum(axis=1, keepdims=True) / cnt
    std = np.sqrt(var)
    std[std < 1e-12] = 1.0
    out = (scores - mean) / std
    out[~finite] = -np.inf
    return out


def apply_popularity(scores: np.ndarray, items: ItemArrays, params: ServingParams,
                     score_stats: Optional[tuple[float, float]] = None) -> np.ndarray:
    """(B, n) scores with -inf for disallowed items -> popularity-adjusted scores."""
    if params.pop_normalize:
        out = zscore_rows(scores, score_stats)
        if params.pop_lambda:
            out -= params.pop_lambda * items.log_votes_z[None, :]
        return out
    if not params.pop_lambda:
        return np.atleast_2d(scores)
    return np.atleast_2d(scores) - params.pop_lambda * items.log_votes[None, :]


def _topk_rows(scores: np.ndarray, k: int) -> np.ndarray:
    """(B, n) -> (B, k) indices of the k largest per row, sorted descending."""
    n = scores.shape[1]
    k = min(k, n)
    if k < n:
        idx = np.argpartition(-scores, kth=k - 1, axis=1)[:, :k]
    else:
        idx = np.tile(np.arange(n), (scores.shape[0], 1))
    order = np.argsort(-np.take_along_axis(scores, idx, axis=1), axis=1, kind="stable")
    return np.take_along_axis(idx, order, axis=1)


def mmr_select(pool_idx: np.ndarray, pool_scores: np.ndarray, items: ItemArrays, n: int, alpha: float) -> np.ndarray:
    """Greedy MMR over a candidate pool (already sorted by relevance, descending).

    Similarity mirrors the pre-v5.1 serving MMR: 0.50 genre Jaccard + 0.20 same
    language + 0.15 same runtime bucket + 0.15 same (known) decade. Relevance is
    min-max normalized within the pool.
    """
    pool_idx = np.asarray(pool_idx)
    p = len(pool_idx)
    if p == 0:
        return pool_idx
    n = min(n, p)
    if alpha >= 1.0 or p == 1:
        return pool_idx[:n]

    g = items.genre_mat[pool_idx]
    inter = g @ g.T
    sizes = g.sum(axis=1)
    union = sizes[:, None] + sizes[None, :] - inter
    jac = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
    lang = items.language[pool_idx]
    rt = items.runtime[pool_idx]
    dec = items.decade[pool_idx]
    sim = (0.50 * jac
           + 0.20 * (lang[:, None] == lang[None, :])
           + 0.15 * (rt[:, None] == rt[None, :])
           + 0.15 * ((dec[:, None] == dec[None, :]) & (dec[:, None] != 0)))

    s = np.asarray(pool_scores, dtype=np.float64)
    s_min, s_max = float(s.min()), float(s.max())
    rel = (s - s_min) / (s_max - s_min) if s_max > s_min else np.zeros(p)

    selected = [0]
    available = np.ones(p, dtype=bool)
    available[0] = False
    max_sim = sim[0].copy()
    while len(selected) < n:
        mmr = alpha * rel - (1.0 - alpha) * max_sim
        mmr[~available] = -np.inf
        best = int(np.argmax(mmr))
        selected.append(best)
        available[best] = False
        np.maximum(max_sim, sim[best], out=max_sim)
    return pool_idx[selected]


def rank_batch(scores: np.ndarray, items: Optional[ItemArrays], k: int, params: Optional[ServingParams],
               score_stats: Optional[tuple[float, float]] = None) -> np.ndarray:
    """(B, n) raw scores with -inf for seen/disallowed items -> (B, k) served item indices."""
    if params is None or items is None:
        return _topk_rows(scores, k)
    adjusted = apply_popularity(scores, items, params, score_stats)
    if params.mmr_alpha >= 1.0:
        return _topk_rows(adjusted, k)
    pool = _topk_rows(adjusted, k * max(params.pool_mult, 1))
    out = np.empty((scores.shape[0], min(k, pool.shape[1])), dtype=pool.dtype)
    for row in range(scores.shape[0]):
        pool_row = pool[row]
        pool_scores = adjusted[row, pool_row]
        keep = np.isfinite(pool_scores)
        chosen = mmr_select(pool_row[keep], pool_scores[keep], items, k, params.mmr_alpha)
        if len(chosen) < out.shape[1]:
            # Fewer finite candidates than k: pad with the remaining pool entries (all -inf,
            # i.e. excluded items) so the output shape stays fixed; they never count as hits.
            rest = [i for i in pool_row if i not in set(chosen)]
            chosen = np.concatenate([chosen, rest[: out.shape[1] - len(chosen)]])
        out[row] = chosen
    return out


def rank_row(scores: np.ndarray, items: Optional[ItemArrays], n: int, params: Optional[ServingParams],
             score_stats: Optional[tuple[float, float]] = None) -> np.ndarray:
    """Single-user convenience wrapper; returns up to ``n`` indices of finite-score items."""
    top = rank_batch(np.asarray(scores, dtype=np.float32)[None, :], items, n, params, score_stats)[0]
    return top[np.isfinite(np.asarray(scores)[top])]
