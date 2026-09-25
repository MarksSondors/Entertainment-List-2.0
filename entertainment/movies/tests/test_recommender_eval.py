"""Unit tests for the recommender evaluation protocol (splits, serving transforms, metrics).

Synthetic data only — no MovieLens files or DB access.
"""
import numpy as np
import pandas as pd
from django.test import SimpleTestCase
from scipy.sparse import csr_matrix

from movies.services.recommender.data_loading import CatalogLookups
from movies.services.recommender.evaluation import (
    EvalTargets,
    balanced_objective,
    build_eval_targets,
    build_train_csr,
    evaluate_scorer,
    predict_explicit,
    stratified_temporal_split,
)
from movies.services.recommender.scoring import (
    LEGACY_SERVING,
    ItemArrays,
    ServingParams,
    mmr_select,
    rank_batch,
    zscore_rows,
)
from movies.services.recommender.splits import three_way_temporal_split


def _ratings_frame(user_sizes, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    ts = 0
    for u, n in enumerate(user_sizes):
        for j in range(n):
            ts += int(rng.integers(1, 100))
            rows.append({"user_id": f"ml_{u}", "tmdb_id": 1000 + j, "rating": float(rng.integers(1, 11)) / 2,
                         "timestamp": ts})
    df = pd.DataFrame(rows)
    return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)  # shuffled input


def _old_stratified_split(df, val_fraction=0.2, min_user_ratings_for_val=2):
    """The pre-v5.1 per-user loop, kept here as the reference implementation."""
    df = df.sort_values("timestamp").reset_index(drop=True)
    val_indices = []
    for _, group in df.groupby("user_id", sort=False):
        n = len(group)
        if n < min_user_ratings_for_val:
            continue
        n_val = max(1, int(round(n * val_fraction)))
        val_indices.extend(group.index[-n_val:].tolist())
    mask = df.index.isin(set(val_indices))
    return df.loc[~mask].reset_index(drop=True), df.loc[mask].reset_index(drop=True)


class ThreeWaySplitTests(SimpleTestCase):
    def setUp(self):
        self.sizes = [1, 2, 3, 5, 8, 10, 13, 20, 37]
        self.df = _ratings_frame(self.sizes)

    def test_parts_partition_rows_and_respect_time_order(self):
        s = three_way_temporal_split(self.df, c_frac=0.2, b_frac=0.15)
        a, b, c = s.a(), s.b(), s.c()
        self.assertEqual(len(a) + len(b) + len(c), len(self.df))
        for u, n in enumerate(self.sizes):
            uid = f"ml_{u}"
            ua, ub, uc = (p[p.user_id == uid].timestamp for p in (a, b, c))
            if n < 2:
                self.assertEqual((len(ua), len(ub), len(uc)), (n, 0, 0))
                continue
            self.assertEqual(len(uc), max(1, round(n * 0.2)))
            self.assertGreaterEqual(len(ua), 1, "A must keep at least one row per user")
            if len(ub):
                self.assertLess(ua.max(), ub.min())
                self.assertLess(ub.max(), uc.min())
            else:
                self.assertLess(ua.max(), uc.min())

    def test_ab_and_c_match_the_legacy_split(self):
        old_train, old_val = _old_stratified_split(self.df)
        new_train, new_val = stratified_temporal_split(self.df)
        key = ["user_id", "tmdb_id", "timestamp"]
        pd.testing.assert_frame_equal(
            old_train.sort_values(key).reset_index(drop=True)[key],
            new_train.sort_values(key).reset_index(drop=True)[key],
        )
        pd.testing.assert_frame_equal(
            old_val.sort_values(key).reset_index(drop=True)[key],
            new_val.sort_values(key).reset_index(drop=True)[key],
        )
        s = three_way_temporal_split(self.df)
        self.assertEqual(len(s.ab()), len(old_train))
        self.assertEqual(len(s.c()), len(old_val))


