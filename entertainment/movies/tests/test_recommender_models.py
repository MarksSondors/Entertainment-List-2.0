"""Unit tests for the recommender's model plumbing: fold-in consistency, the
promotion gate, and hyperparameter/recipe round-trips. Synthetic data only."""
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from movies.management.commands.update_recommender import _solve_user_factor, bundle_confidence_recipe
from movies.services.recommender.evaluation import popularity_percentile
from movies.services.recommender.mf_ranking import fold_in_user_factor
from movies.services.recommender.model_io import promotion_decision
from movies.services.recommender.pipeline import RankingParams, fit_base_models
from movies.services.recommender.weights import ConfidenceRecipe


def _synthetic_ratings(n_users=300, n_items=80, per_user=25, seed=0):
    """Low-rank taste structure so the factorization has something real to learn."""
    rng = np.random.default_rng(seed)
    U = rng.normal(size=(n_users, 4))
    V = rng.normal(size=(n_items, 4))
    rows = []
    for u in range(n_users):
        affinity = U[u] @ V.T
        items = rng.choice(n_items, size=per_user, replace=False, p=np.exp(affinity) / np.exp(affinity).sum())
        for j, i in enumerate(items):
            rating = float(np.clip(np.round(2 * (3.0 + affinity[i] / 2)) / 2, 0.5, 5.0))
            user_id = f"loc_{u}" if u < 5 else f"ml_{u}"
            rows.append({"user_id": user_id, "tmdb_id": 1000 + int(i), "rating": rating,
                         "timestamp": 1_600_000_000 + u * 1000 + j * 86_400})
    return pd.DataFrame(rows)


def _cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


