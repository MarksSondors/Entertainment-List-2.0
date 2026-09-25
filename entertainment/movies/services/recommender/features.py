"""Reranker features: one implementation shared by training and serving.

``ItemFeatureTable`` holds per-item arrays aligned to a fit's item index;
``UserContext`` summarizes one user's history (built the same way from a
training frame or from DB reviews); ``compute_features`` turns (context,
candidates, full-catalog stage scores) into the LightGBM feature matrix.

Features are deliberately scale-free (z-scores, rank percentiles, popularity
percentiles) so a booster trained on A-fit models transfers to the A∪B / full
refits it is evaluated and served with. No NaNs anywhere: unknowns get explicit
fill values (the numpy tree evaluator relies on this).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .data_loading import RUNTIME_BUCKETS, TMDB_GENRES, CatalogLookups

FEATURE_SCHEMA_VERSION = 1
FEATURE_NAMES = [
    "ials_z", "ials_rank_pct",
    "ease_z", "ease_rank_pct", "ease_in_vocab",
    "bias_pred", "user_genre_affinity", "item_bias",
    "pop_pct", "log_tmdb_votes", "tmdb_vote_avg",
    "release_year", "age_at_query",
    "user_log_n", "user_mean_rating", "user_pos_frac",
    "cf_sim_recent", "genre_match_recent",
    "src_ials", "src_ease", "src_pop",
]
RECENT_POSITIVES = 20
_SECONDS_PER_YEAR = 365.25 * 86400


@dataclass
class ItemFeatureTable:
    base_pred: np.ndarray      # (n,) global + year bias + item bias
    item_bias: np.ndarray      # (n,)
    year: np.ndarray           # (n,) release year, -1 unknown
    genre_mat: np.ndarray      # (n, G) float32 0/1
    decade_col: np.ndarray     # (n,) column index into the decade block of the user vector, -1 none
    lang_col: np.ndarray       # (n,) column index into the language block, -1 none
    rt_col: np.ndarray         # (n,) column index into the runtime block
    decades: list              # decade keys of the user category vector
    languages: list            # language keys of the user category vector
    pop_pct: np.ndarray        # (n,) interaction-share popularity within the fit
    log_votes: np.ndarray      # (n,) log1p(TMDB vote_count)
    vote_avg: np.ndarray       # (n,) TMDB vote average
    unit_factors: np.ndarray   # (n, k) L2-normalized ranking item factors

    @classmethod
    def build(cls, idx_to_item: np.ndarray, catalog: CatalogLookups, biases: dict, pop_pct: np.ndarray,
              item_factors: np.ndarray) -> "ItemFeatureTable":
        n = len(idx_to_item)
        decades = sorted(int(d) for d in (biases.get("user_decade_biases") or {}))
        languages = sorted(str(l) for l in (biases.get("user_language_biases") or {}))
        dec_col = {d: i for i, d in enumerate(decades)}
        lang_col = {l: i for i, l in enumerate(languages)}
        rt_col_map = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}
        genre_col = {g: i for i, g in enumerate(TMDB_GENRES)}
        year_b = biases.get("year_biases") or {}
        item_b = biases.get("item_biases") or {}
        g_mean = float(biases.get("global_mean", 3.5))

        base = np.zeros(n); ib = np.zeros(n); year = np.full(n, -1.0)
        genre_mat = np.zeros((n, len(TMDB_GENRES)), dtype=np.float32)
        d_col = np.full(n, -1, np.int64); l_col = np.full(n, -1, np.int64); r_col = np.zeros(n, np.int64)
        log_votes = np.zeros(n); vote_avg = np.zeros(n)
        for i, tid in enumerate(int(t) for t in idx_to_item):
            y = catalog.tmdb_to_year.get(tid)
            ib[i] = float(item_b.get(tid, 0.0))
            base[i] = g_mean + ib[i] + (float(year_b.get(int(y), 0.0)) if y is not None else 0.0)
            if y is not None:
                year[i] = float(y)
                d_col[i] = dec_col.get((int(y) // 10) * 10, -1)
            for g in catalog.tmdb_to_genres.get(tid) or []:
                c = genre_col.get(g)
                if c is not None:
                    genre_mat[i, c] = 1.0
            l_col[i] = lang_col.get(catalog.tmdb_to_language.get(tid, "en"), -1)
            r_col[i] = rt_col_map.get(catalog.tmdb_to_runtime_bucket.get(tid, "standard"), rt_col_map["standard"])
            vote = catalog.tmdb_vote_data.get(tid)
            if vote is not None:
                vote_avg[i] = float(vote[0])
                log_votes[i] = float(np.log1p(max(int(vote[1]), 0)))
        norms = np.linalg.norm(item_factors, axis=1, keepdims=True)
        unit = (item_factors / np.where(norms > 0, norms, 1.0)).astype(np.float32)
        return cls(base_pred=base, item_bias=ib, year=year, genre_mat=genre_mat, decade_col=d_col,
                   lang_col=l_col, rt_col=r_col, decades=decades, languages=languages,
                   pop_pct=np.asarray(pop_pct, dtype=np.float64), log_votes=log_votes, vote_avg=vote_avg,
                   unit_factors=unit)


@dataclass
class UserContext:
    user_factor: np.ndarray
    user_bias: float
    genre_bias: np.ndarray     # (G,)
    decade_bias: np.ndarray    # (len(table.decades),)
    lang_bias: np.ndarray      # (len(table.languages),)
    rt_bias: np.ndarray        # (len(RUNTIME_BUCKETS),)
    n_ratings: int
    mean_rating: float
    pos_frac: float
    recent_pos_idx: np.ndarray  # up to RECENT_POSITIVES most recent positive item indices
    query_year: float


def user_context(
    user_factor: np.ndarray,
    table: ItemFeatureTable,
    *,
    ratings: np.ndarray,
    item_idx: np.ndarray,
    timestamps: np.ndarray,
    query_ts: float,
    positive_threshold: float,
    bias_lookup: Callable[[str, object], float],
    user_bias: float,
) -> UserContext:
    """Build a user's context from their rating history (0-5 scale; ``item_idx`` -1 for
    items outside the fit). ``bias_lookup(kind, key)`` returns the user's category bias
    (e.g. ("user_genre_biases", "Drama")) — training reads the bias dicts, serving the
    overlay-first lookup, so both produce the same context for the same user."""
    ratings = np.asarray(ratings, dtype=np.float64)
    item_idx = np.asarray(item_idx, dtype=np.int64)
    timestamps = np.asarray(timestamps, dtype=np.float64)
    pos = (ratings >= positive_threshold) & (item_idx >= 0)
    order = np.argsort(timestamps[pos], kind="stable")[::-1][:RECENT_POSITIVES]
    return UserContext(
        user_factor=np.asarray(user_factor, dtype=np.float32),
        user_bias=float(user_bias),
        genre_bias=np.array([bias_lookup("user_genre_biases", g) for g in TMDB_GENRES]),
        decade_bias=np.array([bias_lookup("user_decade_biases", d) for d in table.decades]),
        lang_bias=np.array([bias_lookup("user_language_biases", l) for l in table.languages]),
        rt_bias=np.array([bias_lookup("user_runtime_biases", r) for r in RUNTIME_BUCKETS]),
        n_ratings=int(len(ratings)),
        mean_rating=float(ratings.mean()) if len(ratings) else 0.0,
        pos_frac=float((ratings >= positive_threshold).mean()) if len(ratings) else 0.0,
        recent_pos_idx=item_idx[pos][order],
        query_year=1970.0 + float(query_ts) / _SECONDS_PER_YEAR,
    )


def dict_bias_lookup(biases: dict, user_id: str) -> Callable[[str, object], float]:
    """``bias_lookup`` over plain bias dicts (training)."""
    def lookup(kind: str, key) -> float:
        return float((biases.get(kind) or {}).get(key, {}).get(user_id, 0.0))
    return lookup


def _z_and_rank(full: np.ndarray, cand: np.ndarray, valid: Optional[np.ndarray] = None):
    """z-score and rank percentile (1 = best) of ``full[cand]`` against the finite
    entries of ``full`` (optionally restricted to ``valid``)."""
    ref = full[np.isfinite(full)] if valid is None else full[valid]
    if ref.size == 0:
        return np.zeros(len(cand)), np.zeros(len(cand))
    mean, std = float(ref.mean()), float(ref.std()) or 1.0
    vals = full[cand]
    srt = np.sort(ref)
    rank = np.searchsorted(srt, vals, side="right") / len(srt)
    return (vals - mean) / std, rank


def compute_features(
    ctx: UserContext,
    cand_idx: np.ndarray,
    src: np.ndarray,
    table: ItemFeatureTable,
    ials_full: np.ndarray,
    ease_full: Optional[np.ndarray],
) -> np.ndarray:
    """(n_cand, len(FEATURE_NAMES)) float32. ``ials_full`` / ``ease_full`` are the user's
    scores over the fit's whole item space (EASE: NaN outside its vocabulary);
    ``src`` is the (n_cand, 3) bool source matrix from ``generate_candidates``."""
    c = np.asarray(cand_idx, dtype=np.int64)
    n = len(c)
    F = np.zeros((n, len(FEATURE_NAMES)), dtype=np.float64)

    F[:, 0], F[:, 1] = _z_and_rank(ials_full, c)
    if ease_full is not None:
        in_vocab = ~np.isnan(ease_full)
        ez, er = _z_and_rank(np.nan_to_num(ease_full, nan=-np.inf), c, valid=in_vocab)
        iv = in_vocab[c]
        F[:, 2] = np.where(iv, ez, 0.0)
        F[:, 3] = np.where(iv, er, 0.0)
        F[:, 4] = iv

    genre_aff = table.genre_mat[c].astype(np.float64) @ ctx.genre_bias
    cat = genre_aff.copy()
    d = table.decade_col[c]
    cat += np.where(d >= 0, ctx.decade_bias[np.maximum(d, 0)] if len(ctx.decade_bias) else 0.0, 0.0)
    l = table.lang_col[c]
    cat += np.where(l >= 0, ctx.lang_bias[np.maximum(l, 0)] if len(ctx.lang_bias) else 0.0, 0.0)
    cat += ctx.rt_bias[table.rt_col[c]]
    F[:, 5] = table.base_pred[c] + ctx.user_bias + cat
    F[:, 6] = genre_aff
    F[:, 7] = table.item_bias[c]

    F[:, 8] = table.pop_pct[c]
    F[:, 9] = table.log_votes[c]
    F[:, 10] = table.vote_avg[c]
    year = table.year[c]
    F[:, 11] = year
    F[:, 12] = np.where(year > 0, ctx.query_year - year, -1.0)

    F[:, 13] = np.log1p(ctx.n_ratings)
    F[:, 14] = ctx.mean_rating
    F[:, 15] = ctx.pos_frac

    if len(ctx.recent_pos_idx):
        centroid = table.unit_factors[ctx.recent_pos_idx].mean(axis=0)
        norm = float(np.linalg.norm(centroid)) or 1.0
        F[:, 16] = table.unit_factors[c] @ (centroid / norm)
        genre_hist = table.genre_mat[ctx.recent_pos_idx].mean(axis=0)
        n_genres = np.maximum(table.genre_mat[c].sum(axis=1), 1.0)
        F[:, 17] = (table.genre_mat[c] @ genre_hist) / n_genres

    F[:, 18:21] = src
    return F.astype(np.float32)


def generate_candidates(
    ials_full: np.ndarray,
    ease_full: Optional[np.ndarray],
    pop_scores: np.ndarray,
    excluded: np.ndarray,
    *,
    k_ials: int = 200,
    k_ease: int = 200,
    k_pop: int = 50,
) -> tuple[np.ndarray, np.ndarray]:
    """Union of the top-k by iALS, EASE and popularity over non-excluded items.

    ``excluded`` is a bool mask over the full item space (seen / not servable). Ranks
    are computed over the full vocabulary so the rank features mean the same thing in
    training and in a serving scope restricted to a small local catalog.
    Returns (candidate indices, (n, 3) bool source flags [ials, ease, pop]).
    """
    def top(scores: np.ndarray, k: int) -> np.ndarray:
        s = np.where(excluded | ~np.isfinite(scores), -np.inf, scores)
        n_ok = int(np.isfinite(s).sum())
        k = min(k, n_ok)
        if k <= 0:
            return np.zeros(0, dtype=np.int64)
        return np.argpartition(-s, kth=k - 1)[:k]

    sources = [top(ials_full, k_ials),
               top(np.nan_to_num(ease_full, nan=-np.inf), k_ease) if ease_full is not None else np.zeros(0, np.int64),
               top(pop_scores.astype(np.float64), k_pop)]
    cand = np.unique(np.concatenate(sources))
    src = np.stack([np.isin(cand, s) for s in sources], axis=1)
    return cand, src
