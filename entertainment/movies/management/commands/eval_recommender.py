"""Offline evaluation of an existing model pickle, no retraining.

Loads a v5.0 bundle, re-runs the stratified split on fresh data, and reports
RMSE / NDCG@10 / Recall@10 / etc. Useful for regression-checking a deployed
pickle or comparing two snapshots.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from movies.services.recommender.data_loading import load_dataset
from movies.services.recommender.evaluation import (
    evaluate_full,
    stratified_temporal_split,
)
from movies.services.recommender.model_io import load_bundle

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Evaluate a saved recommender pickle (no retraining)."

    def add_arguments(self, parser):
        parser.add_argument("--model", type=str, default=None,
                            help="Path to pickle (defaults to svd_model_latest.pkl).")
        parser.add_argument("--positive-threshold", type=float, default=3.5)

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stdout)

        bundle = load_bundle(Path(opts["model"]) if opts["model"] else None)
        if bundle is None:
            self.stderr.write(self.style.ERROR("No model pickle found."))
            return

        model_version = bundle.get("model_version", bundle.get("metadata", {}).get("model_version", "<legacy>"))
        self.stdout.write(self.style.NOTICE(f"Model version: {model_version}"))

        if "ranking" not in bundle:
            self.stderr.write(self.style.ERROR(
                "Bundle is pre-v5.0 and lacks a separate ranking section. "
                "Re-train with the v5.0 trainer to evaluate."
            ))
            return

        df, _ = load_dataset()
        if df is None or df.empty:
            self.stderr.write(self.style.ERROR("Dataset empty"))
            return

        train_df, val_df = stratified_temporal_split(df, val_fraction=0.2)

        biases = bundle.get("biases", {})
        ranking = bundle["ranking"]
        result = evaluate_full(
            train_df, val_df,
            biases=biases,
            ranking_user_to_idx=ranking["user_to_idx"],
            ranking_item_to_idx=ranking["item_to_idx"],
            ranking_user_factors=ranking["user_factors"],
            ranking_item_factors=ranking["item_factors"],
            positive_threshold=float(opts["positive_threshold"]),
        )

        self.stdout.write(self.style.SUCCESS(
            f"RMSE={result.rmse:.4f} MAE={result.mae:.4f} "
            f"NDCG@10={result.ndcg_at_k:.4f} Recall@10={result.recall_at_k:.4f} "
            f"HitRate@10={result.hit_rate_at_k:.4f} MRR={result.mrr:.4f} "
            f"Coverage@10={result.coverage_at_k:.4f} "
            f"({result.n_test_users} users, {result.n_test_ratings} val ratings)"
        ))
        for label, sub in result.per_cohort.items():
            self.stdout.write(
                f"  [{label}] RMSE={sub.rmse:.4f} NDCG@10={sub.ndcg_at_k:.4f} "
                f"Recall@10={sub.recall_at_k:.4f} ({sub.n_test_users} users)"
            )
