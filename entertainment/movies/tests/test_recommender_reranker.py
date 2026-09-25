"""Reranker plumbing: numpy tree evaluator parity with LightGBM, candidate generation,
and feature consistency. Synthetic data only; LightGBM tests skip if it isn't installed
(it is a training-only dependency)."""
import unittest

import numpy as np
from django.test import SimpleTestCase

from movies.services.recommender.data_loading import TMDB_GENRES, CatalogLookups
from movies.services.recommender.features import (
    FEATURE_NAMES,
    ItemFeatureTable,
    compute_features,
    dict_bias_lookup,
    generate_candidates,
    user_context,
)
from movies.services.recommender.reranker import (
    TrainingData,
    booster_to_numpy_trees,
    check_parity,
    predict_numpy_trees,
    train_lambdarank,
)

try:
    import lightgbm  # noqa: F401
    HAS_LGB = True
except ImportError:
    HAS_LGB = False


def _ranking_data(n_groups=300, per_group=40, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n_groups * per_group, len(FEATURE_NAMES))).astype(np.float32)
    score = X[:, 0] * 1.5 + np.sin(X[:, 5]) - 0.5 * X[:, 8] ** 2
    y = np.digitize(score + rng.normal(scale=0.5, size=len(score)), [1.0, 2.0]).astype(np.int32)
    return TrainingData(X=X, y=y, group=np.full(n_groups, per_group), users=np.arange(n_groups), cand_recall=1.0)


@unittest.skipUnless(HAS_LGB, "lightgbm not installed (training-only dependency)")
class NumpyTreeParityTests(SimpleTestCase):
    def test_numpy_trees_match_lightgbm(self):
        data = _ranking_data()
        booster, X_val = train_lambdarank(data, num_boost_round=60, early_stopping=10,
                                          params={"min_data_in_leaf": 20, "num_leaves": 15})
        trees = booster_to_numpy_trees(booster)
        self.assertLess(check_parity(booster, trees, data.X), 1e-5)
        # exact-threshold ties must go left, like LightGBM's "<="
        X_tie = data.X[:50].copy()
        X_tie[:, trees["split_feature"][0]] = trees["threshold"][0]
        self.assertLess(check_parity(booster, trees, X_tie), 1e-5)

    def test_single_leaf_tree(self):
        trees = {"split_feature": np.zeros(0, np.int32), "threshold": np.zeros(0), "left": np.zeros(0, np.int32),
                 "right": np.zeros(0, np.int32), "leaf_value": np.array([0.25]), "roots": np.array([~0]),
                 "max_depth": 0}
        np.testing.assert_allclose(predict_numpy_trees(trees, np.zeros((3, 2))), [0.25] * 3)


class CandidateTests(SimpleTestCase):
    def test_union_excludes_seen_and_flags_sources(self):
        ials = np.array([9, 8, 7, 1, 0, 0], dtype=float)
        ease = np.array([np.nan, 0, 5, 6, 7, np.nan])
        pop = np.array([100, 1, 1, 1, 1, 50], dtype=float)
        excluded = np.array([True, False, False, False, False, False])
        cand, src = generate_candidates(ials, ease, pop, excluded, k_ials=2, k_ease=2, k_pop=1)
        self.assertNotIn(0, cand)
        np.testing.assert_array_equal(cand, [1, 2, 3, 4, 5])
        np.testing.assert_array_equal(src[:, 0], [True, True, False, False, False])   # iALS top-2 = 1, 2
        np.testing.assert_array_equal(src[:, 1], [False, False, True, True, False])   # EASE top-2 = 4, 3
        np.testing.assert_array_equal(src[:, 2], [False, False, False, False, True])  # pop top-1 = 5


