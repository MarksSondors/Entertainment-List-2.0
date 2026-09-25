"""EASE^R correctness against the textbook dense closed form. Synthetic data only."""
import numpy as np
from django.test import SimpleTestCase
from scipy.sparse import csr_matrix

from movies.services.recommender.ease import (
    EaseModel,
    choose_vocab_size,
    fit_ease,
    gram_matrix,
    prune_topk_columns,
    select_ease_vocab,
    solve_ease,
)


def _random_binary(n_users=200, n_items=40, density=0.15, seed=0):
    rng = np.random.default_rng(seed)
    return csr_matrix((rng.random((n_users, n_items)) < density).astype(np.float32))


def _dense_ease(X, lam):
    G = (X.T @ X).toarray().astype(np.float64)
    P = np.linalg.inv(G + lam * np.eye(G.shape[0]))
    B = -P / np.diag(P)[None, :]
    np.fill_diagonal(B, 0.0)
    return B


class EaseTests(SimpleTestCase):
    def test_gram_matrix_matches_dense_product_across_chunks(self):
        X = _random_binary()
        np.testing.assert_array_equal(gram_matrix(X, user_chunk=37), (X.T @ X).toarray())

    def test_solve_and_unpruned_weights_match_dense_formula(self):
        X = _random_binary()
        G = gram_matrix(X)
        P = solve_ease(G, 25.0)
        np.testing.assert_allclose(P, np.linalg.inv(G.astype(np.float64) + 25.0 * np.eye(40)), rtol=1e-8, atol=1e-10)
        B = prune_topk_columns(solve_ease(G, 25.0), k=None, block=7).toarray()
        np.testing.assert_allclose(B, _dense_ease(X, 25.0), rtol=1e-5, atol=1e-6)

    def test_topk_keeps_largest_entries_per_column(self):
        X = _random_binary()
        dense = _dense_ease(X, 10.0)
        B = prune_topk_columns(solve_ease(gram_matrix(X), 10.0), k=5, block=16).toarray()
        self.assertTrue(np.all((B != 0).sum(axis=0) <= 5))
        for j in range(dense.shape[1]):
            kept = np.abs(B[:, j][B[:, j] != 0])
            self.assertGreaterEqual(kept.min(), np.sort(np.abs(dense[:, j]))[-5] - 1e-6)

    def test_score_one_matches_score_batch_with_out_of_vocab_items(self):
        X = _random_binary(n_items=40)
        vocab = select_ease_vocab(np.asarray(X.sum(axis=0)).ravel(), n_max=30, min_count=1)
        model = fit_ease(X, vocab, lam=20.0, topk=10)
        batch = model.score_batch(X[:5])
        for u in range(5):
            one = model.score_one(X[u].indices)
            np.testing.assert_allclose(one, batch[u], rtol=1e-5, atol=1e-6, equal_nan=True)
        oov = np.setdiff1d(np.arange(40), vocab)
        self.assertTrue(np.isnan(batch[:, oov]).all())

    def test_vocab_selection_and_memory_guard(self):
        counts = np.array([5, 100, 30, 1, 50, 20])
        np.testing.assert_array_equal(select_ease_vocab(counts, n_max=3, min_count=10), [1, 2, 4])
        self.assertEqual(choose_vocab_size(20_000, available_bytes=100 * 2 ** 30), 20_000)
        self.assertEqual(choose_vocab_size(20_000, available_bytes=5 * 2 ** 30), 15_000)

    def test_bundle_round_trip_shape(self):
        X = _random_binary()
        vocab = np.arange(40)
        model = fit_ease(X, vocab, lam=10.0, topk=8)
        section = model.to_bundle(idx_to_item=np.arange(1000, 1040))
        self.assertEqual(section["item_ids"][0], 1000)
        self.assertEqual(len(section["indptr"]), 41)
        self.assertIsInstance(model, EaseModel)
