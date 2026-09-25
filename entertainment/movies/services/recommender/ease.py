"""Sparse EASE^R item-item model (Steck, 2019).

    B = I - P · diag(1 / diag(P)),   P = (XᵀX + λI)⁻¹,   B_jj = 0

X is the binary user x item matrix of positives over a vocabulary of the N most
popular items (the dense N x N inverse bounds N; see ``choose_vocab_size``). B is
pruned to the top-k entries per target column and shipped as a CSR indexed by
*source* item, so scoring a user is just summing the rows of the items they liked:

    score_u = x_u · B
"""
from __future__ import annotations

import gc
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix

logger = logging.getLogger(__name__)


def estimate_ease_memory(n_items: int) -> int:
    """Peak bytes: a float32 Gram matrix plus a float64 working copy for the inverse."""
    return int(n_items) ** 2 * (4 + 8)


def choose_vocab_size(requested: int, available_bytes: int, *, max_fraction: float = 0.6,
                      candidates=(25_000, 20_000, 15_000, 10_000, 5_000)) -> int:
    """Largest vocabulary <= ``requested`` whose peak memory fits in ``max_fraction`` of RAM."""
    for n in sorted({requested, *[c for c in candidates if c <= requested]}, reverse=True):
        if estimate_ease_memory(n) <= max_fraction * available_bytes:
            return n
    return min(candidates)


def select_ease_vocab(item_counts: np.ndarray, n_max: int, min_count: int = 20) -> np.ndarray:
    """Indices (in the fit's item space) of the ``n_max`` most-interacted items with at
    least ``min_count`` interactions, ordered by index."""
    counts = np.asarray(item_counts)
    eligible = np.flatnonzero(counts >= min_count)
    if len(eligible) > n_max:
        eligible = eligible[np.argsort(-counts[eligible], kind="stable")[:n_max]]
    return np.sort(eligible)


def gram_matrix(X: csr_matrix, user_chunk: int = 20_000) -> np.ndarray:
    """Dense float32 XᵀX accumulated over user chunks, so the (possibly denser than
    dense) full sparse product is never materialized. Counts stay exact in float32
    (all well below 2**24)."""
    n = X.shape[1]
    G = np.zeros((n, n), dtype=np.float32)
    X = X.tocsr().astype(np.float32)
    for start in range(0, X.shape[0], user_chunk):
        chunk = X[start:start + user_chunk]
        prod = (chunk.T @ chunk).tocoo()
        G[prod.row, prod.col] += prod.data   # no duplicate (row, col) pairs in a product
    return G


def solve_ease(G: np.ndarray, lam: float) -> np.ndarray:
    """P = (G + λI)⁻¹ as a dense float64 symmetric matrix, via in-place Cholesky
    (``dpotrf`` + ``dpotri``) to keep the peak at one float64 copy."""
    from scipy.linalg import lapack

    A = G.astype(np.float64)
    A[np.diag_indices_from(A)] += lam
    c, info = lapack.dpotrf(A, lower=0, overwrite_a=1, clean=0)
    if info != 0:
        raise np.linalg.LinAlgError(f"dpotrf failed (info={info})")
    P, info = lapack.dpotri(c, lower=0, overwrite_c=1)
    if info != 0:
        raise np.linalg.LinAlgError(f"dpotri failed (info={info})")
    # dpotri fills only the upper triangle; mirror it block by block to avoid an N x N temp.
    n = P.shape[0]
    block = 2048
    for i in range(0, n, block):
        j = min(i + block, n)
        P[i:j, :i] = P[:i, i:j].T
        sub = P[i:j, i:j]
        P[i:j, i:j] = np.triu(sub) + np.triu(sub, 1).T
    return P


def prune_topk_columns(P: np.ndarray, k: Optional[int], block: int = 1024) -> csr_matrix:
    """Pruned B (float32 CSR, rows = source item) built column-block-wise from P without
    materializing the dense B: per target column j keep the ``k`` largest-|B_ij|
    sources. ``k`` None keeps everything (dense B in sparse form, for testing)."""
    n = P.shape[0]
    diag = np.diag(P).copy()
    rows_out, cols_out, vals_out = [], [], []
    keep = n - 1 if k is None else min(int(k), n - 1)
    for j0 in range(0, n, block):
        j1 = min(j0 + block, n)
        Bblk = -P[:, j0:j1] / diag[None, j0:j1]
        Bblk[np.arange(j0, j1), np.arange(j1 - j0)] = 0.0
        if keep < n - 1:
            top = np.argpartition(-np.abs(Bblk), kth=keep - 1, axis=0)[:keep]
        else:
            top = np.tile(np.arange(n)[:, None], (1, j1 - j0))
        cols = np.broadcast_to(np.arange(j0, j1)[None, :], top.shape)
        vals = np.take_along_axis(Bblk, top, axis=0)
        nz = vals != 0
        rows_out.append(top[nz].astype(np.int32))
        cols_out.append(cols[nz].astype(np.int32))
        vals_out.append(vals[nz].astype(np.float32))
    return coo_matrix(
        (np.concatenate(vals_out), (np.concatenate(rows_out), np.concatenate(cols_out))), shape=(n, n)
    ).tocsr()