def _table(n=12, k=4, seed=0):
    rng = np.random.default_rng(seed)
    ids = np.arange(100, 100 + n)
    catalog = CatalogLookups(
        tmdb_to_genres={int(t): [TMDB_GENRES[i % 5], TMDB_GENRES[(i + 2) % 7]] for i, t in enumerate(ids)},
        tmdb_to_language={int(t): ["en", "fr"][i % 2] for i, t in enumerate(ids)},
        tmdb_to_runtime_bucket={int(t): "standard" for t in ids},
        tmdb_to_year={int(t): 1990 + i for i, t in enumerate(ids)},
        tmdb_vote_data={int(t): (7.0, 10 * (i + 1)) for i, t in enumerate(ids)},
    )
    biases = {"global_mean": 3.5, "year_biases": {1995: 0.1}, "item_biases": {101: 0.2},
              "user_biases": {"ml_1": 0.3}, "user_genre_biases": {TMDB_GENRES[0]: {"ml_1": 0.4}},
              "user_decade_biases": {1990: {"ml_1": -0.2}, 2000: {}},
              "user_language_biases": {"fr": {"ml_1": 0.1}, "en": {}},
              "user_runtime_biases": {"standard": {"ml_1": 0.05}}}
    factors = rng.normal(size=(n, k)).astype(np.float32)
    return ItemFeatureTable.build(ids, catalog, biases, np.linspace(0, 1, n), factors), biases, factors


class FeatureTests(SimpleTestCase):
    def test_features_are_finite_and_bias_pred_matches_hand_sum(self):
        table, biases, factors = _table()
        ctx = user_context(np.ones(4), table, ratings=np.array([5.0, 2.0, 4.0]), item_idx=np.array([0, 1, -1]),
                           timestamps=np.array([1.0, 2.0, 3.0]), query_ts=1e9, positive_threshold=3.5,
                           bias_lookup=dict_bias_lookup(biases, "ml_1"), user_bias=0.3)
        ials_full = factors @ np.ones(4)
        ease_full = np.where(np.arange(12) < 8, np.arange(12, dtype=float), np.nan)
        cand = np.array([1, 5, 9])
        src = np.ones((3, 3), dtype=bool)
        F = compute_features(ctx, cand, src, table, ials_full, ease_full)
        self.assertEqual(F.shape, (3, len(FEATURE_NAMES)))
        self.assertTrue(np.isfinite(F).all())
        col = {n: i for i, n in enumerate(FEATURE_NAMES)}
        # item 1 = tmdb 101: year 1991 (decade 1990), fr, genres [TMDB_GENRES[1], TMDB_GENRES[3]]
        expected = 3.5 + 0.2 + 0.3 + (-0.2) + 0.1 + 0.05
        self.assertAlmostEqual(float(F[0, col["bias_pred"]]), expected, places=5)
        self.assertEqual(float(F[2, col["ease_in_vocab"]]), 0.0)     # item 9 outside the EASE vocab
        self.assertEqual(float(F[2, col["ease_z"]]), 0.0)
        np.testing.assert_array_equal(ctx.recent_pos_idx, [0])       # only the in-fit positive

    def test_context_from_training_bias_dicts_equals_overlay_style_lookup(self):
        """Serving builds the context through an overlay-first lookup; with no overlay
        it must produce exactly the training-time features."""
        table, biases, factors = _table()
        kw = dict(ratings=np.array([4.5, 3.0]), item_idx=np.array([2, 3]), timestamps=np.array([5.0, 6.0]),
                  query_ts=2e9, positive_threshold=3.5, user_bias=0.3)
        a = user_context(factors[0], table, bias_lookup=dict_bias_lookup(biases, "ml_1"), **kw)

        def serving_lookup(kind, key):   # mirrors MovieRecommender._user_category_bias without overlay
            return float((biases.get(kind) or {}).get(key, {}).get("ml_1", 0.0))
        b = user_context(factors[0], table, bias_lookup=serving_lookup, **kw)
        cand, src = np.array([0, 4, 7]), np.zeros((3, 3), bool)
        full = factors @ factors[0]
        np.testing.assert_array_equal(compute_features(a, cand, src, table, full, None),
                                      compute_features(b, cand, src, table, full, None))


