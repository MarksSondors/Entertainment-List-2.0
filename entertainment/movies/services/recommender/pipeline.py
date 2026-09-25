"""One fit path for the ranking models.

``fit_base_models`` is called for the A fit (tuning), the A∪B fit (reported eval)
and the full fit (shipped), so all three get byte-for-byte the same processing:
confidence matrix -> iALS/BPR -> content cold-start head -> content blend.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from scipy.sparse import csr_matrix

from .cold_start import ColdStartHead, content_factors_by_idx, fit_cold_start_head, shrink_blend
from .data_loading import CatalogLookups
from .mf_ranking import RankingModel, build_confidence_matrix, train_ranking_model
from .weights import ConfidenceRecipe

logger = logging.getLogger(__name__)


@dataclass
class RankingParams:
    """Every hyperparameter that shapes the ranking model (and its fold-in)."""
    model_type: str = "ials"
    factors: int = 64
    regularization: float = 0.05
    iterations: int = 20
    alpha: float = 1.0                          # iALS outer alpha, or BPR learning rate
    conf_alpha: float = 40.0
    recency_half_life_days: Optional[float] = None
    blend_k: Optional[float] = 20.0             # content-blend shrinkage; None = no blend
    positive_threshold: float = 3.5
    local_user_weight: float = 3.0
    watchlist_confidence: float = 2.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "RankingParams":
        if not d:
            return cls()
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})

    @classmethod
    def from_bundle(cls, bundle: dict) -> "RankingParams":
        """Hyperparameters a bundle was trained with (v5.1 stores them verbatim;
        older bundles only have the core iALS knobs, the rest were constants)."""
        meta = bundle.get("metadata", {}) or {}
        if meta.get("ranking_params"):
            return cls.from_dict(meta["ranking_params"])
        ranking = bundle.get("ranking", {}) or {}
        return cls(
            model_type=ranking.get("model_type", meta.get("model_type", "ials")),
            factors=int(ranking.get("factors", meta.get("k", 64))),
            regularization=float(ranking.get("regularization", meta.get("regularization", 0.05))),
            iterations=int(ranking.get("iterations", meta.get("iterations", 20))),
            alpha=float(ranking.get("alpha", meta.get("alpha", 1.0))),
            positive_threshold=float(ranking.get("positive_threshold", meta.get("positive_threshold", 3.5))),
        )

    def recipe(self, reference_ts: Optional[float] = None) -> ConfidenceRecipe:
        return ConfidenceRecipe(
            positive_threshold=self.positive_threshold,
            conf_alpha=self.conf_alpha,
            outer_alpha=self.alpha if self.model_type == "ials" else 1.0,
            local_user_weight=self.local_user_weight,
            watchlist_confidence=self.watchlist_confidence,
            regularization=self.regularization,
            recency_half_life_days=self.recency_half_life_days,
            reference_ts=reference_ts,
        )

    def fit_key(self) -> str:
        """Hash of the params that change the fitted factors (blend_k is post-hoc)."""
        d = self.to_dict()
        d.pop("blend_k", None)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:12]


@dataclass
class BaseModels:
    ranking: RankingModel                       # item_factors already content-blended per params.blend_k
    raw_item_factors: np.ndarray                # pure collaborative item factors
    content_item_factors: Optional[np.ndarray]  # cold-start head prediction per item (None without head)
    item_counts: np.ndarray                     # interactions per item in the fit (nnz per column)
    cold: Optional[ColdStartHead]
    params: RankingParams
    recipe: ConfidenceRecipe
    extras: dict = field(default_factory=dict)

    @property
    def idx_to_item(self) -> np.ndarray:
        out = np.empty(len(self.ranking.item_to_idx), dtype=np.int64)
        for tmdb_id, idx in self.ranking.item_to_idx.items():
            out[idx] = tmdb_id
        return out

    def item_factors_for_blend(self, blend_k: Optional[float]) -> np.ndarray:
        if self.content_item_factors is None:
            return self.raw_item_factors
        return shrink_blend(self.raw_item_factors, self.content_item_factors, self.item_counts, blend_k)

    def set_blend(self, blend_k: Optional[float]) -> None:
        self.params.blend_k = blend_k
        self.ranking.item_factors = self.item_factors_for_blend(blend_k)


def fit_base_models(
    df_fit: pd.DataFrame,
    catalog: CatalogLookups,
    params: RankingParams,
    *,
    watchlist_df: Optional[pd.DataFrame] = None,
    use_gpu: bool = False,
    cold_start: bool = True,
    random_state: int = 42,
) -> BaseModels:
    """Fit the ranking model + content head on ``df_fit`` and apply the content blend."""
    reference_ts = float(df_fit["timestamp"].max()) if "timestamp" in df_fit.columns and len(df_fit) else None
    recipe = params.recipe(reference_ts)
    R, user_to_idx, item_to_idx = build_confidence_matrix(df_fit, recipe=recipe, watchlist_df=watchlist_df)
    ranking = train_ranking_model(
        params.model_type, R, user_to_idx, item_to_idx,
        factors=int(params.factors), regularization=float(params.regularization),
        iterations=int(params.iterations), alpha=float(params.alpha),
        use_gpu=use_gpu, positive_threshold=params.positive_threshold,
        random_state=random_state,
    )
    item_counts = np.diff(R.tocsc().indptr).astype(np.int32)
    del R

    raw = ranking.item_factors
    cold = None
    content = None
    if cold_start and catalog is not None and catalog.tmdb_to_year:
        cold = fit_cold_start_head(raw, item_to_idx, catalog, ridge_lambda=5.0)
        content = content_factors_by_idx(cold, item_to_idx, catalog)

    models = BaseModels(
        ranking=ranking, raw_item_factors=raw, content_item_factors=content,
        item_counts=item_counts, cold=cold, params=params, recipe=recipe,
    )
    models.set_blend(params.blend_k)
    return models


def positives_csr(df_fit: pd.DataFrame, models: BaseModels) -> csr_matrix:
    """Binary (users x items) matrix of rated positives in ``models``' index space —
    the EASE input (watchlist excluded: MovieLens users have none, so including it would
    skew training vs serving)."""
    from .evaluation import build_train_csr
    pos = df_fit[df_fit["rating"] >= models.params.positive_threshold]
    X = build_train_csr(pos, models.ranking.user_to_idx, models.ranking.item_to_idx)
    X.data[:] = 1.0
    return X


def fit_ease_for(
    models: BaseModels,
    X_pos,
    *,
    lam: float,
    topk: Optional[int],
    n_vocab: int = 20_000,
    min_count: int = 20,
    G: Optional[np.ndarray] = None,
    vocab: Optional[np.ndarray] = None,
):
    """Fit sparse EASE over the ``n_vocab`` most-interacted items of ``models``' fit
    (shrunk automatically if RAM is short) and attach it as ``models.extras['ease']``."""
    import psutil

    from .ease import choose_vocab_size, fit_ease, gram_matrix, select_ease_vocab

    if vocab is None:
        n = choose_vocab_size(n_vocab, psutil.virtual_memory().available)
        vocab = select_ease_vocab(models.item_counts, n, min_count=min_count)
    if G is None:
        G = gram_matrix(X_pos.tocsc()[:, vocab].tocsr())
    ease = fit_ease(X_pos, vocab, lam, topk, G=G)
    covered = float(X_pos.tocsc()[:, vocab].nnz) / max(X_pos.nnz, 1)
    logger.info("EASE: %d-item vocab covers %.1f%% of positives (lambda=%.0f, topk=%s, nnz(B)=%d)",
                len(vocab), 100 * covered, lam, topk, len(ease.data))
    models.extras["ease"] = ease
    return ease