def _items(genres, languages=None, decades=None, votes=None):
    n = len(genres)
    catalog = CatalogLookups(
        tmdb_to_genres={i: g for i, g in enumerate(genres)},
        tmdb_to_language={i: (languages or ["en"] * n)[i] for i in range(n)},
        tmdb_to_runtime_bucket={i: "standard" for i in range(n)},
        tmdb_to_year={i: (decades or [2000] * n)[i] for i in range(n)},
        tmdb_vote_data={i: (7.0, (votes or [100] * n)[i]) for i in range(n)},
    )
    return ItemArrays.build(range(n), catalog)


class ScoringTests(SimpleTestCase):
    def test_zscore_rows_keeps_masked_entries(self):
        s = np.array([[1.0, 2.0, 3.0, -np.inf]], dtype=np.float32)
        z = zscore_rows(s)
        self.assertTrue(np.isneginf(z[0, 3]))
        self.assertAlmostEqual(float(z[0, :3].mean()), 0.0, places=5)
        self.assertAlmostEqual(float(z[0, :3].std()), 1.0, places=5)

    def test_mmr_alpha_one_is_pure_relevance(self):
        items = _items([["Action"]] * 5)
        pool = np.array([4, 2, 0, 1, 3])
        np.testing.assert_array_equal(mmr_select(pool, np.linspace(1, 0, 5), items, 3, alpha=1.0), pool[:3])

    def test_mmr_prefers_a_dissimilar_item_over_a_near_duplicate(self):
        # items 0,1 are identical action movies; item 2 is a (slightly less relevant) romance.
        # Item 3 is a low-relevance tail so min-max normalization doesn't zero item 2.
        items = _items([["Action"], ["Action"], ["Romance"], ["War"]], languages=["en", "en", "fr", "de"],
                       decades=[2000, 2000, 1970, 1950])
        chosen = mmr_select(np.array([0, 1, 2, 3]), np.array([1.0, 0.95, 0.9, 0.0]), items, 2, alpha=0.7)
        np.testing.assert_array_equal(chosen, [0, 2])

    def test_rank_batch_never_returns_masked_items(self):
        items = _items([["Action"], ["Drama"], ["Comedy"], ["Horror"], ["War"], ["Western"]])
        scores = np.array([[5, 4, -np.inf, 3, -np.inf, 1]], dtype=np.float32)
        for params in (None, LEGACY_SERVING, ServingParams(pop_lambda=0.3, mmr_alpha=0.7)):
            top = rank_batch(scores, items, 3, params)[0]
            self.assertTrue(np.all(np.isfinite(scores[0, top])), f"{params}: {top}")

    def test_popularity_penalty_demotes_popular_items(self):
        items = _items([["Action"], ["Action"], ["Action"]], votes=[1_000_000, 10, 10])
        scores = np.array([[1.0, 0.99, 0.0]], dtype=np.float32)
        top = rank_batch(scores, items, 1, ServingParams(pop_lambda=0.5))[0]
        self.assertEqual(int(top[0]), 1)

    def test_serving_params_round_trip(self):
        self.assertEqual(ServingParams.from_dict(LEGACY_SERVING.to_dict()), LEGACY_SERVING)
        self.assertEqual(ServingParams.from_dict(None), ServingParams())


