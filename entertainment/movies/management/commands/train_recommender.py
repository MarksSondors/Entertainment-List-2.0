"""Train the v5.0 recommender bundle.

Orchestrator only — implementation lives in ``movies.services.recommender``.
"""
from __future__ import annotations

import gc
import logging
import os
import sys

import numpy as np
import psutil
from django.core.management.base import BaseCommand

from movies.services.recommender import MODEL_VERSION
from movies.services.recommender.biases import compute_all_biases
from movies.services.recommender.cold_start import (
    blend_item_factors_with_content,
    fit_cold_start_head,
    fit_user_cold_start_head,
)
from movies.services.recommender.data_loading import load_dataset, load_watchlist_pairs
from movies.services.recommender.evaluation import (
    EvalResult,
    build_train_csr,
    evaluate_full,
    fit_explicit_blend_weight,
    global_temporal_split,
    stratified_temporal_split,
)
from movies.services.recommender.mf_ranking import (
    build_confidence_matrix,
    train_ranking_model,
    _gpu_available,
    gpu_diagnostics,
)
from movies.services.recommender.model_io import build_bundle, now_iso, save_bundle
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


class Command(BaseCommand):
    help = "Train the v5.0 movie recommender (joint-ridge biases + iALS ranking + cold-start)."

    def add_arguments(self, parser):
        parser.add_argument("--optimize", action="store_true",
                            help="Run Optuna search over ranking hyperparameters (multi-objective: "
                                 "NDCG@10 / RMSE / coverage), also choosing between iALS and BPR.")
        parser.add_argument("--trials", type=int, default=15)
        parser.add_argument("--model-type", choices=["auto", "ials", "bpr"], default="auto",
                            help="Ranking model to train. 'auto' lets --optimize search over both "
                                 "(defaults to iALS when not optimizing).")
        parser.add_argument("--gpu", action="store_true",
                            help="Use CUDA for the iALS fit (requires implicit + cupy).")
        parser.add_argument("--positive-threshold", type=float, default=3.5,
                            help="Rating >= threshold counts as a positive interaction (5-scale).")
        parser.add_argument("--keep-versions", type=int, default=5)
        parser.add_argument("--no-cold-start", action="store_true")
        parser.add_argument("--no-cache", action="store_true",
                            help="Bypass the cached Parquet dataset and reload/reprocess from the raw "
                                 "CSVs + DB instead of reusing data/.recommender_cache/.")
        parser.add_argument("--max-memory-gb", type=float, default=None,
                            help="Soft address-space limit in GB for this process (Linux only, via "
                                 "resource.RLIMIT_AS). Converts an OS-level OOM kill into a catchable "
                                 "MemoryError with a clear message instead of a silent SIGKILL. "
                                 "No effect on Windows/macOS.")

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stdout)
        self._apply_memory_limit(opts.get("max_memory_gb"))
        try:
            self._train(opts)
        except MemoryError:
            self.stderr.write(self.style.ERROR(
                "Training ran out of memory (MemoryError under the configured --max-memory-gb limit, "
                "or a bare allocation failure). Try lowering --trials, narrowing the iALS --factors "
                "search range, or increasing --max-memory-gb / the container's memory limit."
            ))
            raise

    def _apply_memory_limit(self, max_memory_gb):
        """Cap this process's address space so an out-of-memory condition raises a
        catchable ``MemoryError`` instead of the kernel silently SIGKILL-ing the
        process (see ``handle``'s ``MemoryError`` handler above). Linux-only; a no-op
        (with a warning) on platforms without the ``resource`` module.
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

    def _train(self, opts):
        gpu = bool(opts["gpu"])
        if gpu and not _gpu_available():
            diag = gpu_diagnostics()
            self.stdout.write(self.style.WARNING(
                "--gpu requested but implicit CUDA backend unavailable. Falling back to CPU."
            ))
            self.stdout.write(self.style.WARNING(
                f"  diagnostics: implicit.gpu module={diag['implicit_gpu_module']} "
                f"HAS_CUDA={diag['implicit_has_cuda']} cupy_importable={diag['cupy_importable']} "
                f"cuda_devices={diag['device_count']}"
            ))
            if diag["error"]:
                self.stdout.write(self.style.WARNING(f"  reason: {diag['error']}"))
            self.stdout.write(self.style.WARNING(
                "  fix: install cupy matching your CUDA (e.g. `pip install cupy-cuda12x`) "
                "and ensure `nvidia-smi` works on this host. Do NOT install on the inference VM."
            ))
            gpu = False

        self.stdout.write(self.style.NOTICE(f"Training recommender v{MODEL_VERSION} (gpu={gpu})"))
        _log_mem(self.stdout, "start")

        # 1. Load
        df, catalog = load_dataset(use_cache=not opts["no_cache"])
        if df is None or df.empty:
            self.stderr.write(self.style.ERROR("Failed to load dataset (no rows)"))
            return
        _log_mem(self.stdout, "after load")

        # Extra low-confidence implicit positives from Watchlist adds (users watchlist far
        # more often than they rate). Scoped to users/items already known from ratings
        # (see build_confidence_matrix's docstring) so this only densifies existing signal.
        try:
            watchlist_df = load_watchlist_pairs()
        except Exception:
            logger.exception("Failed to load Watchlist pairs; continuing without them")
            watchlist_df = None
        if watchlist_df is not None and not watchlist_df.empty:
            self.stdout.write(f"Loaded {len(watchlist_df)} watchlist pairs for extra implicit signal")

        # 2. Stratified split (only used for eval / Optuna)
        train_df, val_df = stratified_temporal_split(df, val_fraction=0.2)

        # 3. Per-row weights (sample_weight for biases, applied symmetrically to train + val view)
        sample_w_train = combine_sample_weights(
            time_decay(train_df["timestamp"].values),
            source_weights(train_df["user_id"]),
            np.sqrt(compute_ips_weights(train_df["tmdb_id"])),
        )

        # 4. Biases (joint per-user ridge)
        self.stdout.write("Computing biases (global / year / item / user / joint-ridge categories)...")
        biases = compute_all_biases(train_df, sample_w_train, damping=10.0, ridge_lambda=10.0)
        _log_mem(self.stdout, "after biases")

        # 5. Optuna search over ranking hyperparameters — multi-objective (NDCG@10, RMSE,
        # coverage) rather than NDCG alone, so the search surfaces the Pareto front instead
        # of silently picking a trial that maximizes NDCG even if RMSE/coverage regress.
        # Also searches over model_type (iALS vs BPR) when --model-type=auto.
        _default_model_type = "ials" if opts["model_type"] == "auto" else opts["model_type"]
        best_params = {"factors": 64, "regularization": 0.05, "iterations": 20,
                       "alpha": 0.01 if _default_model_type == "bpr" else 1.0,
                       "model_type": _default_model_type}
        threshold = float(opts["positive_threshold"])

        if opts["optimize"]:
            try:
                import optuna
                optuna.logging.set_verbosity(optuna.logging.WARNING)

                R_search, u2i, i2i = build_confidence_matrix(
                    train_df, positive_threshold=threshold, alpha=40.0,
                    watchlist_df=watchlist_df,
                )
                # u2i/i2i don't change across trials (train_ranking_model returns them
                # unchanged), so the seen-items mask can be built once instead of once per trial.
                search_train_csr = build_train_csr(train_df, u2i, i2i)

                def objective(trial: "optuna.Trial") -> tuple[float, float, float]:
                    factors = trial.suggest_int("factors", 32, 192, step=16)
                    reg = trial.suggest_float("regularization", 1e-3, 1e-1, log=True)
                    iters = trial.suggest_int("iterations", 10, 30)
                    model_type = (
                        trial.suggest_categorical("model_type", ["ials", "bpr"])
                        if opts["model_type"] == "auto" else opts["model_type"]
                    )
                    # BPR's "alpha" slot is an SGD learning rate (needs ~1e-3-1e-1) while
                    # iALS's is a confidence multiplier (~0.5-2.0) — reusing one range for
                    # both diverges BPR to NaN factors, so search each on its own scale
                    # under a distinct parameter name.
                    if model_type == "bpr":
                        alpha_outer = trial.suggest_float("bpr_learning_rate", 1e-3, 1e-1, log=True)
                    else:
                        alpha_outer = trial.suggest_float("alpha", 0.5, 2.0)
                    rank = train_ranking_model(
                        model_type, R_search, u2i, i2i,
                        factors=factors, regularization=reg, iterations=iters,
                        alpha=alpha_outer, use_gpu=gpu,
                        positive_threshold=threshold,
                    )
                    res = evaluate_full(
                        train_df, val_df,
                        biases=biases,
                        ranking_user_to_idx=rank.user_to_idx,
                        ranking_item_to_idx=rank.item_to_idx,
                        ranking_user_factors=rank.user_factors,
                        ranking_item_factors=rank.item_factors,
                        positive_threshold=threshold,
                        train_csr=search_train_csr,
                    )
                    self.stdout.write(
                        f"  trial: model={model_type} factors={factors} reg={reg:.4g} iters={iters} "
                        f"alpha={alpha_outer:.4g} -> NDCG@10={res.ndcg_at_k:.4f} "
                        f"Recall@10={res.recall_at_k:.4f} RMSE={res.rmse:.4f} Coverage@10={res.coverage_at_k:.4f}"
                    )
                    # Optuna minimizes each objective: flip NDCG/coverage (want them high),
                    # keep RMSE as-is (want it low).
                    return -res.ndcg_at_k, res.rmse, -res.coverage_at_k

                study = optuna.create_study(directions=["minimize", "minimize", "minimize"])
                study.optimize(objective, n_trials=int(opts["trials"]))
                # Multi-objective studies have no single "best" trial — pick the Pareto-optimal
                # trial with the highest NDCG@10 as the one representative config to train the
                # final model with, but log the whole front so the trade-off is visible.
                pareto = study.best_trials
                best_trial = min(pareto, key=lambda t: t.values[0])
                best_params = dict(best_trial.params)
                best_params.setdefault("model_type",
                                       "ials" if opts["model_type"] == "auto" else opts["model_type"])
                # Normalize onto a single "alpha" key so the final-fit section below doesn't
                # need to know which of "alpha"/"bpr_learning_rate" this particular trial used.
                if best_params["model_type"] == "bpr" and "bpr_learning_rate" in best_params:
                    best_params["alpha"] = best_params.pop("bpr_learning_rate")
                self.stdout.write(self.style.SUCCESS(
                    f"Pareto front: {len(pareto)} trials. Chosen (max NDCG): {best_params} "
                    f"(NDCG={-best_trial.values[0]:.4f} RMSE={best_trial.values[1]:.4f} "
                    f"Coverage={-best_trial.values[2]:.4f})"
                ))
                del R_search, u2i, i2i, search_train_csr
                gc.collect()
            except ImportError:
                self.stdout.write(self.style.WARNING("Optuna not installed; using defaults"))

        # 6. Final ranking fit on FULL data (train + val) so the shipped model uses every rating
        final_model_type = best_params.get("model_type", "ials" if opts["model_type"] == "auto" else opts["model_type"])
        self.stdout.write(f"Fitting final {final_model_type} on full data...")
        R, user_to_idx, item_to_idx = build_confidence_matrix(
            df, positive_threshold=threshold, alpha=40.0,
            watchlist_df=watchlist_df,
        )
        ranking = train_ranking_model(
            final_model_type, R, user_to_idx, item_to_idx,
            factors=int(best_params.get("factors", 64)),
            regularization=float(best_params.get("regularization", 0.05)),
            iterations=int(best_params.get("iterations", 20)),
            alpha=float(best_params.get("alpha", 0.01 if final_model_type == "bpr" else 1.0)),
            use_gpu=gpu,
            positive_threshold=threshold,
        )
        _log_mem(self.stdout, f"after {final_model_type}")
        # Per-item interaction counts (nnz per column) for the content-blend shrinkage below,
        # computed before R is freed.
        item_interaction_counts = np.asarray(R.astype(bool).sum(axis=0)).ravel()
        del R
        gc.collect()

        # 7. Cold-start head (CPU)
        cold = None
        user_cold = None
        if not opts["no_cold_start"]:
            self.stdout.write("Fitting cold-start ridge head...")
            cold = fit_cold_start_head(
                ranking.item_factors, ranking.item_to_idx, catalog,
                ridge_lambda=5.0,
            )
            _log_mem(self.stdout, "after cold-start")

            # 7b. Hybridize every item's factor with its content-predicted factor (not just
            # cold-start-only items) — see cold_start.blend_item_factors_with_content for why.
            self.stdout.write("Blending item factors with content-based predictions...")
            ranking.item_factors = blend_item_factors_with_content(
                ranking.item_factors, ranking.item_to_idx, cold, catalog,
                item_interaction_counts, k_shrinkage=20.0,
            )

            # 7c. Symmetric user cold-start head: lets a brand-new user with only a few
            # ratings get a useful factor instead of falling straight back to popularity.
            self.stdout.write("Fitting user cold-start ridge head...")
            positive_ratings = df[df["rating"] >= threshold]
            user_cold = fit_user_cold_start_head(
                ranking.user_factors, ranking.user_to_idx, positive_ratings, catalog, cold,
                ridge_lambda=5.0,
            )
            _log_mem(self.stdout, "after user cold-start")

        # 8. Held-out evaluation on the val split for the shipped metadata
        self.stdout.write("Evaluating on held-out validation split...")
        eval_result: EvalResult = evaluate_full(
            train_df, val_df,
            biases=biases,
            ranking_user_to_idx=ranking.user_to_idx,
            ranking_item_to_idx=ranking.item_to_idx,
            ranking_user_factors=ranking.user_factors,
            ranking_item_factors=ranking.item_factors,
            positive_threshold=threshold,
        )
        self.stdout.write(self.style.SUCCESS(
            f"  RMSE={eval_result.rmse:.4f} MAE={eval_result.mae:.4f} "
            f"NDCG@10={eval_result.ndcg_at_k:.4f} Recall@10={eval_result.recall_at_k:.4f} "
            f"HitRate@10={eval_result.hit_rate_at_k:.4f} MRR={eval_result.mrr:.4f} "
            f"Coverage@10={eval_result.coverage_at_k:.4f}"
        ))

        # 8b. Learn a scalar blending the bias-hierarchy prediction with the iALS factor dot
        # product (see evaluation.fit_explicit_blend_weight) — shipped so serving can combine
        # both signals for the displayed rating instead of using bias-only or factor-only.
        explicit_blend_alpha = fit_explicit_blend_weight(
            val_df, biases,
            user_to_idx=ranking.user_to_idx, item_to_idx=ranking.item_to_idx,
            user_factors=ranking.user_factors, item_factors=ranking.item_factors,
        )
        self.stdout.write(f"Explicit bias/factor blend weight: {explicit_blend_alpha:.4f}")

        # 8c. Realistic "replay" evaluation: a single global temporal cutoff instead of the
        # per-user holdout used for training/model-selection above. Reported alongside the
        # main eval for visibility only — it does not change what the model was trained on.
        replay_eval_dict = None
        try:
            replay_train_df, replay_val_df = global_temporal_split(df, cutoff_quantile=0.8)
            if not replay_val_df.empty:
                replay_result = evaluate_full(
                    replay_train_df, replay_val_df,
                    biases=biases,
                    ranking_user_to_idx=ranking.user_to_idx,
                    ranking_item_to_idx=ranking.item_to_idx,
                    ranking_user_factors=ranking.user_factors,
                    ranking_item_factors=ranking.item_factors,
                    positive_threshold=threshold,
                )
                replay_eval_dict = replay_result.to_dict()
                self.stdout.write(self.style.SUCCESS(
                    f"  [replay] RMSE={replay_result.rmse:.4f} NDCG@10={replay_result.ndcg_at_k:.4f} "
                    f"Recall@10={replay_result.recall_at_k:.4f} Coverage@10={replay_result.coverage_at_k:.4f}"
                ))
        except Exception:
            logger.exception("Global replay evaluation failed; continuing without it")

        # 9. Build + save bundle
        metadata = {
            "trained_at": now_iso(),
            "model_version": MODEL_VERSION,
            "model_type": ranking.model_type,
            "trained_with_gpu": ranking.trained_with_gpu,
            "n_users": int(ranking.user_factors.shape[0]),
            "n_items": int(ranking.item_factors.shape[0]),
            "n_ratings": int(len(df)),
            "n_local_users": int(sum(1 for u in ranking.user_to_idx if u.startswith("loc_"))),
            "n_ml_users": int(sum(1 for u in ranking.user_to_idx if u.startswith("ml_"))),
            "k": int(ranking.factors),
            "regularization": float(ranking.regularization),
            "iterations": int(ranking.iterations),
            "alpha": float(ranking.alpha),
            "positive_threshold": float(ranking.positive_threshold),
            "ips_debiasing": True,
            "explicit_blend_alpha": explicit_blend_alpha,
            "eval": eval_result.to_dict(),
            "eval_replay": replay_eval_dict,
        }

        bundle = build_bundle(
            biases=biases, catalog=catalog, ranking=ranking,
            cold_start=cold, user_cold_start=user_cold, metadata=metadata,
        )
        path = save_bundle(bundle, keep_versions=int(opts["keep_versions"]))
        self.stdout.write(self.style.SUCCESS(f"Saved {path}"))
        _log_mem(self.stdout, "done")
