"""Second-stage LambdaRank reranker.

Training (on the training machine, needs lightgbm): candidates + features come from
models fit on A, labels from each user's B ratings (>= 4.5 -> 2, >= 3.5 -> 1, else 0).
Evaluation applies the A->B booster to features from the A∪B fit, scored on C.

Serving never imports lightgbm: the booster is flattened into numpy arrays
(``booster_to_numpy_trees``) and evaluated by ``predict_numpy_trees``; the bundle
only ships the reranker if the numpy evaluator reproduces LightGBM's predictions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    ItemFeatureTable,
    compute_features,
    dict_bias_lookup,
    generate_candidates,
    user_context,
)

logger = logging.getLogger(__name__)

LGB_PARAMS = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "eval_at": [10],
    "label_gain": [0, 1, 3],
    "num_leaves": 63,
    "learning_rate": 0.05,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambdarank_truncation_level": 30,
    "use_missing": False,
    "zero_as_missing": False,
    "verbosity": -1,
    "seed": 42,
}


class RerankFeatureBuilder:
    """Candidates + features for users of one fit (A for training, A∪B for eval)."""

    def __init__(self, models, fit_df: pd.DataFrame, biases: dict, catalog, X_pos, *,
                 k_ials: int = 200, k_ease: int = 200, k_pop: int = 50):
        from .evaluation import build_train_csr, popularity_percentile

        r = models.ranking
        self.models = models
        self.biases = biases
        self.ease = models.extras.get("ease")
        self.X_pos = X_pos.tocsr() if X_pos is not None else None
        if self.ease is not None and self.X_pos is None:
            raise ValueError("EASE features need the positives matrix X_pos")
        self.threshold = models.params.positive_threshold
        self.k = (k_ials, k_ease, k_pop)
        self.idx_to_user = {v: k for k, v in r.user_to_idx.items()}
        self.table = ItemFeatureTable.build(models.idx_to_item, catalog, biases,
                                            popularity_percentile(models.item_counts), r.item_factors)
        self.seen = build_train_csr(fit_df, r.user_to_idx, r.item_to_idx)
        self.pop_scores = models.item_counts.astype(np.float64)

        # Per-user rating history (all ratings, not just positives), grouped by user index.
        users = fit_df["user_id"].astype(str).map(r.user_to_idx)
        keep = users.notna().to_numpy()
        u = users[keep].to_numpy(dtype=np.int64)
        items = fit_df["tmdb_id"][keep].map(r.item_to_idx).fillna(-1).to_numpy(dtype=np.int64)
        order = np.argsort(u, kind="stable")
        self._h_items = items[order]
        self._h_ratings = fit_df["rating"].to_numpy(dtype=np.float64)[keep][order]
        self._h_ts = fit_df["timestamp"].to_numpy(dtype=np.float64)[keep][order]
        self._h_indptr = np.concatenate([[0], np.cumsum(np.bincount(u, minlength=len(r.user_to_idx)))])

    def history(self, u: int):
        s, e = self._h_indptr[u], self._h_indptr[u + 1]
        return self._h_ratings[s:e], self._h_items[s:e], self._h_ts[s:e]

    def for_user(self, u: int, query_ts: Optional[float] = None) -> tuple[np.ndarray, np.ndarray]:
        """(candidate item indices, feature matrix) for user index ``u``."""
        r = self.models.ranking
        ratings, items, ts = self.history(u)
        user_id = self.idx_to_user[int(u)]
        ctx = user_context(
            r.user_factors[u], self.table, ratings=ratings, item_idx=items, timestamps=ts,
            query_ts=float(query_ts if query_ts is not None else (ts.max() if len(ts) else 0.0)),
            positive_threshold=self.threshold, bias_lookup=dict_bias_lookup(self.biases, user_id),
            user_bias=float((self.biases.get("user_biases") or {}).get(user_id, 0.0)),
        )
        ials_full = r.item_factors @ r.user_factors[u]
        ease_full = None
        if self.ease is not None:
            ease_full = self.ease.score_one(self.X_pos.indices[self.X_pos.indptr[u]:self.X_pos.indptr[u + 1]])
        excluded = np.zeros(len(ials_full), dtype=bool)
        excluded[self.seen.indices[self.seen.indptr[u]:self.seen.indptr[u + 1]]] = True
        cand, src = generate_candidates(ials_full, ease_full, self.pop_scores, excluded,
                                        k_ials=self.k[0], k_ease=self.k[1], k_pop=self.k[2])
        return cand, compute_features(ctx, cand, src, self.table, ials_full, ease_full)


def _labels_by_user(label_df: pd.DataFrame, user_to_idx: dict, item_to_idx: dict,
                    threshold: float, strong: float) -> tuple[dict, dict]:
    """({user_idx: {item_idx: gain}}, {user_idx: first label timestamp})."""
    users = label_df["user_id"].astype(str).map(user_to_idx)
    items = label_df["tmdb_id"].map(item_to_idx)
    keep = users.notna().to_numpy() & items.notna().to_numpy()
    u = users[keep].to_numpy(dtype=np.int64)
    i = items[keep].to_numpy(dtype=np.int64)
    rating = label_df["rating"].to_numpy()[keep]
    ts = label_df["timestamp"].to_numpy(dtype=np.float64)[keep]
    gain = np.where(rating >= strong, 2, np.where(rating >= threshold, 1, 0))
    labels: dict[int, dict[int, int]] = {}
    first_ts: dict[int, float] = {}
    for uu, ii, g, t in zip(u, i, gain, ts):
        labels.setdefault(int(uu), {})[int(ii)] = int(g)
        first_ts[int(uu)] = min(first_ts.get(int(uu), t), t)
    return labels, first_ts


def sample_users(labels: dict, builder: RerankFeatureBuilder, n: int, seed: int = 42,
                 exclude: Optional[set] = None) -> np.ndarray:
    """Up to ``n`` users with >= 1 positive label, stratified by history-length quartile
    (heavy users don't dominate); local (loc_) users are always included."""
    exclude = exclude or set()
    eligible = np.array([u for u, lab in labels.items() if u not in exclude and any(g > 0 for g in lab.values())],
                        dtype=np.int64)
    if len(eligible) <= n:
        return eligible
    local = np.array([u for u in eligible if builder.idx_to_user[int(u)].startswith("loc_")], dtype=np.int64)
    rest = np.setdiff1d(eligible, local)
    lengths = np.diff(builder._h_indptr)[rest]
    quart = np.digitize(lengths, np.quantile(lengths, [0.25, 0.5, 0.75]))
    rng = np.random.default_rng(seed)
    per = max((n - len(local)) // 4, 1)
    picks = [rng.choice(rest[quart == q], size=min(per, int((quart == q).sum())), replace=False)
             for q in range(4) if (quart == q).any()]
    return np.concatenate([local, *picks])


@dataclass
class TrainingData:
    X: np.ndarray
    y: np.ndarray
    group: np.ndarray        # rows per user, in order
    users: np.ndarray
    cand_recall: float       # share of label positives that made it into the candidate set


def build_training_data(builder: RerankFeatureBuilder, label_df: pd.DataFrame, users: np.ndarray,
                        *, strong: float = 4.5, labels: Optional[dict] = None,
                        first_ts: Optional[dict] = None, report: Callable[[str], None] = logger.info
                        ) -> TrainingData:
    r = builder.models.ranking
    if labels is None:
        labels, first_ts = _labels_by_user(label_df, r.user_to_idx, r.item_to_idx, builder.threshold, strong)
    Xs, ys, groups, kept = [], [], [], []
    n_pos = n_pos_in_cand = 0
    for j, u in enumerate(users):
        lab = labels.get(int(u), {})
        cand, F = builder.for_user(int(u), query_ts=first_ts.get(int(u)))
        y = np.array([lab.get(int(c), 0) for c in cand], dtype=np.int32)
        pos_total = sum(1 for g in lab.values() if g > 0)
        n_pos += pos_total
        n_pos_in_cand += int((y > 0).sum())
        if (y > 0).any():
            Xs.append(F); ys.append(y); groups.append(len(cand)); kept.append(int(u))
        if (j + 1) % 5000 == 0:
            report(f"  reranker features: {j + 1}/{len(users)} users")
    recall = n_pos_in_cand / max(n_pos, 1)
    return TrainingData(X=np.vstack(Xs), y=np.concatenate(ys), group=np.array(groups),
                        users=np.array(kept), cand_recall=recall)


def train_lambdarank(data: TrainingData, *, val_frac: float = 0.2, num_boost_round: int = 1000,
                     early_stopping: int = 50, params: Optional[dict] = None, seed: int = 42):
    """LambdaRank with a group-level train/validation split for early stopping."""
    import lightgbm as lgb

    rng = np.random.default_rng(seed)
    n_groups = len(data.group)
    is_val = np.zeros(n_groups, dtype=bool)
    is_val[rng.choice(n_groups, size=max(int(val_frac * n_groups), 1), replace=False)] = True
    row_group = np.repeat(np.arange(n_groups), data.group)
    tr_rows, va_rows = ~is_val[row_group], is_val[row_group]
    dtrain = lgb.Dataset(data.X[tr_rows], data.y[tr_rows], group=data.group[~is_val],
                         feature_name=FEATURE_NAMES, free_raw_data=False)
    dval = lgb.Dataset(data.X[va_rows], data.y[va_rows], group=data.group[is_val],
                       feature_name=FEATURE_NAMES, reference=dtrain, free_raw_data=False)
    booster = lgb.train({**LGB_PARAMS, **(params or {})}, dtrain, num_boost_round=num_boost_round,
                        valid_sets=[dval], callbacks=[lgb.early_stopping(early_stopping, verbose=False)])
    return booster, data.X[va_rows]


# ---------------------------------------------------------------------------
# numpy tree evaluator (serving never imports lightgbm)
# ---------------------------------------------------------------------------

def booster_to_numpy_trees(booster) -> dict:
    """Flatten a LightGBM booster (numerical splits, no missing handling) into arrays.

    Internal nodes are numbered globally; a child reference >= 0 is an internal node,
    < 0 encodes leaf ``~ref``.
    """
    dump = booster.dump_model(num_iteration=booster.best_iteration or None)
    feat, thr, left, right, leaf_value, roots = [], [], [], [], [], []
    max_depth = 0

    def walk(node, depth):
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        if "leaf_index" in node or "leaf_value" in node and "split_index" not in node:
            leaf_value.append(float(node["leaf_value"]))
            return ~(len(leaf_value) - 1)
        if node.get("decision_type", "<=") != "<=":
            raise ValueError(f"unsupported decision type {node.get('decision_type')}")
        me = len(feat)
        feat.append(int(node["split_feature"])); thr.append(float(node["threshold"]))
        left.append(0); right.append(0)
        left[me] = walk(node["left_child"], depth + 1)
        right[me] = walk(node["right_child"], depth + 1)
        return me

    for tree in dump["tree_info"]:
        roots.append(walk(tree["tree_structure"], 0))
    return {
        "format": "lgbm_numpy_v1",
        "split_feature": np.array(feat, dtype=np.int32),
        "threshold": np.array(thr, dtype=np.float64),
        "left": np.array(left, dtype=np.int32),
        "right": np.array(right, dtype=np.int32),
        "leaf_value": np.array(leaf_value, dtype=np.float64),
        "roots": np.array(roots, dtype=np.int32),
        "max_depth": int(max_depth),
    }


def predict_numpy_trees(trees: dict, X: np.ndarray) -> np.ndarray:
    """Sum of leaf values over all trees, evaluated for all rows/trees one depth level at a time."""
    X = np.asarray(X, dtype=np.float64)
    feat, thr, left, right = trees["split_feature"], trees["threshold"], trees["left"], trees["right"]
    cur = np.tile(trees["roots"].astype(np.int64), (X.shape[0], 1))
    for _ in range(trees["max_depth"] + 1):
        internal = cur >= 0
        if not internal.any():
            break
        rows, cols = np.nonzero(internal)
        node = cur[rows, cols]
        go_left = X[rows, feat[node]] <= thr[node]
        cur[rows, cols] = np.where(go_left, left[node], right[node])
    return trees["leaf_value"][~cur].sum(axis=1)


def check_parity(booster, trees: dict, X: np.ndarray, tol: float = 1e-5) -> float:
    """Max |LightGBM - numpy| over ``X``; raises if above ``tol``."""
    ref = booster.predict(X, num_iteration=booster.best_iteration or None)
    diff = float(np.max(np.abs(ref - predict_numpy_trees(trees, X)))) if len(X) else 0.0
    if diff > tol:
        raise AssertionError(f"numpy tree evaluator differs from LightGBM by {diff:.3g}")
    return diff


def reranker_to_bundle(trees: dict, *, best_iteration: int, n_train_users: int, cand_k: tuple,
                       model_str: Optional[str] = None) -> dict:
    return {
        "format": trees["format"],
        "feature_names": list(FEATURE_NAMES),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "trees": trees,
        "candidate_k": {"ials": cand_k[0], "ease": cand_k[1], "pop": cand_k[2]},
        "best_iteration": int(best_iteration),
        "trained_on": "A->B",
        "n_train_users": int(n_train_users),
        "model_str": model_str,
    }


def reranker_scorer(builder: RerankFeatureBuilder, trees: dict, first_ts: Optional[dict] = None):
    """``evaluation.ScoreBatch`` over the reranker: candidates get tree scores, everything
    else -inf (can't be recommended)."""
    first_ts = first_ts or {}
    n_items = len(builder.models.ranking.item_to_idx)

    def score(users: np.ndarray) -> np.ndarray:
        out = np.full((len(users), n_items), -np.inf, dtype=np.float32)
        for row, u in enumerate(users):
            cand, F = builder.for_user(int(u), query_ts=first_ts.get(int(u)))
            out[row, cand] = predict_numpy_trees(trees, F)
        return out
    return score
