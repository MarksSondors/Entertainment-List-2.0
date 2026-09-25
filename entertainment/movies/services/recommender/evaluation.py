"""Evaluation: RMSE/MAE for the displayed rating + full-catalog ranking metrics.

Ranking evaluation is stage-agnostic: any model is a ``score_batch(user_idxs) ->
(B, n_items)`` callable in the fit's item-index space (see ``factor_scorer`` /
``popularity_scorer``). ``EvalTargets`` fixes the user sample and relevance labels
once per (fit, eval split), so every stage is scored on identical users and the
comparison table is apples-to-apples. Serving transforms (popularity penalty,
MMR) are applied through ``scoring.rank_batch`` exactly as serving applies them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix, csr_matrix

from .biases import build_feature_blocks
from .data_loading import TMDB_GENRES
from .scoring import ItemArrays, ServingParams, rank_batch
from .splits import three_way_temporal_split

logger = logging.getLogger(__name__)

# Bump whenever the meaning of the stored metrics changes. The champion/challenger
# gate only compares models evaluated under the same protocol.
#   1-2: pre-v5.1 (leaked: the evaluated model had been refit on the eval rows)
#   3:   A∪B-fit models scored on held-out C (see splits.py)
EVAL_PROTOCOL = 3

ScoreBatch = Callable[[np.ndarray], np.ndarray]


@dataclass
class EvalResult:
    rmse: float = 0.0
    mae: float = 0.0
    ndcg_at_k: float = 0.0
    ndcg_graded_at_k: float = 0.0
    recall_at_k: float = 0.0
    hit_rate_at_k: float = 0.0
    mrr: float = 0.0
    coverage_at_k: float = 0.0
    novelty: float = 0.0   # mean interaction-share popularity of recommended items (lower = more discovery)
    n_test_users: int = 0
    n_test_ratings: int = 0
    per_cohort: dict[str, "EvalResult"] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "rmse": self.rmse,
            "mae": self.mae,
            "ndcg_at_k": self.ndcg_at_k,
            "ndcg_graded_at_k": self.ndcg_graded_at_k,
            "recall_at_k": self.recall_at_k,
            "hit_rate_at_k": self.hit_rate_at_k,
            "mrr": self.mrr,
            "coverage_at_k": self.coverage_at_k,
            "novelty": self.novelty,
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
    """(train, val) where val is each user's latest ``val_fraction`` of ratings.

    Thin wrapper over ``splits.three_way_temporal_split`` with no B part, i.e.
    (A∪B, C) of the full protocol.
    """
    s = three_way_temporal_split(df, c_frac=val_fraction, b_frac=0.0,
                                 min_user_ratings=min_user_ratings_for_val)
    return s.ab(), s.c()


def global_temporal_split(
    df: pd.DataFrame,
    *,
    cutoff_quantile: float = 0.8,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A single global timestamp cutoff (train = everything before, val = everything
    at/after) — a "replay" of what would have happened after date X, including
    users/items that only appear after the cutoff.
    """
    if df.empty:
        return df, df
    cutoff = float(df["timestamp"].quantile(cutoff_quantile))
    train_df = df[df["timestamp"] < cutoff].reset_index(drop=True)
    val_df = df[df["timestamp"] >= cutoff].reset_index(drop=True)
    logger.info(
        "Global temporal split (cutoff=%.0f, q=%.2f): %d train / %d val (%.1f%%)",
        cutoff, cutoff_quantile, len(train_df), len(val_df), 100.0 * len(val_df) / max(len(df), 1),
    )
    return train_df, val_df


# ---------------------------------------------------------------------------
# Pointwise (displayed rating)
# ---------------------------------------------------------------------------