class FoldInConsistencyTests(SimpleTestCase):
    """Regression test for the fold-in bug: update_recommender used to ignore implicit's
    outer alpha and use a hardcoded regularization, so folded-in factors for the real
    (local) users came out on a different scale than the trained ones."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.df = _synthetic_ratings()
        cls.params = RankingParams(factors=8, regularization=0.5, iterations=60, alpha=0.5,
                                   conf_alpha=10.0, recency_half_life_days=365.0, blend_k=None)
        cls.models = fit_base_models(cls.df, catalog=None, params=cls.params, cold_start=False)

    def _user_rows(self, user_id):
        r = self.models.ranking
        pos = self.df[(self.df.user_id == user_id) & (self.df.rating >= self.params.positive_threshold)]
        idx = pos.tmdb_id.map(r.item_to_idx).to_numpy()
        stored = self.models.recipe.rating_confidence(pos.rating.to_numpy(), np.ones(len(pos), bool),
                                                      pos.timestamp.to_numpy())
        return pos, idx, stored

    def test_fold_in_reproduces_trained_user_factor(self):
        r = self.models.ranking
        for user_id in ("loc_0", "loc_3"):
            _, idx, stored = self._user_rows(user_id)
            folded = fold_in_user_factor(r.item_factors, idx, stored, self.models.recipe)
            trained = r.user_factors[r.user_to_idx[user_id]]
            self.assertGreater(_cosine(folded, trained), 0.99, user_id)
            self.assertAlmostEqual(np.linalg.norm(folded) / np.linalg.norm(trained), 1.0, delta=0.05)

    def test_old_recipe_constants_produce_a_different_factor(self):
        # What the pre-fix fold-in effectively did: outer alpha 1.0, reg 0.05.
        r = self.models.ranking
        _, idx, stored = self._user_rows("loc_0")
        buggy = ConfidenceRecipe(**{**self.models.recipe.to_dict(), "outer_alpha": 1.0, "regularization": 0.05})
        folded = fold_in_user_factor(r.item_factors, idx, stored, buggy)
        trained = r.user_factors[r.user_to_idx["loc_0"]]
        self.assertGreater(abs(np.linalg.norm(folded) / np.linalg.norm(trained) - 1.0), 0.05)

    def test_update_recommender_payload_path_matches(self):
        r = self.models.ranking
        pos, idx, stored = self._user_rows("loc_1")
        bundle = {"ranking": {"item_to_idx": r.item_to_idx, "item_factors": r.item_factors,
                              "confidence": self.models.recipe.to_dict()}}
        payload = [{"tmdb_id": int(t), "rating": float(x), "timestamp": float(ts)}
                   for t, x, ts in zip(pos.tmdb_id, pos.rating, pos.timestamp)]
        via_command = _solve_user_factor(payload, [], bundle, bundle_confidence_recipe(bundle))
        direct = fold_in_user_factor(r.item_factors, idx, stored, self.models.recipe)
        np.testing.assert_allclose(via_command, direct, rtol=1e-5, atol=1e-6)

        with_watchlist = _solve_user_factor(payload, [int(pos.tmdb_id.iloc[0]) + 10_000, 1000], bundle,
                                            bundle_confidence_recipe(bundle))
        self.assertFalse(np.allclose(with_watchlist, via_command), "watchlist items must contribute")

    def test_legacy_bundle_recipe_uses_trained_alpha_and_reg(self):
        legacy = {"ranking": {"alpha": 0.535, "regularization": 0.0067, "model_type": "ials",
                              "positive_threshold": 3.5}}
        recipe = bundle_confidence_recipe(legacy)
        self.assertAlmostEqual(recipe.outer_alpha, 0.535)
        self.assertAlmostEqual(recipe.regularization, 0.0067)
        self.assertEqual(recipe.conf_alpha, 40.0)


class PromotionGateTests(SimpleTestCase):
    def _meta(self, ndcg, proto=3):
        return {"eval_protocol": proto, "eval": {"ndcg_at_k": ndcg}}

    def test_promotes_without_champion_or_on_protocol_change(self):
        self.assertTrue(promotion_decision(self._meta(0.1), None)[0])
        self.assertTrue(promotion_decision(self._meta(0.1), {"eval": {"ndcg_at_k": 0.38}})[0])

    def test_blocks_a_regression_beyond_tolerance(self):
        promote, reason = promotion_decision(self._meta(0.140), self._meta(0.150))
        self.assertFalse(promote)
        self.assertIn("below champion", reason)

    def test_allows_small_noise_and_improvements(self):
        self.assertTrue(promotion_decision(self._meta(0.148), self._meta(0.150))[0])
        self.assertTrue(promotion_decision(self._meta(0.160), self._meta(0.150))[0])


class ParamsTests(SimpleTestCase):
    def test_fit_key_ignores_post_hoc_blend(self):
        a = RankingParams(factors=64, blend_k=20.0)
        b = RankingParams(factors=64, blend_k=None)
        self.assertEqual(a.fit_key(), b.fit_key())
        self.assertNotEqual(a.fit_key(), RankingParams(factors=96).fit_key())

    def test_from_legacy_bundle(self):
        p = RankingParams.from_bundle({"ranking": {"factors": 128, "regularization": 0.025, "iterations": 26,
                                                   "alpha": 0.6, "model_type": "ials"}})
        self.assertEqual((p.factors, p.iterations, p.conf_alpha), (128, 26, 40.0))

    def test_recipe_round_trip(self):
        r = RankingParams(alpha=0.7, regularization=0.3, conf_alpha=12.0).recipe(reference_ts=123.0)
        self.assertEqual(ConfidenceRecipe.from_dict(r.to_dict()), r)
        self.assertEqual(r.outer_alpha, 0.7)
        self.assertEqual(RankingParams(model_type="bpr", alpha=0.01).recipe().outer_alpha, 1.0)

    def test_popularity_percentile_is_interaction_weighted(self):
        np.testing.assert_allclose(popularity_percentile(np.array([6, 1, 2, 1])), [1.0, 0.2, 0.4, 0.2], rtol=1e-6)
