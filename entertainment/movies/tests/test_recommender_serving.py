"""Serving-side tests: the vectorized displayed rating must match the scalar
``predict_rating`` exactly, and bundles without v5.1 sections must keep loading."""
import time
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase
from scipy.sparse import csr_matrix

from movies.services.recommendation import MovieRecommender
from movies.services.recommender.cold_start import fit_cold_start_head
from movies.services.recommender.data_loading import TMDB_GENRES, CatalogLookups
from movies.services.recommender.ease import fit_ease
from movies.services.recommender.evaluation import blend_scorer, factor_scorer
from movies.services.recommender.mf_ranking import RankingModel
from movies.services.recommender.model_io import build_bundle
from movies.services.recommender.scoring import LEGACY_SERVING, ServingParams


def _synthetic_bundle(serving=None, n_items=30, n_known=24, k=6, seed=0, with_ease=False):
    rng = np.random.default_rng(seed)
    ids = list(range(500, 500 + n_items))
    langs = ["en", "fr", "ja"]
    buckets = ["short", "standard", "long", "epic"]
    catalog = CatalogLookups(
        tmdb_to_genres={t: list(rng.choice(TMDB_GENRES, size=rng.integers(1, 4), replace=False)) for t in ids},
        tmdb_to_language={t: langs[i % 3] for i, t in enumerate(ids)},
        tmdb_to_runtime_bucket={t: buckets[i % 4] for i, t in enumerate(ids)},
        tmdb_to_year={t: 1950 + 3 * i for i, t in enumerate(ids)},
        tmdb_vote_data={t: (6.0 + (i % 4) / 2, 10 * (i + 1) ** 2) for i, t in enumerate(ids)},
    )
    known = ids[:n_known]
    item_to_idx = {t: i for i, t in enumerate(known)}
    user_to_idx = {"loc_1": 0, "loc_2": 1}
    ranking = RankingModel(
        user_factors=rng.normal(size=(2, k)).astype(np.float32),
        item_factors=rng.normal(size=(n_known, k)).astype(np.float32),
        user_to_idx=user_to_idx, item_to_idx=item_to_idx, factors=k, regularization=0.1,
        iterations=10, alpha=1.0, positive_threshold=3.5, trained_with_gpu=False,
    )
    now = time.time()
    biases = {
        "global_mean": 3.4,
        "year_biases": {1950 + 3 * i: float(rng.normal(0, 0.1)) for i in range(n_items)},
        "item_biases": {t: float(rng.normal(0, 0.3)) for t in known},
        "user_biases": {"loc_1": 0.25},
        "user_genre_biases": {g: {"loc_1": float(rng.normal(0, 0.2))} for g in TMDB_GENRES[::2]},
        "user_decade_biases": {d: {"loc_1": float(rng.normal(0, 0.1))} for d in range(1950, 2040, 10)},
        "user_language_biases": {"fr": {"loc_1": 0.3}, "en": {"loc_1": -0.1}},
        "user_runtime_biases": {"long": {"loc_1": 0.15}},
        "user_time_trend": {"loc_1": 0.2},
        "user_time_norm": {"loc_1": (now - 86400 * 400, now - 86400 * 10)},
    }
    cold = fit_cold_start_head(ranking.item_factors, item_to_idx, catalog, ridge_lambda=1.0)
    metadata = {"trained_at": "test-bundle", "explicit_blend_alpha": 0.2}
    if serving is not None:
        metadata["serving"] = serving.to_dict()
    ease_section = None
    if with_ease:
        X = csr_matrix((rng.random((60, n_known)) < 0.3).astype(np.float32))
        vocab = np.arange(0, n_known - 4)          # last 4 trained items fall outside the EASE vocab
        ease_section = fit_ease(X, vocab, lam=5.0, topk=8).to_bundle(np.array(known))
    return build_bundle(biases=biases, catalog=catalog, ranking=ranking, cold_start=cold, metadata=metadata,
                        ease=ease_section), ids


class VectorizedPredictParityTests(SimpleTestCase):
    def test_vectorized_matches_scalar_predict_rating(self):
        bundle, ids = _synthetic_bundle()
        rec = MovieRecommender(bundle=bundle)
        # mix of explicit years, unknown years (None -> catalog year) and cold items
        years = [None if i % 5 == 0 else 1960 + i for i in range(len(ids))]
        scope = rec._get_scope("test", ids, years)
        for user in ("loc_1", "loc_2"):
            u = rec._user_factor(user)
            vec = rec._predict_ratings(user, scope, u)
            scalar = [rec.predict_rating(user, t, year=y) for t, y in zip(ids, years)]
            np.testing.assert_allclose(vec, scalar, rtol=1e-5, atol=1e-5, err_msg=user)
        self.assertTrue(scope.has_factor.all(), "cold items get factors from the cold-start head")

    def test_scope_cache_rebuilds_when_candidates_change(self):
        bundle, ids = _synthetic_bundle()
        rec = MovieRecommender(bundle=bundle)
        a = rec._get_scope("local", ids[:10])
        self.assertIs(rec._get_scope("local", ids[:10]), a)
        self.assertIsNot(rec._get_scope("local", ids[:11]), a)


class BundleCompatibilityTests(SimpleTestCase):
    def test_pre_v51_bundle_gets_legacy_serving(self):
        bundle, _ = _synthetic_bundle(serving=None)
        rec = MovieRecommender(bundle=bundle)
        self.assertEqual(rec.serving, LEGACY_SERVING)
        self.assertIsNotNone(rec._pop_stats)

    def test_tuned_serving_is_read_from_metadata(self):
        tuned = ServingParams(pop_lambda=0.25, pop_normalize=True, mmr_alpha=0.85)
        bundle, _ = _synthetic_bundle(serving=tuned)
        self.assertEqual(MovieRecommender(bundle=bundle).serving, tuned)


class EaseBlendServingParityTests(SimpleTestCase):
    def test_serving_blend_matches_eval_blend_scorer(self):
        tuned = ServingParams(pop_lambda=0.2, pop_normalize=True, mmr_alpha=1.0, ease_beta=0.7)
        bundle, ids = _synthetic_bundle(serving=tuned, with_ease=True)
        rec = MovieRecommender(bundle=bundle)
        self.assertIsNotNone(rec.ease)
        known = ids[:24]
        user_pos = np.array([0, 3, 5, 21])       # includes an out-of-vocab positive
        scope = rec._get_scope("test", known)
        u = rec._user_factor("loc_1")
        with patch.object(MovieRecommender, "_user_positive_item_idx", return_value=user_pos):
            scores, stats = rec._ranking_scores(1, u, scope)

        X = csr_matrix((np.ones(len(user_pos)), (np.zeros(len(user_pos), int), user_pos)), shape=(1, 24))
        expected = blend_scorer(factor_scorer(rec.user_factors[[0]], rec.item_factors), rec.ease, X, 0.7)(
            np.array([0]))[0]
        np.testing.assert_allclose(scores, expected, rtol=1e-4, atol=1e-4)
        self.assertAlmostEqual(stats[0], float(expected.mean()), places=4)

    def test_bundle_without_ease_serves_plain_ials(self):
        bundle, ids = _synthetic_bundle(serving=ServingParams(ease_beta=0.7))
        rec = MovieRecommender(bundle=bundle)
        self.assertIsNone(rec.ease)
        scope = rec._get_scope("test", ids[:24])
        u = rec._user_factor("loc_1")
        scores, _ = rec._ranking_scores(1, u, scope)
        np.testing.assert_allclose(scores, scope.factors @ u, rtol=1e-5)