def predict_explicit(
    df: pd.DataFrame,
    biases: dict,
    *,
    user_to_idx: Optional[dict] = None,
    item_to_idx: Optional[dict] = None,
    user_factors: Optional[np.ndarray] = None,
    item_factors: Optional[np.ndarray] = None,
    factor_blend_weight: float = 1.0,
) -> np.ndarray:
    """Predict explicit ratings using the bias hierarchy + (optional) weighted factor dot.

    Mirrors ``MovieRecommender.predict_rating`` so RMSE measures the displayed rating.
    """
    n = len(df)
    pred = np.full(n, biases["global_mean"], dtype=np.float32)
    pred += df["year"].map(biases["year_biases"]).fillna(0).astype(np.float32).values
    pred += df["tmdb_id"].map(biases["item_biases"]).fillna(0).astype(np.float32).values
    user_ids = df["user_id"].astype(str)
    pred += user_ids.map(biases["user_biases"]).fillna(0).astype(np.float32).values

    # Genre (multi-hot) — vectorized via a sparse membership matrix (build_feature_blocks's
    # genre block is always the first len(TMDB_GENRES) columns).
    if biases.get("user_genre_biases"):
        genre_col_idx = {g: i for i, g in enumerate(TMDB_GENRES)}
        genre_membership, _ = build_feature_blocks(df)
        user_id_vals = user_ids.to_numpy()
        for g, bias_map in biases["user_genre_biases"].items():
            col = genre_col_idx.get(g)
            if col is None:
                continue
            mask = genre_membership[:, col].toarray().ravel() > 0
            if mask.any():
                pred[mask] += pd.Series(user_id_vals[mask]).map(bias_map).fillna(0).astype(np.float32).values

    for key, column in (("user_decade_biases", "decade"),
                        ("user_language_biases", "language"),
                        ("user_runtime_biases", "runtime_bucket")):
        if not biases.get(key) or column not in df.columns:
            continue
        col_vals = df[column].to_numpy()
        for cat, bias_map in biases[key].items():
            mask = col_vals == cat
            if mask.any():
                pred[mask] += user_ids[mask].map(bias_map).fillna(0).astype(np.float32).values

    # Per-user linear time-drift (see biases.compute_user_time_trend_biases): the row's
    # timestamp normalized to the user's own [t_min, t_max] window, clamped to [-1, 1].
    trend = biases.get("user_time_trend")
    norm = biases.get("user_time_norm")
    if trend and norm and "timestamp" in df.columns:
        slope = user_ids.map(trend).fillna(0).astype(np.float64).to_numpy()
        bounds = user_ids.map(norm)
        has = bounds.notna().to_numpy() & (slope != 0)
        if has.any():
            b = np.array(bounds[has].tolist(), dtype=np.float64)
            t_min, t_max = b[:, 0], b[:, 1]
            span = np.where(t_max > t_min, t_max - t_min, 1.0)
            ts = df["timestamp"].to_numpy()[has].astype(np.float64)
            t_norm = np.clip((ts - t_min) / span - 0.5, -1.0, 1.0)
            pred[has] += (slope[has] * t_norm).astype(np.float32)

    if user_factors is not None and item_factors is not None and user_to_idx and item_to_idx and factor_blend_weight:
        u_idx = user_ids.map(user_to_idx).to_numpy()
        i_idx = df["tmdb_id"].map(item_to_idx).to_numpy()
        valid = (~pd.isna(u_idx)) & (~pd.isna(i_idx))
        if valid.any():
            uv = u_idx[valid].astype(int)
            iv = i_idx[valid].astype(int)
            inter = np.einsum("ij,ij->i", user_factors[uv], item_factors[iv]).astype(np.float32)
            pred[valid] += factor_blend_weight * inter
    return pred


def fit_explicit_blend_weight(
    val_df: pd.DataFrame,
    biases: dict,
    *,
    user_to_idx: dict[str, int],
    item_to_idx: dict[int, int],
    user_factors: np.ndarray,
    item_factors: np.ndarray,
) -> float:
    """Learn a single scalar blending the bias-hierarchy prediction with the factor dot
    product, minimizing squared error on ``val_df``:

        rating ≈ bias_pred + alpha * dot(user_factors[u], item_factors[i])

    Closed form: alpha* = sum(resid * dot) / sum(dot^2). ``val_df`` must be held out
    from both the biases and the factors (e.g. B with A-fit models), otherwise alpha
    is fit on in-sample residuals.
    """
    sub = val_df[val_df["user_id"].astype(str).isin(user_to_idx) & val_df["tmdb_id"].isin(item_to_idx)]
    if len(sub) < 50:
        return 0.0

    bias_pred = predict_explicit(sub, biases)
    resid = sub["rating"].values.astype(np.float32) - bias_pred

    u_idx = sub["user_id"].astype(str).map(user_to_idx).values.astype(int)
    i_idx = sub["tmdb_id"].map(item_to_idx).values.astype(int)
    dot = np.einsum("ij,ij->i", user_factors[u_idx], item_factors[i_idx]).astype(np.float32)

    denom = float(np.sum(dot.astype(np.float64) ** 2))
    if denom < 1e-6:
        return 0.0
    alpha = float(np.sum(resid.astype(np.float64) * dot) / denom)
    alpha = float(np.clip(alpha, -1.0, 1.0))
    logger.info("Fitted explicit blend weight alpha=%.4f on %d held-out rows", alpha, len(sub))
    return alpha


