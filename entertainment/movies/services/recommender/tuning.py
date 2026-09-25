"""Hyperparameter tuning on the A -> B protocol (see splits.py).

- ``search_ranking_params``: Optuna over the iALS/BPR + confidence-recipe knobs,
  single "balanced" objective (NDCG@10 with a soft coverage floor).
- ``tune_serving``: post-hoc grid over the knobs that need no refit (content-blend
  strength, popularity penalty, MMR diversity) on one fitted A-model.

Everything here scores on B with models fit on A; C is never touched.
"""
from __future__ import annotations

import csv
import gc
import itertools
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .data_loading import CatalogLookups
from .evaluation import (
    EvalResult,
    EvalTargets,
    balanced_objective,
    build_eval_targets,
    build_train_csr,
    evaluate_scorer,
    factor_scorer,
    popularity_percentile,
)
from .pipeline import BaseModels, RankingParams, fit_base_models
from .scoring import LEGACY_SERVING, ItemArrays, ServingParams

logger = logging.getLogger(__name__)

# Starting points enqueued ahead of the TPE search so it can never end up worse
# than the best configurations known before v5.1.
KNOWN_GOOD_CONFIGS = [
    # Aug 3 2026 model (best stored NDCG before the protocol fix): k=128.
    {"factors": 128, "regularization": 0.0253, "iterations": 26, "alpha": 0.608, "conf_alpha": 40.0},
]

RECENCY_CHOICES = {"off": None, "1y": 365.0, "3y": 1095.0, "6y": 2190.0}


@dataclass
class TuningContext:
    """A-fit / B-eval fixtures shared by every trial (vocabulary is fixed by the
    positive threshold, so it doesn't change between trials)."""
    fit_df: pd.DataFrame
    catalog: CatalogLookups
    watchlist_df: Optional[pd.DataFrame]
    user_to_idx: dict
    item_to_idx: dict
    targets: EvalTargets
    seen: object
    items: ItemArrays
    pop_pct: np.ndarray

    @classmethod
    def from_models(cls, models: BaseModels, fit_df: pd.DataFrame, eval_df: pd.DataFrame,
                    catalog: CatalogLookups, watchlist_df, *, max_users: int) -> "TuningContext":
        r = models.ranking
        return cls(
            fit_df=fit_df, catalog=catalog, watchlist_df=watchlist_df,
            user_to_idx=r.user_to_idx, item_to_idx=r.item_to_idx,
            targets=build_eval_targets(eval_df, r.user_to_idx, r.item_to_idx,
                                       positive_threshold=models.params.positive_threshold,
                                       max_users=max_users),
            seen=build_train_csr(fit_df, r.user_to_idx, r.item_to_idx),
            items=ItemArrays.build(models.idx_to_item, catalog),
            pop_pct=popularity_percentile(models.item_counts),
        )

    def evaluate(self, models: BaseModels, serving: Optional[ServingParams] = None,
                 make_scorer: Optional[Callable] = None) -> EvalResult:
        """Score ``models`` on B; ``make_scorer(models)`` overrides the plain iALS scorer."""
        r = models.ranking
        if len(r.item_to_idx) != len(self.item_to_idx) or len(r.user_to_idx) != len(self.user_to_idx):
            raise ValueError("Model vocabulary differs from the tuning context's")
        scorer = make_scorer(models) if make_scorer else factor_scorer(r.user_factors, r.item_factors)
        return evaluate_scorer(scorer, self.targets, self.seen,
                               items=self.items, serving=serving, item_pop_pct=self.pop_pct)


def _suggest_params(trial, base: RankingParams, model_type_choice: str) -> RankingParams:
    model_type = (trial.suggest_categorical("model_type", ["ials", "bpr"])
                  if model_type_choice == "auto" else model_type_choice)
    p = replace(
        base,
        model_type=model_type,
        factors=trial.suggest_int("factors", 64, 320, step=32),
        regularization=trial.suggest_float("regularization", 1e-3, 10.0, log=True),
        iterations=trial.suggest_int("iterations", 10, 30),
        conf_alpha=trial.suggest_float("conf_alpha", 5.0, 80.0, log=True),
        recency_half_life_days=RECENCY_CHOICES[trial.suggest_categorical("recency", list(RECENCY_CHOICES))],
    )
    # BPR's "alpha" slot is an SGD learning rate (~1e-3-1e-1) while iALS's is an outer
    # confidence multiplier — separate parameter names so each gets its own range.
    if model_type == "bpr":
        p.alpha = trial.suggest_float("bpr_learning_rate", 1e-3, 1e-1, log=True)
    else:
        p.alpha = trial.suggest_float("alpha", 0.3, 2.0, log=True)
    return p


