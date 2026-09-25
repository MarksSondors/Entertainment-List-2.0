"""Train the recommender bundle.

Orchestrator only — implementation lives in ``movies.services.recommender``.

Protocol (see ``splits.py``): every user's history is split by time into A / B / C.
  1. Tune on A -> B: Optuna over ranking hyperparameters (--optimize), the EASE λ and
     the iALS/EASE blend weight β, then a post-hoc grid over the serving knobs
     (content blend, popularity penalty, MMR) for both iALS and the blend.
  2. Report on A∪B -> C: refit with the chosen config and score every stage
     (MostPop, raw iALS, pre-v5.1 serving, tuned iALS, EASE, tuned blend) on the
     untouched C split. The blend only ships if it beats tuned iALS there.
  1b. Reranker: a LambdaRank model over iALS/EASE/popularity candidates, trained on
     A-model features with B labels; ships only if it beats the best other stage on C.
  3. Ship: refit on everything and save; the bundle is promoted to
     svd_model_latest.pkl only if it doesn't regress against the current champion.
"""
from __future__ import annotations

import gc
import json
import logging
import os
import sys
from dataclasses import replace
from datetime import datetime

import psutil
from django.core.management.base import BaseCommand

from movies.services.recommender import MODEL_VERSION
from movies.services.recommender.biases import compute_all_biases
from movies.services.recommender.cold_start import fit_user_cold_start_head
from movies.services.recommender.data_loading import load_cached_dataset, load_dataset, load_watchlist_pairs
from movies.services.recommender.evaluation import (
    EVAL_PROTOCOL,
    EvalResult,
    blend_scorer,
    build_eval_targets,
    build_train_csr,
    evaluate_pointwise,
    ease_scorer,
    evaluate_stage,
    factor_scorer,
    fit_explicit_blend_weight,
    format_stage_table,
    global_temporal_split,
    popularity_percentile,
    popularity_scorer,
)
from movies.services.recommender.mf_ranking import _gpu_available, gpu_diagnostics
from movies.services.recommender.model_io import (
    LATEST_META,
    build_bundle,
    model_dir,
    now_iso,
    save_bundle,
)
from movies.services.recommender.pipeline import RankingParams, fit_base_models, fit_ease_for, positives_csr
from movies.services.recommender.reranker import (
    RerankFeatureBuilder,
    _labels_by_user,
    booster_to_numpy_trees,
    build_training_data,
    check_parity,
    reranker_scorer,
    reranker_to_bundle,
    sample_users,
    train_lambdarank,
)
from movies.services.recommender.scoring import LEGACY_SERVING, ItemArrays, ServingParams
from movies.services.recommender.splits import SPLIT_VERSION, three_way_temporal_split
from movies.services.recommender.stage_cache import dataset_fingerprint, load_or_fit, stage_key
from movies.services.recommender.tuning import (
    TuningContext,
    legacy_reference,
    search_ranking_params,
    tune_blend,
    tune_ease,
    tune_serving,
)
from movies.services.recommender.weights import (
    combine_sample_weights,
    compute_ips_weights,
    source_weights,
    time_decay,
)

logger = logging.getLogger(__name__)


def _log_mem(stdout, label: str) -> None:
    mb = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    stdout.write(f"[{label}] RSS = {mb:.1f} MB")


def _bias_sample_weights(df):
    return combine_sample_weights(
        time_decay(df["timestamp"].values),
        source_weights(df["user_id"]),
        compute_ips_weights(df["tmdb_id"]) ** 0.5,
    )


def _champion_params() -> RankingParams | None:
    """Ranking params of the current champion (from its metadata sidecar), if recorded."""
    p = model_dir() / LATEST_META
    if not p.exists():
        return None
    try:
        meta = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return RankingParams.from_dict(meta["ranking_params"]) if meta.get("ranking_params") else None