class RankingMetricTests(SimpleTestCase):
    def _targets(self, relevant, n_items):
        users = np.array(sorted(relevant), dtype=np.int32)
        return EvalTargets(users=users, user_ids=np.array([f"ml_{u}" for u in users], dtype=object),
                           relevant=relevant, n_items=n_items,
                           n_ratings=sum(len(v) for v in relevant.values()))

    def test_hand_computed_metrics(self):
        # user 0: relevant {1, 3}; ranking 3,0,1 -> hits at ranks 1 and 3
        # user 1: relevant {4};    ranking 2,0,1 -> miss
        scores = np.array([[0.8, 0.5, 0.0, 0.9, 0.1],
                           [0.8, 0.7, 0.9, 0.0, 0.1]], dtype=np.float32)
        targets = self._targets({0: {1: 1, 3: 2}, 1: {4: 1}}, n_items=5)
        seen = csr_matrix((2, 5), dtype=np.float32)
        r = evaluate_scorer(lambda u: scores[u], targets, seen, k=3)

        d = 1.0 / np.log2(np.arange(2, 5))
        ndcg_u0 = (d[0] + d[2]) / (d[0] + d[1])
        self.assertAlmostEqual(r.ndcg_at_k, ndcg_u0 / 2, places=6)
        self.assertAlmostEqual(r.recall_at_k, (2 / 2 + 0) / 2, places=6)
        self.assertAlmostEqual(r.hit_rate_at_k, 0.5)
        self.assertAlmostEqual(r.mrr, (1.0 + 0) / 2)
        g_u0 = (3 * d[0] + 1 * d[2]) / (3 * d[0] + 1 * d[1])
        self.assertAlmostEqual(r.ndcg_graded_at_k, g_u0 / 2, places=6)
        self.assertAlmostEqual(r.coverage_at_k, 4 / 5)  # items 3,0,1,2 recommended

    def test_seen_items_are_masked(self):
        scores = np.array([[0.9, 0.8, 0.1]], dtype=np.float32)
        targets = self._targets({0: {1: 1}}, n_items=3)
        seen = csr_matrix(([1.0], ([0], [0])), shape=(1, 3))
        r = evaluate_scorer(lambda u: scores[u], targets, seen, k=1)
        self.assertEqual(r.hit_rate_at_k, 1.0)

    def test_build_eval_targets_keeps_local_users_when_sampling(self):
        user_to_idx = {f"ml_{i}": i for i in range(50)} | {"loc_1": 50}
        item_to_idx = {100: 0, 101: 1}
        eval_df = pd.DataFrame({
            "user_id": list(user_to_idx),
            "tmdb_id": [100] * 51,
            "rating": [4.0] * 51,
        })
        t = build_eval_targets(eval_df, user_to_idx, item_to_idx, max_users=5)
        self.assertEqual(len(t.users), 5)
        self.assertIn("loc_1", list(t.user_ids))
        self.assertEqual(len(t.subset("loc_").users), 1)

    def test_build_train_csr_dedups(self):
        df = pd.DataFrame({"user_id": ["a", "a", "b"], "tmdb_id": [1, 1, 2]})
        m = build_train_csr(df, {"a": 0, "b": 1}, {1: 0, 2: 1})
        self.assertEqual(m.nnz, 2)

    def test_balanced_objective_penalizes_only_below_floor(self):
        from movies.services.recommender.evaluation import EvalResult
        self.assertEqual(balanced_objective(EvalResult(ndcg_at_k=0.3, coverage_at_k=0.1), 0.05), 0.3)
        self.assertAlmostEqual(balanced_objective(EvalResult(ndcg_at_k=0.3, coverage_at_k=0.02), 0.05),
                               0.3 - 0.5 * 0.03)


class PredictExplicitTests(SimpleTestCase):
    def test_time_trend_matches_manual_formula(self):
        biases = {
            "global_mean": 3.5, "year_biases": {}, "item_biases": {}, "user_biases": {"u": 0.1},
            "user_time_trend": {"u": 0.4}, "user_time_norm": {"u": (100.0, 300.0)},
        }
        df = pd.DataFrame({"user_id": ["u", "u", "v"], "tmdb_id": [1, 2, 3], "year": [2000] * 3,
                           "timestamp": [100, 900, 500]})
        pred = predict_explicit(df, biases)
        expected_u0 = 3.5 + 0.1 + 0.4 * (-0.5)
        expected_u1 = 3.5 + 0.1 + 0.4 * 1.0     # (900-100)/200 - 0.5 = 3.5, clipped to 1
        np.testing.assert_allclose(pred, [expected_u0, expected_u1, 3.5], rtol=1e-6)
