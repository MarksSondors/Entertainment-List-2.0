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
from movies.services.recommender.cold_start import fit_cold_start_head
from movies.services.recommender.data_loading import load_dataset
from movies.services.recommender.evaluation import (
    EvalResult,
    evaluate_full,
    stratified_temporal_split,
)
from movies.services.recommender.mf_ranking import (
    build_confidence_matrix,
    train_ials,
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
                            help="Run Optuna search over iALS hyperparameters using NDCG@10.")
        parser.add_argument("--trials", type=int, default=15)
        parser.add_argument("--gpu", action="store_true",
                            help="Use CUDA for the iALS fit (requires implicit + cupy).")
        parser.add_argument("--positive-threshold", type=float, default=3.5,
                            help="Rating >= threshold counts as a positive interaction (5-scale).")
        parser.add_argument("--keep-versions", type=int, default=5)
        parser.add_argument("--no-cold-start", action="store_true")

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stdout)
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
        df, catalog = load_dataset()
        if df is None or df.empty:
            self.stderr.write(self.style.ERROR("Failed to load dataset (no rows)"))
            return
        _log_mem(self.stdout, "after load")

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

        # 5. Optuna search over iALS hyperparameters using NDCG@10
        best_params = {"factors": 64, "regularization": 0.05, "iterations": 20, "alpha": 1.0}
        threshold = float(opts["positive_threshold"])

        if opts["optimize"]:
            try:
                import optuna
                optuna.logging.set_verbosity(optuna.logging.WARNING)

                R_search, u2i, i2i = build_confidence_matrix(
                    train_df, positive_threshold=threshold, alpha=40.0,
                )

                def objective(trial: "optuna.Trial") -> float:
                    factors = trial.suggest_int("factors", 32, 192, step=16)
                    reg = trial.suggest_float("regularization", 1e-3, 1e-1, log=True)
                    iters = trial.suggest_int("iterations", 10, 30)
                    alpha_outer = trial.suggest_float("alpha", 0.5, 2.0)
                    rank = train_ials(
                        R_search, u2i, i2i,
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
                    )
                    self.stdout.write(
                        f"  trial: factors={factors} reg={reg:.4g} iters={iters} alpha={alpha_outer:.2f} "
                        f"-> NDCG@10={res.ndcg_at_k:.4f} Recall@10={res.recall_at_k:.4f} RMSE={res.rmse:.4f}"
                    )
                    # Maximize NDCG -> Optuna minimizes, so flip sign.
                    return -res.ndcg_at_k

                study = optuna.create_study(direction="minimize")
                study.optimize(objective, n_trials=int(opts["trials"]))
                best_params = study.best_params
                self.stdout.write(self.style.SUCCESS(f"Best params: {best_params}"))
                del R_search, u2i, i2i
                gc.collect()
            except ImportError:
                self.stdout.write(self.style.WARNING("Optuna not installed; using defaults"))

        # 6. Final iALS fit on FULL data (train + val) so the shipped model uses every rating
        self.stdout.write("Fitting final iALS on full data...")
        R, user_to_idx, item_to_idx = build_confidence_matrix(
            df, positive_threshold=threshold, alpha=40.0,
        )
        ranking = train_ials(
            R, user_to_idx, item_to_idx,
            factors=int(best_params.get("factors", 64)),
            regularization=float(best_params.get("regularization", 0.05)),
            iterations=int(best_params.get("iterations", 20)),
            alpha=float(best_params.get("alpha", 1.0)),
            use_gpu=gpu,
            positive_threshold=threshold,
        )
        _log_mem(self.stdout, "after iALS")
        del R
        gc.collect()

        # 7. Cold-start head (CPU)
        cold = None
        if not opts["no_cold_start"]:
            self.stdout.write("Fitting cold-start ridge head...")
            cold = fit_cold_start_head(
                ranking.item_factors, ranking.item_to_idx, catalog,
                ridge_lambda=5.0,
            )
            _log_mem(self.stdout, "after cold-start")

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

        # 9. Build + save bundle
        metadata = {
            "trained_at": now_iso(),
            "model_version": MODEL_VERSION,
            "model_type": "ials",
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
            "eval": eval_result.to_dict(),
        }

        bundle = build_bundle(
            biases=biases, catalog=catalog, ranking=ranking,
            cold_start=cold, metadata=metadata,
        )
        path = save_bundle(bundle, keep_versions=int(opts["keep_versions"]))
        self.stdout.write(self.style.SUCCESS(f"Saved {path}"))
        _log_mem(self.stdout, "done")