def evaluate_pointwise(
    eval_df: pd.DataFrame,
    biases: dict,
    **factor_kwargs,
) -> tuple[float, float]:
    """(RMSE, MAE) of ``predict_explicit`` on eval_df, clipped to the 0.5-5 display range."""
    if eval_df.empty:
        return 0.0, 0.0
    pred = np.clip(predict_explicit(eval_df, biases, **factor_kwargs), 0.5, 5.0)
    err = pred - eval_df["rating"].values.astype(np.float32)
    return float(np.sqrt(np.mean(err ** 2))), float(np.mean(np.abs(err)))


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def build_train_csr(
    train_df: pd.DataFrame,
    user_to_idx: dict[str, int],
    item_to_idx: dict[int, int],
) -> csr_matrix:
    """(n_users, n_items) seen-interactions CSR used to mask already-rated items out of
    ranking evaluation. Build once per fit and reuse across stages/trials."""
    users = train_df["user_id"].astype(str)
    tr_mask = users.isin(user_to_idx).to_numpy() & train_df["tmdb_id"].isin(item_to_idx).to_numpy()
    if not tr_mask.any():
        return csr_matrix((len(user_to_idx), len(item_to_idx)), dtype=np.float32)
    u_arr = users[tr_mask].map(user_to_idx).to_numpy(dtype=np.int32)
    i_arr = train_df["tmdb_id"][tr_mask].map(item_to_idx).to_numpy(dtype=np.int32)
    m = coo_matrix(
        (np.ones(len(u_arr), dtype=np.float32), (u_arr, i_arr)),
        shape=(len(user_to_idx), len(item_to_idx)),
    ).tocsr()
    m.sum_duplicates()
    return m


@dataclass
class EvalTargets:
    """Held-out relevance labels for a fixed user sample, in a fit's index space.

    ``relevant[u]`` maps item index -> graded gain (1 for rating >= threshold, 2 for
    rating >= strong_threshold). Only (user, item) pairs known to the fit are kept.
    """
    users: np.ndarray                      # (n_users,) int32 user indices, sorted
    user_ids: np.ndarray                   # (n_users,) str ids aligned to ``users``
    relevant: dict[int, dict[int, int]]
    n_items: int
    n_ratings: int

    def subset(self, prefix: str) -> "EvalTargets":
        mask = np.array([str(u).startswith(prefix) for u in self.user_ids], dtype=bool)
        users = self.users[mask]
        relevant = {int(u): self.relevant[int(u)] for u in users}
        return EvalTargets(users=users, user_ids=self.user_ids[mask], relevant=relevant,
                           n_items=self.n_items,
                           n_ratings=int(sum(len(v) for v in relevant.values())))


def build_eval_targets(
    eval_df: pd.DataFrame,
    user_to_idx: dict[str, int],
    item_to_idx: dict[int, int],
    *,
    positive_threshold: float = 3.5,
    strong_threshold: float = 4.5,
    max_users: int = 10_000,
    always_include_prefix: Optional[str] = "loc_",
    seed: int = 42,
) -> EvalTargets:
    """Sample up to ``max_users`` eval users with >= 1 known held-out positive. Users
    whose id starts with ``always_include_prefix`` (local users) are always kept so the
    local cohort is never sampled away."""
    pos = eval_df[eval_df["rating"] >= positive_threshold]
    users = pos["user_id"].astype(str)
    known = users.isin(user_to_idx).to_numpy() & pos["tmdb_id"].isin(item_to_idx).to_numpy()
    pos = pos[known]
    users = users[known]
    if pos.empty:
        return EvalTargets(np.zeros(0, np.int32), np.zeros(0, object), {}, len(item_to_idx), 0)

    u_idx = users.map(user_to_idx).to_numpy(dtype=np.int64)
    i_idx = pos["tmdb_id"].map(item_to_idx).to_numpy(dtype=np.int64)
    gain = np.where(pos["rating"].to_numpy() >= strong_threshold, 2, 1)

    unique_users = np.unique(u_idx)
    if len(unique_users) > max_users:
        idx_to_user = {v: k for k, v in user_to_idx.items()}
        forced = np.array([u for u in unique_users
                           if always_include_prefix and idx_to_user[int(u)].startswith(always_include_prefix)],
                          dtype=np.int64)
        rest = np.setdiff1d(unique_users, forced)
        rng = np.random.default_rng(seed)
        take = rng.choice(rest, size=max(max_users - len(forced), 0), replace=False)
        unique_users = np.sort(np.concatenate([forced, take]))
    keep = np.isin(u_idx, unique_users)

    relevant: dict[int, dict[int, int]] = {}
    for u, i, g in zip(u_idx[keep], i_idx[keep], gain[keep]):
        relevant.setdefault(int(u), {})[int(i)] = int(g)

    idx_to_user = {v: k for k, v in user_to_idx.items()}
    users_sorted = np.array(sorted(relevant), dtype=np.int32)
    return EvalTargets(
        users=users_sorted,
        user_ids=np.array([idx_to_user[int(u)] for u in users_sorted], dtype=object),
        relevant=relevant,
        n_items=len(item_to_idx),
        n_ratings=int(keep.sum()),
    )