def search_ranking_params(
    ctx: TuningContext,
    base: RankingParams,
    *,
    n_trials: int,
    coverage_floor: Optional[float],
    model_type_choice: str = "ials",
    use_gpu: bool = False,
    log_path: Optional[Path] = None,
    report: Callable[[str], None] = logger.info,
) -> tuple[RankingParams, EvalResult]:
    """Optuna (TPE, single objective) over ranking hyperparameters; returns the best
    params and their A->B result. Trial 0 is ``base``, then ``KNOWN_GOOD_CONFIGS``."""
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    rows: list[dict] = []
    best: dict = {"score": -np.inf, "params": base, "result": None}

    def objective(trial) -> float:
        params = _suggest_params(trial, base, model_type_choice)
        models = fit_base_models(ctx.fit_df, ctx.catalog, params, watchlist_df=ctx.watchlist_df,
                                 use_gpu=use_gpu)
        res = ctx.evaluate(models)
        score = balanced_objective(res, coverage_floor)
        del models
        gc.collect()
        rows.append({"trial": trial.number, **params.to_dict(), "ndcg_at_k": res.ndcg_at_k,
                     "recall_at_k": res.recall_at_k, "coverage_at_k": res.coverage_at_k,
                     "novelty": res.novelty, "objective": score})
        report(f"  trial {trial.number:2d}: {params.model_type} k={params.factors} reg={params.regularization:.4g} "
               f"it={params.iterations} a={params.alpha:.3g} conf={params.conf_alpha:.3g} "
               f"rec={params.recency_half_life_days} -> NDCG@10={res.ndcg_at_k:.4f} "
               f"Cov@10={res.coverage_at_k:.4f} obj={score:.4f}")
        if score > best["score"]:
            best.update(score=score, params=params, result=res)
        return score

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    inv_recency = {v: k for k, v in RECENCY_CHOICES.items()}
    for cfg in [base.to_dict(), *KNOWN_GOOD_CONFIGS]:
        seed = {k: cfg[k] for k in ("factors", "regularization", "iterations", "conf_alpha") if k in cfg}
        seed["factors"] = int(min(max(32 * round(seed.get("factors", 64) / 32), 64), 320))
        seed["recency"] = inv_recency.get(cfg.get("recency_half_life_days"), "off")
        if model_type_choice == "auto":
            seed["model_type"] = cfg.get("model_type", "ials")
        if (cfg.get("model_type", "ials") == "bpr"):
            seed["bpr_learning_rate"] = cfg.get("alpha", 0.01)
        else:
            seed["alpha"] = float(min(max(cfg.get("alpha", 1.0), 0.3), 2.0))
        study.enqueue_trial(seed, skip_if_exists=True)
    study.optimize(objective, n_trials=n_trials)

    if log_path is not None and rows:
        with open(log_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        report(f"Wrote trial log {log_path}")
    return best["params"], best["result"]


def tune_serving(
    models: BaseModels,
    ctx: TuningContext,
    *,
    coverage_floor: Optional[float],
    blend_ks=(5.0, 20.0, 50.0, None),
    pop_lambdas=(0.0, 0.1, 0.25, 0.5),
    mmr_alphas=(1.0, 0.85, 0.7),
    make_scorer: Optional[Callable] = None,
    report: Callable[[str], None] = logger.info,
) -> tuple[Optional[float], ServingParams, list[dict]]:
    """Grid over (content-blend k, popularity λ, MMR α) on one A-model; returns the
    best (blend_k, ServingParams) under the balanced objective. MMR is by far the most
    expensive evaluation, so it is only swept for the best (k, λ) pair."""
    table: list[dict] = []
    best = (-np.inf, models.params.blend_k, ServingParams())
    original_k = models.params.blend_k

    for blend_k, lam in itertools.product(blend_ks, pop_lambdas):
        models.set_blend(blend_k)
        serving = ServingParams(pop_lambda=lam, pop_normalize=True, mmr_alpha=1.0)
        res = ctx.evaluate(models, serving, make_scorer)
        score = balanced_objective(res, coverage_floor)
        table.append({"blend_k": blend_k, **serving.to_dict(), "ndcg_at_k": res.ndcg_at_k,
                      "coverage_at_k": res.coverage_at_k, "objective": score})
        report(f"  serving k={blend_k} pop_lambda={lam}: NDCG@10={res.ndcg_at_k:.4f} Cov@10={res.coverage_at_k:.4f} obj={score:.4f}")
        if score > best[0]:
            best = (score, blend_k, serving)

    _, best_k, best_serving = best
    models.set_blend(best_k)
    for alpha in mmr_alphas:
        if alpha >= 1.0:
            continue
        serving = replace(best_serving, mmr_alpha=alpha)
        res = ctx.evaluate(models, serving, make_scorer)
        score = balanced_objective(res, coverage_floor)
        table.append({"blend_k": best_k, **serving.to_dict(), "ndcg_at_k": res.ndcg_at_k,
                      "coverage_at_k": res.coverage_at_k, "objective": score})
        report(f"  serving k={best_k} pop_lambda={serving.pop_lambda} mmr={alpha}: NDCG@10={res.ndcg_at_k:.4f} "
               f"Cov@10={res.coverage_at_k:.4f} obj={score:.4f}")
        if score > best[0]:
            best = (score, best_k, serving)

    models.set_blend(original_k)
    return best[1], best[2], table


def legacy_reference(models: BaseModels, ctx: TuningContext) -> tuple[EvalResult, EvalResult]:
    """(raw, legacy-served) results of a reference model — their coverage values are
    the floors for the ranking search and the serving grid respectively."""
    return ctx.evaluate(models), ctx.evaluate(models, LEGACY_SERVING)


def tune_ease(
    models: BaseModels,
    ctx: TuningContext,
    X_pos,
    *,
    coverage_floor: Optional[float],
    lambdas=(100.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0),
    topk: Optional[int] = 200,
    n_vocab: int = 20_000,
    report: Callable[[str], None] = logger.info,
) -> tuple[float, list[dict]]:
    """λ search for EASE on A -> B (one Gram matrix, one inversion per λ). Extends the
    grid by one step when the best λ sits on an edge. Leaves the best model attached
    as ``models.extras['ease']``."""
    import psutil

    from .ease import choose_vocab_size, gram_matrix, select_ease_vocab
    from .evaluation import ease_scorer
    from .pipeline import fit_ease_for

    vocab = select_ease_vocab(models.item_counts, choose_vocab_size(n_vocab, psutil.virtual_memory().available))
    G = gram_matrix(X_pos.tocsc()[:, vocab].tocsr())
    table: list[dict] = []
    results: dict[float, tuple[float, object]] = {}

    def run(lam: float) -> float:
        ease = fit_ease_for(models, X_pos, lam=lam, topk=topk, G=G, vocab=vocab)
        res = evaluate_scorer(ease_scorer(ease, X_pos), ctx.targets, ctx.seen, item_pop_pct=ctx.pop_pct)
        score = balanced_objective(res, coverage_floor)
        table.append({"lambda": lam, "topk": topk, "ndcg_at_k": res.ndcg_at_k,
                      "coverage_at_k": res.coverage_at_k, "objective": score})
        report(f"  EASE lambda={lam:g}: NDCG@10={res.ndcg_at_k:.4f} Cov@10={res.coverage_at_k:.4f} obj={score:.4f}")
        results[lam] = (score, ease)
        return score

    grid = sorted(lambdas)
    for lam in grid:
        run(lam)
    for _ in range(2):
        best_lam = max(results, key=lambda l: results[l][0])
        if best_lam == min(results):
            run(best_lam / 2)
        elif best_lam == max(results):
            run(best_lam * 2)
        else:
            break
    best_lam = max(results, key=lambda l: results[l][0])
    models.extras["ease"] = results[best_lam][1]
    return best_lam, table


def tune_blend(
    models: BaseModels,
    ctx: TuningContext,
    X_pos,
    *,
    coverage_floor: Optional[float],
    betas=(0.0, 0.25, 0.5, 1.0, 2.0, 4.0),
    report: Callable[[str], None] = logger.info,
) -> tuple[float, list[dict]]:
    """β for score = z(iALS) + β z(EASE) on A -> B (raw ranking, no serving transform)."""
    from .evaluation import blend_scorer

    ease = models.extras["ease"]
    ials = factor_scorer(models.ranking.user_factors, models.ranking.item_factors)
    table: list[dict] = []
    best = (-np.inf, 0.0)
    for beta in betas:
        res = evaluate_scorer(blend_scorer(ials, ease, X_pos, beta), ctx.targets, ctx.seen,
                              item_pop_pct=ctx.pop_pct)
        score = balanced_objective(res, coverage_floor)
        table.append({"beta": beta, "ndcg_at_k": res.ndcg_at_k, "coverage_at_k": res.coverage_at_k,
                      "objective": score})
        report(f"  blend beta={beta:g}: NDCG@10={res.ndcg_at_k:.4f} Cov@10={res.coverage_at_k:.4f} obj={score:.4f}")
        if score > best[0]:
            best = (score, beta)
    return best[1], table