class Command(BaseCommand):
    help = "Train the movie recommender (joint-ridge biases + iALS ranking + cold-start), gated on held-out eval."

    def add_arguments(self, parser):
        parser.add_argument("--optimize", action="store_true",
                            help="Run the Optuna search over ranking hyperparameters (on A -> B).")
        parser.add_argument("--trials", type=int, default=30)
        parser.add_argument("--model-type", choices=["auto", "ials", "bpr"], default="ials",
                            help="Ranking model. 'auto' lets --optimize search over iALS and BPR.")
        parser.add_argument("--no-ease", action="store_true",
                            help="Skip the EASE item-item model and the iALS/EASE blend.")
        parser.add_argument("--ease-items", type=int, default=20_000,
                            help="EASE vocabulary size (most-interacted items; shrunk automatically if RAM is short).")
        parser.add_argument("--ease-topk", type=int, default=200,
                            help="Keep the top-k EASE weights per item (bundle size ~ items * k * 8 bytes).")
        parser.add_argument("--no-reranker", action="store_true",
                            help="Skip the LightGBM LambdaRank reranker (requires lightgbm, training-only).")
        parser.add_argument("--reranker-users", type=int, default=20_000,
                            help="Users whose B labels train the reranker.")
        parser.add_argument("--candidate-k", type=int, default=200,
                            help="Reranker candidates per source (iALS and EASE top-k; popularity uses k/4).")
        parser.add_argument("--no-serving-tuning", action="store_true",
                            help="Skip the post-hoc grid over blend / popularity / MMR knobs.")
        parser.add_argument("--gpu", action="store_true",
                            help="Use CUDA for the iALS fit (requires implicit + cupy).")
        parser.add_argument("--positive-threshold", type=float, default=3.5,
                            help="Rating >= threshold counts as a positive interaction (5-scale).")
        parser.add_argument("--eval-users", type=int, default=10_000,
                            help="User sample for the reported AB -> C evaluation.")
        parser.add_argument("--tune-users", type=int, default=5_000,
                            help="User sample for A -> B tuning evaluations (smaller = faster trials).")
        parser.add_argument("--keep-versions", type=int, default=5)
        parser.add_argument("--no-cold-start", action="store_true")
        parser.add_argument("--replay-eval", action="store_true",
                            help="Also report a global-cutoff replay eval (one extra fit).")
        parser.add_argument("--output-dir", type=str, default=None,
                            help="Save (and gate/promote) the bundle in this directory instead of "
                                 "movies/ml_models/ — for experiments that must not touch the served model.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Run tuning + held-out evaluation but skip the final fit and saving.")
        parser.add_argument("--force-promote", action="store_true",
                            help="Promote to svd_model_latest.pkl even if the held-out eval regressed.")
        parser.add_argument("--no-cache", action="store_true",
                            help="Bypass the cached Parquet dataset and reload/reprocess from the raw "
                                 "CSVs + DB instead of reusing data/.recommender_cache/.")
        parser.add_argument("--dataset-cache", type=str, default=None,
                            help="Train from a cached dataset key (or 'latest') without DB access "
                                 "(no fresh local reviews or watchlist). For offline experiments.")
        parser.add_argument("--no-stage-cache", action="store_true",
                            help="Don't reuse/store fitted A / AB models under data/.recommender_cache/stages/.")
        parser.add_argument("--max-memory-gb", type=float, default=None,
                            help="Soft address-space limit in GB for this process (Linux only, via "
                                 "resource.RLIMIT_AS). Converts an OS-level OOM kill into a catchable "
                                 "MemoryError with a clear message instead of a silent SIGKILL. "
                                 "No effect on Windows/macOS.")

    def handle(self, *args, **opts):
        if hasattr(sys.stdout, "reconfigure"):
            # Windows consoles default to cp1252; never crash a long run on a stray character.
            sys.stdout.reconfigure(errors="replace")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stdout)
        self._apply_memory_limit(opts.get("max_memory_gb"))
        try:
            self._train(opts)
        except MemoryError:
            self.stderr.write(self.style.ERROR(
                "Training ran out of memory (MemoryError under the configured --max-memory-gb limit, "
                "or a bare allocation failure). Try lowering --trials / --tune-users, narrowing the "
                "--factors search range, or increasing --max-memory-gb / the container's memory limit."
            ))
            raise

    def _apply_memory_limit(self, max_memory_gb):
        """Cap this process's address space so an out-of-memory condition raises a
        catchable ``MemoryError`` instead of the kernel silently SIGKILL-ing the
        process. Linux-only; a no-op (with a warning) elsewhere.
        """
        if not max_memory_gb:
            return
        try:
            import resource
        except ImportError:
            self.stdout.write(self.style.WARNING(
                "--max-memory-gb has no effect on this platform (resource module unavailable, e.g. Windows)"
            ))
            return
        try:
            limit_bytes = int(max_memory_gb * 1024 ** 3)
            _, hard = resource.getrlimit(resource.RLIMIT_AS)
            new_hard = hard if hard != resource.RLIM_INFINITY and hard < limit_bytes else limit_bytes
            resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, new_hard))
            self.stdout.write(self.style.NOTICE(f"Soft memory limit set to {max_memory_gb:.1f} GB (RLIMIT_AS)"))
        except (ValueError, OSError) as e:
            self.stdout.write(self.style.WARNING(f"Failed to set memory limit: {e}"))

    def _resolve_gpu(self, requested: bool) -> bool:
        if not requested or _gpu_available():
            return requested
        diag = gpu_diagnostics()
        self.stdout.write(self.style.WARNING(
            "--gpu requested but implicit CUDA backend unavailable. Falling back to CPU.\n"
            f"  diagnostics: implicit.gpu module={diag['implicit_gpu_module']} "
            f"HAS_CUDA={diag['implicit_has_cuda']} cupy_importable={diag['cupy_importable']} "
            f"cuda_devices={diag['device_count']}"
            + (f"\n  reason: {diag['error']}" if diag["error"] else "")
            + "\n  fix: install cupy matching your CUDA (e.g. `pip install cupy-cuda12x`) and ensure "
              "`nvidia-smi` works on this host. Do NOT install on the inference VM."
        ))
        return False

    # ------------------------------------------------------------------

    def _train(self, opts):
        gpu = self._resolve_gpu(bool(opts["gpu"]))
        use_stage_cache = not opts["no_stage_cache"]
        threshold = float(opts["positive_threshold"])
        self.stdout.write(self.style.NOTICE(f"Training recommender v{MODEL_VERSION} (gpu={gpu})"))
        _log_mem(self.stdout, "start")

        # 1. Load
        if opts["dataset_cache"]:
            df, catalog = load_cached_dataset(opts["dataset_cache"])
            watchlist_df = None
            self.stdout.write(self.style.WARNING(
                f"Training from cached dataset '{opts['dataset_cache']}' (no DB: local reviews may be "
                "stale and Watchlist signal is skipped)."
            ))
        else:
            df, catalog = load_dataset(use_cache=not opts["no_cache"])
            watchlist_df = self._load_watchlist()
        if df is None or df.empty:
            self.stderr.write(self.style.ERROR("Failed to load dataset (no rows)"))
            return
        _log_mem(self.stdout, "after load")

        fingerprint = dataset_fingerprint(df)
        splits = three_way_temporal_split(df)
        full_df = splits.df  # the split keeps a time-sorted copy; drop the original to save RAM
        del df
        gc.collect()
        base_params = _champion_params() or RankingParams()
        base_params.positive_threshold = threshold
        if opts["model_type"] != "auto":
            base_params.model_type = opts["model_type"]

        def cached_fit(stage: str, fit_df, params: RankingParams):
            key = stage_key(fingerprint, SPLIT_VERSION, stage, params.fit_key(), watchlist_df is not None,
                            not opts["no_cold_start"])
            models = load_or_fit(
                stage, key,
                lambda: fit_base_models(fit_df, catalog, params, watchlist_df=watchlist_df, use_gpu=gpu,
                                        cold_start=not opts["no_cold_start"]),
                enabled=use_stage_cache,
            )
            models.set_blend(params.blend_k)
            return models

        # 2. Tune on A -> B
        A, B = splits.a(), splits.b()
        params = base_params
        serving = LEGACY_SERVING
        tuning_info: dict = {}
        self.stdout.write(f"Reference fit on A with {base_params.to_dict()}")
        models_A = cached_fit("fit_A", A, base_params)
        ctx = TuningContext.from_models(models_A, A, B, catalog, watchlist_df, max_users=int(opts["tune_users"]))
        ref_raw, ref_legacy = legacy_reference(models_A, ctx)
        self.stdout.write(f"  reference A->B: raw NDCG@10={ref_raw.ndcg_at_k:.4f} Cov@10={ref_raw.coverage_at_k:.4f}; "
                          f"legacy-served NDCG@10={ref_legacy.ndcg_at_k:.4f} Cov@10={ref_legacy.coverage_at_k:.4f}")
        tuning_info["reference_A_to_B"] = {"raw": ref_raw.to_dict(), "legacy_served": ref_legacy.to_dict()}

        if opts["optimize"]:
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                self.stdout.write(f"Optuna: {opts['trials']} trials on A -> B "
                                  f"(coverage floor {ref_raw.coverage_at_k:.4f})")
                params, best_res = search_ranking_params(
                    ctx, base_params, n_trials=int(opts["trials"]),
                    coverage_floor=ref_raw.coverage_at_k, model_type_choice=opts["model_type"],
                    use_gpu=gpu, log_path=model_dir() / f"optuna_trials_{ts}.csv",
                    report=self.stdout.write,
                )
                self.stdout.write(self.style.SUCCESS(
                    f"Chosen ranking params: {params.to_dict()} (A->B NDCG@10={best_res.ndcg_at_k:.4f})"))
                tuning_info["best_A_to_B"] = best_res.to_dict()
                if params.fit_key() != base_params.fit_key():
                    models_A = cached_fit("fit_A", A, params)
            except ImportError:
                self.stdout.write(self.style.WARNING("Optuna not installed; using champion/default params"))

        self.stdout.write("Computing biases on A (reranker features, explicit blend weight)...")
        biases_A = compute_all_biases(A, _bias_sample_weights(A), damping=10.0, ridge_lambda=10.0)

        # EASE: λ on A -> B, then the blend weight β for z(iALS) + β z(EASE).
        use_ease = not opts["no_ease"]
        ease_lam_A, beta, X_pos_A = None, 0.0, None
        if use_ease:
            X_pos_A = positives_csr(A, models_A)
            self.stdout.write("EASE lambda search on A -> B...")
            ease_lam_A, ease_table = tune_ease(models_A, ctx, X_pos_A, coverage_floor=ref_raw.coverage_at_k,
                                               topk=int(opts["ease_topk"]), n_vocab=int(opts["ease_items"]),
                                               report=self.stdout.write)
            beta, blend_table = tune_blend(models_A, ctx, X_pos_A, coverage_floor=ref_raw.coverage_at_k,
                                           report=self.stdout.write)
            tuning_info["ease"] = {"lambda_A": ease_lam_A, "lambda_grid": ease_table,
                                   "beta": beta, "beta_grid": blend_table}
            self.stdout.write(self.style.SUCCESS(f"EASE lambda={ease_lam_A:g}, blend beta={beta:g}"))

        def make_blend(m):
            return blend_scorer(factor_scorer(m.ranking.user_factors, m.ranking.item_factors),
                                m.extras["ease"], X_pos_A, beta)

        # Serving knobs, tuned separately for each stage that could ship: {stage: (blend_k, ServingParams)}
        candidates = {"ials": (params.blend_k, LEGACY_SERVING)}
        if beta > 0:
            candidates["blend"] = (params.blend_k, replace(LEGACY_SERVING, ease_beta=beta))
        if not opts["no_serving_tuning"]:
            floor = ref_legacy.coverage_at_k
            for stage in list(candidates):
                self.stdout.write(f"Serving-knob grid for {stage} on A -> B (coverage floor {floor:.4f})")
                k, sp, grid = tune_serving(models_A, ctx, coverage_floor=floor, report=self.stdout.write,
                                           make_scorer=make_blend if stage == "blend" else None)
                if stage == "blend":
                    sp = replace(sp, ease_beta=beta)
                candidates[stage] = (k, sp)
                tuning_info[f"serving_grid_{stage}"] = grid
                self.stdout.write(self.style.SUCCESS(f"  {stage}: blend_k={k} {sp.to_dict()}"))
        params.blend_k, serving = candidates["ials"]
        models_A.set_blend(params.blend_k)

        # Reranker: A-model candidates/features, B labels (biases_A computed above).
        trees = None
        reranker_info: dict = {}
        cand_k = (int(opts["candidate_k"]), int(opts["candidate_k"]), max(int(opts["candidate_k"]) // 4, 1))
        if not opts["no_reranker"]:
            trees, reranker_info = self._train_reranker(models_A, A, B, biases_A, catalog, X_pos_A, ctx,
                                                        cand_k, int(opts["reranker_users"]),
                                                        ref_legacy.coverage_at_k,
                                                        tune=not opts["no_serving_tuning"])
            if trees is not None:
                candidates["rerank"] = (params.blend_k, reranker_info.pop("serving"))
                tuning_info["reranker"] = {k: v for k, v in reranker_info.items() if k != "model_str"}

        # Displayed-rating blend weight: biases and factors both fit on A, alpha fit on B.
        r = models_A.ranking
        explicit_blend_alpha = fit_explicit_blend_weight(
            B, biases_A, user_to_idx=r.user_to_idx, item_to_idx=r.item_to_idx,
            user_factors=r.user_factors, item_factors=r.item_factors,
        )
        self.stdout.write(f"Explicit bias/factor blend weight: {explicit_blend_alpha:.4f}")
        nnz_A = X_pos_A.nnz if X_pos_A is not None else None
        del A, B, ctx, models_A, biases_A, r, X_pos_A
        gc.collect()
        _log_mem(self.stdout, "after tuning")

        # 3. Report on A∪B -> C
        AB, C = splits.ab(), splits.c()
        self.stdout.write("Computing biases on A+B (shipped)...")
        biases = compute_all_biases(AB, _bias_sample_weights(AB), damping=10.0, ridge_lambda=10.0)
        models_AB = cached_fit("fit_AB", AB, params)
        X_pos_AB = None
        if ease_lam_A is not None:
            # EASE on A∪B whenever it was tuned: the blend and the reranker's features both use it.
            X_pos_AB = positives_csr(AB, models_AB)
            fit_ease_for(models_AB, X_pos_AB, lam=ease_lam_A * X_pos_AB.nnz / nnz_A, topk=int(opts["ease_topk"]),
                         n_vocab=int(opts["ease_items"]))
        rerank_scorer_C = None
        if trees is not None:
            builder_AB = RerankFeatureBuilder(models_AB, AB, biases, catalog, X_pos_AB,
                                              k_ials=cand_k[0], k_ease=cand_k[1], k_pop=cand_k[2])
            _, first_ts_C = _labels_by_user(C, models_AB.ranking.user_to_idx, models_AB.ranking.item_to_idx,
                                            threshold, 4.5)
            rerank_scorer_C = reranker_scorer(builder_AB, trees, first_ts_C)
        stage_results = self._evaluate_on_c(models_AB, AB, C, catalog, candidates, X_pos_AB,
                                            threshold, int(opts["eval_users"]), rerank_scorer=rerank_scorer_C)
        self.stdout.write(format_stage_table(stage_results, baseline="mostpop", reference="ials_legacy"))

        # Gate: ship the blend only if it clearly beats tuned iALS on C without losing coverage.
        gates: dict = {}
        chosen = "ials"
        if "blend_served" in stage_results:
            b, i = stage_results["blend_served"], stage_results["ials_served"]
            cov_floor = min(i.coverage_at_k, stage_results["ials_legacy"].coverage_at_k)
            ok = b.ndcg_at_k >= 1.01 * i.ndcg_at_k and b.coverage_at_k >= cov_floor
            reason = (f"blend NDCG@10 {b.ndcg_at_k:.4f} vs iALS {i.ndcg_at_k:.4f} "
                      f"({100 * (b.ndcg_at_k - i.ndcg_at_k) / max(i.ndcg_at_k, 1e-9):+.1f}%), "
                      f"coverage {b.coverage_at_k:.4f} vs floor {cov_floor:.4f}")
            gates["ease_blend"] = {"shipped": bool(ok), "reason": reason}
            self.stdout.write((self.style.SUCCESS if ok else self.style.WARNING)(
                f"EASE blend {'SHIPS' if ok else 'does not ship'}: {reason}"))
            if ok:
                chosen = "blend"
        if "rerank_served" in stage_results:
            rr, best = stage_results["rerank_served"], stage_results[f"{chosen}_served"]
            cov_floor = min(best.coverage_at_k, stage_results["ials_legacy"].coverage_at_k)
            ok = rr.ndcg_at_k >= 1.01 * best.ndcg_at_k and rr.coverage_at_k >= cov_floor
            reason = (f"reranker NDCG@10 {rr.ndcg_at_k:.4f} vs {chosen} {best.ndcg_at_k:.4f} "
                      f"({100 * (rr.ndcg_at_k - best.ndcg_at_k) / max(best.ndcg_at_k, 1e-9):+.1f}%), "
                      f"coverage {rr.coverage_at_k:.4f} vs floor {cov_floor:.4f}")
            gates["reranker"] = {"shipped": bool(ok), "reason": reason}
            self.stdout.write((self.style.SUCCESS if ok else self.style.WARNING)(
                f"Reranker {'SHIPS' if ok else 'does not ship'}: {reason}"))
            if ok:
                # keep the blend's EASE weight so serving can fall back to it if the reranker fails
                fallback_beta = candidates[chosen][1].ease_beta
                chosen = "rerank"
                candidates["rerank"] = (candidates["rerank"][0],
                                        replace(candidates["rerank"][1], ease_beta=fallback_beta))
        params.blend_k, serving = candidates[chosen]
        eval_result = stage_results[f"{chosen}_served"]
        if chosen != "rerank":
            trees = None
        models_AB.set_blend(params.blend_k)
        r = models_AB.ranking
        eval_result.rmse, eval_result.mae = evaluate_pointwise(
            C, biases, user_to_idx=r.user_to_idx, item_to_idx=r.item_to_idx,
            user_factors=r.user_factors, item_factors=r.item_factors, factor_blend_weight=explicit_blend_alpha,
        )
        self.stdout.write(self.style.SUCCESS(
            f"Held-out C, shipped stage '{chosen}': NDCG@10={eval_result.ndcg_at_k:.4f} "
            f"Recall@10={eval_result.recall_at_k:.4f} Coverage@10={eval_result.coverage_at_k:.4f} "
            f"RMSE={eval_result.rmse:.4f} MAE={eval_result.mae:.4f}"
        ))
        del AB, C, models_AB, X_pos_AB, r, rerank_scorer_C
        gc.collect()

        replay_eval = None
        if opts["replay_eval"]:
            replay_eval = self._replay_eval(full_df, catalog, params, serving, watchlist_df, gpu, threshold,
                                            int(opts["eval_users"]))
        del splits
        gc.collect()

        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("--dry-run: skipping the final fit and save."))
            self.stdout.write(json.dumps({"ranking_params": params.to_dict(), "serving": serving.to_dict(),
                                          "explicit_blend_alpha": explicit_blend_alpha, "gates": gates,
                                          "eval": eval_result.to_dict()}, indent=2, default=str))
            return

        # 4. Ship: refit on everything
        self.stdout.write(f"Fitting final {params.model_type} on full data...")
        final = fit_base_models(full_df, catalog, params, watchlist_df=watchlist_df, use_gpu=gpu,
                                cold_start=not opts["no_cold_start"])
        ranking = final.ranking
        _log_mem(self.stdout, "after final fit")

        ease_section = None
        if ease_lam_A is not None and (serving.ease_beta > 0 or trees is not None):
            X_pos_full = positives_csr(full_df, final)
            lam_full = ease_lam_A * X_pos_full.nnz / nnz_A
            self.stdout.write(f"Fitting final EASE on full data (lambda={lam_full:.0f})...")
            ease_full = fit_ease_for(final, X_pos_full, lam=lam_full, topk=int(opts["ease_topk"]),
                                     n_vocab=int(opts["ease_items"]))
            ease_section = ease_full.to_bundle(final.idx_to_item)
            del X_pos_full, ease_full
            gc.collect()
            _log_mem(self.stdout, "after final EASE")

        user_cold = None
        if final.cold is not None:
            self.stdout.write("Fitting user cold-start ridge head...")
            user_cold = fit_user_cold_start_head(
                ranking.user_factors, ranking.user_to_idx, full_df[full_df["rating"] >= threshold], catalog, final.cold,
                ridge_lambda=5.0,
            )

        metadata = {
            "trained_at": now_iso(),
            "model_version": MODEL_VERSION,
            "model_type": ranking.model_type,
            "trained_with_gpu": ranking.trained_with_gpu,
            "n_users": int(ranking.user_factors.shape[0]),
            "n_items": int(ranking.item_factors.shape[0]),
            "n_ratings": int(len(full_df)),
            "n_local_users": int(sum(1 for u in ranking.user_to_idx if u.startswith("loc_"))),
            "n_ml_users": int(sum(1 for u in ranking.user_to_idx if u.startswith("ml_"))),
            "k": int(ranking.factors),
            "regularization": float(ranking.regularization),
            "iterations": int(ranking.iterations),
            "alpha": float(ranking.alpha),
            "positive_threshold": float(ranking.positive_threshold),
            "ips_debiasing": True,
            "explicit_blend_alpha": explicit_blend_alpha,
            "ranking_params": params.to_dict(),
            "serving": serving.to_dict(),
            "eval_protocol": EVAL_PROTOCOL,
            "eval": eval_result.to_dict(),
            "eval_stages": {k: v.to_dict() for k, v in stage_results.items()},
            "eval_replay": replay_eval,
            "gates": gates,
            "tuning": tuning_info,
            "optimized": bool(opts["optimize"]),
        }
        bundle = build_bundle(
            biases=biases, catalog=catalog, ranking=ranking,
            cold_start=final.cold, user_cold_start=user_cold, metadata=metadata,
            confidence=final.recipe.to_dict(), item_counts=final.item_counts, ease=ease_section,
        )
        if trees is not None:
            bundle["reranker"] = reranker_to_bundle(
                trees, best_iteration=reranker_info.get("best_iteration", 0),
                n_train_users=reranker_info.get("n_train_users", 0), cand_k=cand_k,
                model_str=reranker_info.get("model_str"),
            )
        result = save_bundle(bundle, keep_versions=int(opts["keep_versions"]),
                             force_promote=bool(opts["force_promote"]), directory=opts["output_dir"])
        style = self.style.SUCCESS if result.promoted else self.style.WARNING
        self.stdout.write(style(f"Saved {result.path} - {'PROMOTED' if result.promoted else 'NOT promoted'}: "
                                f"{result.reason}"))
        _log_mem(self.stdout, "done")

    # ------------------------------------------------------------------

    def _evaluate_on_c(self, models, AB, C, catalog, candidates: dict, X_pos, threshold: float,
                       eval_users: int, rerank_scorer=None) -> dict[str, EvalResult]:
        """Score every stage on C with identical users / seen-masks. ``candidates`` maps a
        shippable stage ("ials" / "blend") to its tuned (blend_k, ServingParams)."""
        r = models.ranking
        seen = build_train_csr(AB, r.user_to_idx, r.item_to_idx)
        targets = build_eval_targets(C, r.user_to_idx, r.item_to_idx, positive_threshold=threshold,
                                     max_users=eval_users)
        items = ItemArrays.build(models.idx_to_item, catalog)
        pop_pct = popularity_percentile(models.item_counts)

        def ials_with(blend_k):
            models.set_blend(blend_k)
            return factor_scorer(r.user_factors, models.ranking.item_factors)

        ials_k, ials_serving = candidates["ials"]
        stages = [
            ("mostpop", lambda: popularity_scorer(models.item_counts), None),
            ("ials", lambda: ials_with(ials_k), None),
            ("ials_legacy", lambda: ials_with(20.0), LEGACY_SERVING),
            ("ials_served", lambda: ials_with(ials_k), ials_serving),
        ]
        if "blend" in candidates and "ease" in models.extras:
            blend_k, blend_serving = candidates["blend"]
            ease = models.extras["ease"]
            stages += [
                ("ease", lambda: ease_scorer(ease, X_pos), None),
                ("blend_served", lambda: blend_scorer(ials_with(blend_k), ease, X_pos, blend_serving.ease_beta),
                 blend_serving),
            ]
        if rerank_scorer is not None and "rerank" in candidates:
            stages.append(("rerank_served", lambda: rerank_scorer, candidates["rerank"][1]))
        results = {}
        for name, make, sp in stages:
            results[name] = evaluate_stage(make(), targets, seen, items=items, serving=sp, item_pop_pct=pop_pct)
            self.stdout.write(f"  C {name}: NDCG@10={results[name].ndcg_at_k:.4f}")
        self.stdout.write(f"Held-out C: {targets.n_ratings} positives, {len(targets.users)} users")
        return results

    def _train_reranker(self, models_A, A, B, biases_A, catalog, X_pos_A, ctx, cand_k, n_users: int,
                        coverage_floor: float, *, tune: bool):
        """Train the LambdaRank reranker on A -> B; returns (numpy trees, info) or (None, {})."""
        try:
            import lightgbm  # noqa: F401
        except ImportError:
            self.stdout.write(self.style.WARNING(
                "lightgbm not installed (pip install -r requirements-train.txt); skipping the reranker"))
            return None, {}
        r = models_A.ranking
        builder = RerankFeatureBuilder(models_A, A, biases_A, catalog, X_pos_A,
                                       k_ials=cand_k[0], k_ease=cand_k[1], k_pop=cand_k[2])
        labels, first_ts = _labels_by_user(B, r.user_to_idx, r.item_to_idx, models_A.params.positive_threshold, 4.5)
        tune_users = set(int(u) for u in ctx.targets.users)
        users = sample_users(labels, builder, n_users, exclude=tune_users)
        self.stdout.write(f"Reranker: building features for {len(users)} users (candidates k={cand_k})...")
        data = build_training_data(builder, B, users, labels=labels, first_ts=first_ts, report=self.stdout.write)
        self.stdout.write(f"  {data.X.shape[0]} rows / {len(data.group)} groups; candidate recall on B = "
                          f"{data.cand_recall:.3f}")
        booster, X_val = train_lambdarank(data)
        trees = booster_to_numpy_trees(booster)
        try:
            diff = check_parity(booster, trees, X_val[:50_000])
        except AssertionError as e:
            self.stdout.write(self.style.ERROR(f"Reranker not shipped: {e}"))
            return None, {}
        gain = sorted(zip(booster.feature_importance("gain"), booster.feature_name()), reverse=True)
        self.stdout.write(f"  best_iteration={booster.best_iteration} parity max|diff|={diff:.1e}; top features: "
                          + ", ".join(f"{n}" for _, n in gain[:6]))

        # Serving knobs for the reranker stage, scored on the tuning users (disjoint from its training users).
        scorer = _memoized(reranker_scorer(builder, trees, first_ts))
        serving = ServingParams(pop_lambda=0.0, pop_normalize=True, mmr_alpha=1.0)
        grid = []
        if tune:
            _, serving, grid = tune_serving(models_A, ctx, coverage_floor=coverage_floor,
                                            blend_ks=(models_A.params.blend_k,), pop_lambdas=(0.0, 0.1, 0.25),
                                            mmr_alphas=(1.0, 0.85), make_scorer=lambda m: scorer,
                                            report=self.stdout.write)
        return trees, {
            "serving": serving, "serving_grid": grid, "cand_recall_B": data.cand_recall,
            "best_iteration": int(booster.best_iteration or 0), "n_train_users": int(len(data.group)),
            "feature_gain": {n: float(g) for g, n in gain}, "model_str": booster.model_to_string(),
        }

    def _replay_eval(self, df, catalog, params, serving, watchlist_df, gpu, threshold, eval_users):
        """Global-cutoff replay: fit on everything before the 80% timestamp quantile,
        score what happened after (a separate fit, so nothing leaks)."""
        try:
            train_df, val_df = global_temporal_split(df, cutoff_quantile=0.8)
            if val_df.empty:
                return None
            models = fit_base_models(train_df, catalog, params, watchlist_df=watchlist_df, use_gpu=gpu)
            r = models.ranking
            targets = build_eval_targets(val_df, r.user_to_idx, r.item_to_idx, positive_threshold=threshold,
                                         max_users=eval_users)
            res = evaluate_stage(factor_scorer(r.user_factors, r.item_factors), targets,
                                 build_train_csr(train_df, r.user_to_idx, r.item_to_idx),
                                 items=ItemArrays.build(models.idx_to_item, catalog), serving=serving,
                                 item_pop_pct=popularity_percentile(models.item_counts))
            self.stdout.write(self.style.SUCCESS(
                f"  [replay] NDCG@10={res.ndcg_at_k:.4f} Recall@10={res.recall_at_k:.4f} "
                f"Coverage@10={res.coverage_at_k:.4f} ({res.n_test_users} users)"
            ))
            return res.to_dict()
        except Exception:
            logger.exception("Global replay evaluation failed; continuing without it")
            return None

    def _load_watchlist(self):
        # Extra low-confidence implicit positives from Watchlist adds (users watchlist far
        # more often than they rate); scoped to users/items already known from ratings.
        try:
            wl = load_watchlist_pairs()
        except Exception:
            logger.exception("Failed to load Watchlist pairs; continuing without them")
            return None
        if wl is None or wl.empty:
            return None
        self.stdout.write(f"Loaded {len(wl)} watchlist pairs for extra implicit signal")
        return wl


def _memoized(score_batch):
    """Cache a scorer's output per user batch — reranker features don't depend on the
    serving knobs being tuned, so a grid over them shouldn't recompute features."""
    cache: dict[bytes, object] = {}

    def score(users):
        key = users.tobytes()
        if key not in cache:
            cache[key] = score_batch(users)
        return cache[key].copy()
    return score
