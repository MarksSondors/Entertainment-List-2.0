"""Offline experiment harness for the recommender.

Refits the ranking model with a bundle's hyperparameters on a clean split and
prints a stage comparison table (MostPop vs iALS vs iALS with serving transforms,
...), each row with its lift over MostPop and over the pre-v5.1 serving config.

    python manage.py eval_recommender                          # AB->C with svd_model_latest.pkl's params
    python manage.py eval_recommender --model path/to/old.pkl  # compare an older snapshot's config
    python manage.py eval_recommender --dataset-cache latest   # offline: no DB, reuse the cached dataset
    python manage.py eval_recommender --protocol A-\\>B --users 3000
    python manage.py eval_recommender --show-user loc_1        # eyeball top-20 per stage
    python manage.py eval_recommender --as-is                  # old behaviour: score the shipped factors (leaked!)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
from django.core.management.base import BaseCommand

from movies.services.recommender.data_loading import load_cached_dataset, load_dataset
from movies.services.recommender.evaluation import (
    EvalResult,
    blend_scorer,
    build_eval_targets,
    build_train_csr,
    ease_scorer,
    evaluate_stage,
    factor_scorer,
    format_stage_table,
    popularity_percentile,
    popularity_scorer,
)
from movies.services.recommender.model_io import load_bundle
from movies.services.recommender.pipeline import RankingParams, fit_base_models, fit_ease_for, positives_csr
from movies.services.recommender.scoring import LEGACY_SERVING, ItemArrays, ServingParams, rank_batch
from movies.services.recommender.splits import SPLIT_VERSION, three_way_temporal_split
from movies.services.recommender.stage_cache import dataset_fingerprint, load_or_fit, stage_key

logger = logging.getLogger(__name__)

ALL_STAGES = ("mostpop", "ials", "ials_legacy", "ials_served", "ease", "blend", "blend_served")


class Command(BaseCommand):
    help = "Clean offline evaluation / stage comparison for the movie recommender."

    def add_arguments(self, parser):
        parser.add_argument("--model", type=str, default=None,
                            help="Bundle whose hyperparameters to evaluate (default: svd_model_latest.pkl).")
        parser.add_argument("--params", type=str, default=None,
                            help="JSON RankingParams overrides, e.g. '{\"factors\": 128}'.")
        parser.add_argument("--dataset-cache", type=str, default=None,
                            help="Load a cached dataset by key (or 'latest') without DB access.")
        parser.add_argument("--protocol", choices=["AB->C", "A->B"], default="AB->C")
        parser.add_argument("--users", type=int, default=10_000, help="Eval user sample size.")
        parser.add_argument("--stages", type=str, default=",".join(ALL_STAGES))
        parser.add_argument("--ease-lambda", type=float, default=None,
                            help="EASE lambda for the ease/blend stages (default: the bundle's tuned value "
                                 "scaled to this fit, else 500).")
        parser.add_argument("--ease-topk", type=int, default=200)
        parser.add_argument("--ease-items", type=int, default=20_000)
        parser.add_argument("--ease-beta", type=float, default=None,
                            help="Blend weight for the blend stages (default: the bundle's, else 1.0).")
        parser.add_argument("--as-is", action="store_true",
                            help="Also score the bundle's shipped factors (LEAKED: they saw the eval rows).")
        parser.add_argument("--show-user", type=str, default=None,
                            help="Print the top-20 per stage for this user id (e.g. loc_1).")
        parser.add_argument("--json-out", type=str, default=None)
        parser.add_argument("--no-stage-cache", action="store_true")

    def handle(self, *args, **opts):
        if hasattr(sys.stdout, "reconfigure"):
            # Windows consoles default to cp1252; never crash a long run on a stray character.
            sys.stdout.reconfigure(errors="replace")
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stdout)

        bundle = load_bundle(Path(opts["model"]) if opts["model"] else None)
        params = RankingParams.from_bundle(bundle) if bundle else RankingParams()
        if opts["params"]:
            params = RankingParams.from_dict({**params.to_dict(), **json.loads(opts["params"])})
        served = ServingParams.from_dict((bundle or {}).get("metadata", {}).get("serving")) \
            if bundle and (bundle.get("metadata") or {}).get("serving") else LEGACY_SERVING
        self.stdout.write(self.style.NOTICE(f"Ranking params: {params.to_dict()}"))
        self.stdout.write(self.style.NOTICE(f"Served transform: {served.to_dict()}"))

        if opts["dataset_cache"]:
            df, catalog = load_cached_dataset(opts["dataset_cache"])
            watchlist_df = None
        else:
            df, catalog = load_dataset()
            watchlist_df = self._load_watchlist()
        if df is None or df.empty:
            self.stderr.write(self.style.ERROR("Dataset empty"))
            return

        fingerprint = dataset_fingerprint(df)
        splits = three_way_temporal_split(df)
        del df
        if opts["protocol"] == "AB->C":
            fit_df, eval_df = splits.ab(), splits.c()
        else:
            fit_df, eval_df = splits.a(), splits.b()
        del splits

        cache_key = stage_key(fingerprint, SPLIT_VERSION, opts["protocol"], params.fit_key(),
                              watchlist_df is not None)
        models = load_or_fit(
            "base_models", cache_key,
            lambda: fit_base_models(fit_df, catalog, params, watchlist_df=watchlist_df),
            enabled=not opts["no_stage_cache"],
        )
        models.set_blend(params.blend_k)
        ranking = models.ranking

        seen = build_train_csr(fit_df, ranking.user_to_idx, ranking.item_to_idx)
        targets = build_eval_targets(eval_df, ranking.user_to_idx, ranking.item_to_idx,
                                     positive_threshold=params.positive_threshold,
                                     max_users=int(opts["users"]))
        items = ItemArrays.build(models.idx_to_item, catalog)
        pop_pct = popularity_percentile(models.item_counts)

        wanted = [s.strip() for s in opts["stages"].split(",") if s.strip()]
        X_pos = None
        if any(w in ("ease", "blend", "blend_served") for w in wanted):
            X_pos = positives_csr(fit_df, models)
            tuning = ((bundle or {}).get("metadata") or {}).get("tuning") or {}
            lam = opts["ease_lambda"] or (tuning.get("ease") or {}).get("lambda_A") or 500.0
            self.stdout.write(f"Fitting EASE (lambda={lam:g}, topk={opts['ease_topk']})...")
            fit_ease_for(models, X_pos, lam=float(lam), topk=int(opts["ease_topk"]), n_vocab=int(opts["ease_items"]))
        beta = opts["ease_beta"] if opts["ease_beta"] is not None else (served.ease_beta or 1.0)
        stage_defs = self._stage_defs(models, served, X_pos, beta)
        if opts["as_is"] and bundle is not None:
            stage_defs["asis_LEAKED"] = self._asis_stage(bundle, ranking)
            wanted.append("asis_LEAKED")

        results: dict[str, EvalResult] = {}
        for name in wanted:
            if name not in stage_defs or stage_defs[name] is None:
                self.stdout.write(self.style.WARNING(f"Skipping unknown/unavailable stage '{name}'"))
                continue
            scorer, serving = stage_defs[name]
            results[name] = evaluate_stage(scorer, targets, seen, items=items, serving=serving,
                                           item_pop_pct=pop_pct)
            self.stdout.write(f"  {name}: NDCG@10={results[name].ndcg_at_k:.4f}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Protocol {opts['protocol']}: {targets.n_ratings} held-out positives over "
            f"{len(targets.users)} users, {targets.n_items} items"
        ))
        self.stdout.write(format_stage_table(results, baseline="mostpop", reference="ials_legacy"))

        if opts["show_user"]:
            self._show_user(opts["show_user"], stage_defs, wanted, models, seen, items)

        if opts["json_out"]:
            payload = {
                "protocol": opts["protocol"],
                "params": params.to_dict(),
                "served": served.to_dict(),
                "stages": {k: v.to_dict() for k, v in results.items()},
            }
            Path(opts["json_out"]).write_text(json.dumps(payload, indent=2))
            self.stdout.write(f"Wrote {opts['json_out']}")

    # ------------------------------------------------------------------

    @staticmethod
    def _stage_defs(models, served: ServingParams, X_pos, beta: float) -> dict:
        ranking = models.ranking
        ials = factor_scorer(ranking.user_factors, ranking.item_factors)
        defs = {
            "mostpop": (popularity_scorer(models.item_counts), None),
            "ials": (ials, None),
            "ials_legacy": (ials, LEGACY_SERVING),
            "ials_served": (ials, served),
        }
        ease = models.extras.get("ease")
        if ease is not None and X_pos is not None:
            blend = blend_scorer(ials, ease, X_pos, beta)
            defs.update({
                "ease": (ease_scorer(ease, X_pos), None),
                "blend": (blend, None),
                "blend_served": (blend, served),
            })
        return defs

    def _asis_stage(self, bundle: dict, ranking):
        """The shipped factors, re-indexed into this fit's user/item space."""
        self.stdout.write(self.style.WARNING(
            "asis_LEAKED scores the shipped model, which was trained on the eval rows: "
            "its numbers are optimistic and must not be compared with the clean stages."
        ))
        b_rank = bundle.get("ranking", {})
        b_u2i, b_i2i = b_rank.get("user_to_idx", {}), b_rank.get("item_to_idx", {})
        uf, itf = b_rank.get("user_factors"), b_rank.get("item_factors")
        if uf is None or itf is None:
            return None
        k = uf.shape[1]
        U = np.zeros((len(ranking.user_to_idx), k), dtype=np.float32)
        V = np.zeros((len(ranking.item_to_idx), k), dtype=np.float32)
        for u, i in ranking.user_to_idx.items():
            j = b_u2i.get(u)
            if j is not None:
                U[i] = uf[j]
        for t, i in ranking.item_to_idx.items():
            j = b_i2i.get(t)
            if j is not None:
                V[i] = itf[j]
        return factor_scorer(U, V), LEGACY_SERVING

    def _show_user(self, user_id: str, stage_defs: dict, wanted: list[str], models, seen, items) -> None:
        u = models.ranking.user_to_idx.get(user_id)
        if u is None:
            self.stdout.write(self.style.WARNING(f"{user_id} is not in the fit's user index"))
            return
        idx_to_item = models.idx_to_item
        titles = _title_lookup([int(t) for t in idx_to_item])
        for name in wanted:
            if name not in stage_defs or stage_defs[name] is None:
                continue
            scorer, serving = stage_defs[name]
            scores = np.asarray(scorer(np.array([u])), dtype=np.float32)
            scores[0, seen.indices[seen.indptr[u]:seen.indptr[u + 1]]] = -np.inf
            top = rank_batch(scores, items, 20, serving)[0]
            self.stdout.write(self.style.NOTICE(f"\n[{name}] top-20 for {user_id}"))
            for rank, i in enumerate(top, 1):
                tmdb_id = int(idx_to_item[i])
                self.stdout.write(f"  {rank:2d}. {titles.get(tmdb_id, '?')}  (tmdb {tmdb_id})")

    @staticmethod
    def _load_watchlist():
        from movies.services.recommender.data_loading import load_watchlist_pairs
        try:
            wl = load_watchlist_pairs()
            return wl if not wl.empty else None
        except Exception:
            logger.exception("Failed to load Watchlist pairs; continuing without them")
            return None


def _title_lookup(tmdb_ids: list[int]) -> dict[int, str]:
    """tmdb_id -> title, from the local DB when reachable, else MovieLens movies.csv."""
    try:
        from movies.models import Movie
        return dict(Movie.objects.filter(tmdb_id__in=tmdb_ids).values_list("tmdb_id", "title"))
    except Exception:
        pass
    try:
        import pandas as pd
        from django.conf import settings
        d = Path(settings.BASE_DIR) / "data" / "ml-32m"
        links = pd.read_csv(d / "links.csv", usecols=["movieId", "tmdbId"]).dropna()
        movies = pd.read_csv(d / "movies.csv", usecols=["movieId", "title"])
        m = links.merge(movies, on="movieId")
        return dict(zip(m["tmdbId"].astype(int), m["title"]))
    except Exception:
        return {}