@dataclass
class EaseModel:
    """Pruned EASE weights over a vocabulary of the fit's items."""
    vocab: np.ndarray        # (N,) indices into the fit's (iALS) item space
    indptr: np.ndarray       # CSR by source item, over vocab positions
    indices: np.ndarray
    data: np.ndarray
    lam: float
    topk: Optional[int]
    n_items_total: int       # size of the fit's item space scores are scattered into

    @classmethod
    def from_csr(cls, B: csr_matrix, vocab: np.ndarray, lam: float, topk: Optional[int],
                 n_items_total: int) -> "EaseModel":
        B = B.tocsr()
        return cls(vocab=np.asarray(vocab, dtype=np.int32), indptr=B.indptr.astype(np.int64),
                   indices=B.indices.astype(np.int32), data=B.data.astype(np.float32),
                   lam=float(lam), topk=topk, n_items_total=int(n_items_total))

    @property
    def B(self) -> csr_matrix:
        n = len(self.vocab)
        return csr_matrix((self.data, self.indices, self.indptr), shape=(n, n))

    @property
    def vocab_pos(self) -> np.ndarray:
        """(n_items_total,) position of each fit item in the vocab, -1 if out of vocab."""
        pos = np.full(self.n_items_total, -1, dtype=np.int64)
        pos[self.vocab] = np.arange(len(self.vocab))
        return pos

    def score_batch(self, X_full: csr_matrix) -> np.ndarray:
        """(B, n_items_total) binary interaction rows in the fit's item space ->
        (B, n_items_total) EASE scores; out-of-vocab items score NaN."""
        X_vocab = X_full.tocsc()[:, self.vocab].tocsr()
        s = np.asarray((X_vocab @ self.B).todense(), dtype=np.float32)
        out = np.full((X_full.shape[0], self.n_items_total), np.nan, dtype=np.float32)
        out[:, self.vocab] = s
        return out

    def score_one(self, item_idx: np.ndarray) -> np.ndarray:
        """Scores for one user from the fit-space indices of their positives (numpy only,
        no scipy objects: sums the CSR rows of their in-vocab items)."""
        pos = self.vocab_pos[np.asarray(item_idx, dtype=np.int64)]
        pos = np.unique(pos[pos >= 0])
        s = np.zeros(len(self.vocab), dtype=np.float64)
        if len(pos):
            starts, ends = self.indptr[pos], self.indptr[pos + 1]
            sel = np.concatenate([np.arange(a, b) for a, b in zip(starts, ends)]) if len(pos) else np.zeros(0, int)
            s = np.bincount(self.indices[sel], weights=self.data[sel], minlength=len(self.vocab))
        out = np.full(self.n_items_total, np.nan, dtype=np.float32)
        out[self.vocab] = s
        return out

    def to_bundle(self, idx_to_item: np.ndarray) -> dict:
        """Portable bundle section: vocab stored as TMDB ids so it survives re-indexing."""
        return {
            "format": "csr_src_rows_v1",
            "item_ids": np.asarray(idx_to_item[self.vocab], dtype=np.int64),
            "indptr": self.indptr, "indices": self.indices, "data": self.data,
            "lambda": self.lam, "topk": self.topk,
        }


def fit_ease(X: csr_matrix, vocab: np.ndarray, lam: float, topk: Optional[int],
             G: Optional[np.ndarray] = None) -> EaseModel:
    """Fit EASE on binary interactions ``X`` (users x fit items) restricted to ``vocab``.
    Pass a precomputed Gram matrix ``G`` (over ``vocab``) to reuse it across λ values."""
    if G is None:
        G = gram_matrix(X.tocsc()[:, vocab].tocsr())
    P = solve_ease(G, lam)
    B = prune_topk_columns(P, topk)
    del P
    gc.collect()
    return EaseModel.from_csr(B, vocab, lam, topk, n_items_total=X.shape[1])