def factor_scorer(user_factors: np.ndarray, item_factors: np.ndarray) -> ScoreBatch:
    def score(users: np.ndarray) -> np.ndarray:
        return user_factors[users] @ item_factors.T
    return score


def ease_scorer(ease, X_pos: csr_matrix) -> ScoreBatch:
    """EASE scores from each user's positives; out-of-vocab items can't be recommended."""
    def score(users: np.ndarray) -> np.ndarray:
        s = ease.score_batch(X_pos[users])
        s[np.isnan(s)] = -np.inf
        return s
    return score


def blend_scorer(ials: ScoreBatch, ease, X_pos: csr_matrix, beta: float) -> ScoreBatch:
    """z(iALS) + beta * z(EASE), z-scored per user over each model's own catalog; items
    outside the EASE vocabulary get a neutral EASE term (0)."""
    from .scoring import zscore_rows

    def score(users: np.ndarray) -> np.ndarray:
        out = zscore_rows(ials(users))
        if beta:
            e = ease.score_batch(X_pos[users])
            oov = np.isnan(e)
            e[oov] = -np.inf
            ez = zscore_rows(e)
            ez[oov] = 0.0
            out += beta * ez
        return out
    return score


def popularity_scorer(item_counts: np.ndarray) -> ScoreBatch:
    """MostPop baseline: every user gets the same score = item's positive count in the fit."""
    counts = np.asarray(item_counts, dtype=np.float32)

    def score(users: np.ndarray) -> np.ndarray:
        return np.tile(counts, (len(users), 1))
    return score


def popularity_percentile(item_counts: np.ndarray) -> np.ndarray:
    """Per-item popularity in [0, 1] as the share of all interactions that go to items
    at most as popular as it (1 = the most popular item).

    Interaction-weighted rather than a plain item-rank percentile: the catalog's long
    tail makes nearly every item a recommender surfaces sit above the 99th item-rank
    percentile, which saturates a rank-based novelty metric.
    """
    counts = np.asarray(item_counts, dtype=np.float64)
    total = counts.sum()
    if counts.size == 0 or total <= 0:
        return np.ones_like(counts, dtype=np.float32)
    order = np.argsort(counts, kind="stable")
    cum = np.cumsum(counts[order])
    # ties share the value of the last item in their run
    sorted_counts = counts[order]
    last_of_run = np.searchsorted(sorted_counts, sorted_counts, side="right") - 1
    share = np.empty_like(counts)
    share[order] = cum[last_of_run] / total
    return share.astype(np.float32)