@unittest.skipUnless(HAS_LGB, "lightgbm not installed (training-only dependency)")
class ServingTrainingFeatureParityTests(SimpleTestCase):
    """The reranker score a local user gets at serving time (history from the DB, overlay-
    first biases, EASE from live positives) must equal what the training feature builder
    computes for the same user from the training frame."""

    def test_serving_reranker_matches_training_builder(self):
        from unittest.mock import patch

        import pandas as pd

        from movies.services.recommendation import MovieRecommender
        from movies.services.recommender.model_io import build_bundle
        from movies.services.recommender.pipeline import RankingParams, fit_base_models, fit_ease_for, positives_csr
        from movies.services.recommender.reranker import RerankFeatureBuilder, reranker_to_bundle
        from movies.tests.test_recommender_models import _synthetic_ratings

        df = _synthetic_ratings(n_users=120, n_items=60, per_user=20, seed=3)
        ids = sorted(df.tmdb_id.unique())
        catalog = CatalogLookups(
            tmdb_to_genres={int(t): [TMDB_GENRES[int(t) % 7]] for t in ids},
            tmdb_to_language={int(t): "en" for t in ids},
            tmdb_to_runtime_bucket={int(t): "standard" for t in ids},
            tmdb_to_year={int(t): 1980 + int(t) % 30 for t in ids},
            tmdb_vote_data={int(t): (6.5, int(t) % 97 + 1) for t in ids},
        )
        models = fit_base_models(df, catalog, RankingParams(factors=6, iterations=15, blend_k=None),
                                 cold_start=False)
        X_pos = positives_csr(df, models)
        fit_ease_for(models, X_pos, lam=10.0, topk=15, min_count=1)
        biases = {"global_mean": 3.4, "year_biases": {}, "item_biases": {int(t): 0.01 * (int(t) % 5) for t in ids},
                  "user_biases": {"loc_1": 0.2}, "user_genre_biases": {TMDB_GENRES[1]: {"loc_1": 0.3}},
                  "user_decade_biases": {1990: {"loc_1": 0.1}}, "user_language_biases": {"en": {"loc_1": 0.0}},
                  "user_runtime_biases": {"standard": {"loc_1": -0.05}}}

        builder = RerankFeatureBuilder(models, df, biases, catalog, X_pos, k_ials=15, k_ease=15, k_pop=5)
        booster, _ = train_lambdarank(_ranking_data(n_groups=60, per_group=20), num_boost_round=20,
                                      early_stopping=5, params={"min_data_in_leaf": 5, "num_leaves": 7})
        trees = booster_to_numpy_trees(booster)
        bundle = build_bundle(
            biases=biases, catalog=catalog, ranking=models.ranking, cold_start=None,
            metadata={"trained_at": "test", "serving": {"pop_lambda": 0.0, "ease_beta": 1.0}},
            item_counts=models.item_counts, ease=models.extras["ease"].to_bundle(models.idx_to_item),
        )
        bundle["reranker"] = reranker_to_bundle(trees, best_iteration=booster.best_iteration or 0,
                                                n_train_users=60, cand_k=(15, 15, 5))
        rec = MovieRecommender(bundle=bundle)
        self.assertIsNotNone(rec.reranker)

        u = models.ranking.user_to_idx["loc_1"]
        ratings, items, ts = builder.history(u)
        query_ts = 1_700_000_000.0
        cand, F = builder.for_user(u, query_ts=query_ts)
        expected = np.full(len(models.ranking.item_to_idx), -np.inf, dtype=np.float32)
        expected[cand] = predict_numpy_trees(trees, F)

        scope = rec._get_scope("test", [int(t) for t in models.idx_to_item])
        with patch.object(MovieRecommender, "_user_history", return_value=(ratings, items, ts)), \
                patch("movies.services.recommendation.time.time", return_value=query_ts):
            scores, stats = rec._ranking_scores(1, rec._user_factor("loc_1"), scope)
        self.assertIsNone(stats)
        np.testing.assert_allclose(scores, expected, rtol=1e-5, atol=1e-5)
        self.assertNotIn("reranker", rec._disabled_tiers)