def evaluate_scorer(
    score_batch: ScoreBatch,
    targets: EvalTargets,
    seen_csr: csr_matrix,
    *,
    k: int = 10,
    items: Optional[ItemArrays] = None,
    serving: Optional[ServingParams] = None,
    item_pop_pct: Optional[np.ndarray] = None,
    batch_size: int = 512,
) -> EvalResult:
    """NDCG@K (binary + graded) / Recall@K / HitRate@K / MRR / Coverage@K / novelty.

    Items in ``seen_csr`` (the fit's interactions) are excluded before ranking; when
    ``serving`` is given, the popularity penalty + MMR are applied via ``rank_batch``.
    """
    n = len(targets.users)
    if n == 0:
        return EvalResult()

    discounts = 1.0 / np.log2(np.arange(2, k + 2))
    ndcg_sum = ndcg_g_sum = recall_sum = rr_sum = 0.0
    hits_total = 0
    novelty_sum = 0.0
    novelty_cnt = 0
    recommended = np.zeros(targets.n_items, dtype=bool)

    for start in range(0, n, batch_size):
        batch = targets.users[start:start + batch_size]
        scores = np.asarray(score_batch(batch), dtype=np.float32)
        for row, u in enumerate(batch):
            seen = seen_csr.indices[seen_csr.indptr[u]:seen_csr.indptr[u + 1]]
            if len(seen):
                scores[row, seen] = -np.inf
        top = rank_batch(scores, items, k, serving)

        for row, u in enumerate(batch):
            rel = targets.relevant[int(u)]
            recs = top[row]
            recommended[recs] = True
            if item_pop_pct is not None:
                novelty_sum += float(item_pop_pct[recs].sum())
                novelty_cnt += len(recs)
            gains = np.array([rel.get(int(r), 0) for r in recs], dtype=np.float64)
            hit = (gains > 0).astype(np.float64)
            n_hit = hit.sum()
            if n_hit > 0:
                hits_total += 1
                rr_sum += 1.0 / (int(np.argmax(hit)) + 1)
            recall_sum += n_hit / len(rel)
            n_ideal = min(len(rel), k)
            ndcg_sum += float((hit * discounts[:len(hit)]).sum()) / float(discounts[:n_ideal].sum())
            graded = (2.0 ** gains - 1.0)
            ideal_g = np.sort(2.0 ** np.array(list(rel.values()), dtype=np.float64) - 1.0)[::-1][:k]
            ndcg_g_sum += float((graded * discounts[:len(graded)]).sum()) / float((ideal_g * discounts[:len(ideal_g)]).sum())

    return EvalResult(
        ndcg_at_k=ndcg_sum / n,
        ndcg_graded_at_k=ndcg_g_sum / n,
        recall_at_k=recall_sum / n,
        hit_rate_at_k=hits_total / n,
        mrr=rr_sum / n,
        coverage_at_k=float(recommended.sum()) / max(targets.n_items, 1),
        novelty=novelty_sum / novelty_cnt if novelty_cnt else 0.0,
        n_test_users=n,
        n_test_ratings=targets.n_ratings,
    )


COHORTS = (("loc_", "local"), ("ml_", "movielens"))


def evaluate_stage(
    score_batch: ScoreBatch,
    targets: EvalTargets,
    seen_csr: csr_matrix,
    *,
    cohorts: bool = True,
    **kwargs,
) -> EvalResult:
    """``evaluate_scorer`` over all sampled users plus a per-cohort breakdown."""
    overall = evaluate_scorer(score_batch, targets, seen_csr, **kwargs)
    if cohorts:
        for prefix, label in COHORTS:
            sub = targets.subset(prefix)
            if len(sub.users):
                overall.per_cohort[label] = evaluate_scorer(score_batch, sub, seen_csr, **kwargs)
    return overall


def balanced_objective(result: EvalResult, coverage_floor: Optional[float], penalty: float = 0.5) -> float:
    """The "balanced" selection score: NDCG@10 minus a soft penalty for dropping
    coverage@10 below ``coverage_floor`` (the reference config's coverage)."""
    score = result.ndcg_at_k
    if coverage_floor is not None:
        score -= penalty * max(0.0, coverage_floor - result.coverage_at_k)
    return score


def format_stage_table(
    results: dict[str, EvalResult],
    *,
    baseline: Optional[str] = "mostpop",
    reference: Optional[str] = None,
) -> str:
    """Plain-text comparison table with Δ% vs ``baseline`` and ``reference`` stages."""
    base = results.get(baseline) if baseline else None
    ref = results.get(reference) if reference else None

    def delta(val: float, other: Optional[EvalResult]) -> str:
        if other is None or not other.ndcg_at_k:
            return "      "
        return f"{100.0 * (val - other.ndcg_at_k) / other.ndcg_at_k:+6.1f}%"

    header = (f"{'stage':<14} {'NDCG@10':>8} {'gNDCG':>7} {'Recall':>7} {'HR':>6} {'MRR':>6} "
              f"{'Cov@10':>7} {'Novelty':>7} {'vs ' + (baseline or '-'):>11} {'vs ' + (reference or '-'):>11}")
    lines = [header, "-" * len(header)]
    for name, r in results.items():
        lines.append(
            f"{name:<14} {r.ndcg_at_k:8.4f} {r.ndcg_graded_at_k:7.4f} {r.recall_at_k:7.4f} "
            f"{r.hit_rate_at_k:6.3f} {r.mrr:6.3f} {r.coverage_at_k:7.4f} {r.novelty:7.3f} "
            f"{delta(r.ndcg_at_k, base):>11} {delta(r.ndcg_at_k, ref):>11}"
        )
        local = r.per_cohort.get("local")
        if local is not None:
            lines.append(f"{'  - local':<14} {local.ndcg_at_k:8.4f} {local.ndcg_graded_at_k:7.4f} "
                         f"{local.recall_at_k:7.4f} {local.hit_rate_at_k:6.3f} {local.mrr:6.3f}"
                         f"   ({local.n_test_users} users, {local.n_test_ratings} held-out positives)")
    return "\n".join(lines)
