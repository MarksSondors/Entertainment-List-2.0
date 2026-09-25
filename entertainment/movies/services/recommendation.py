"""Inference-time movie recommender.

Loads a v5.0 bundle (or a legacy v4 pickle for backward compat) and a separate
overlay pickle for per-user fold-in updates. CPU-only — never imports CuPy or
``implicit.gpu``.

Scoring:
- ``predict_rating`` / ``_predict_ratings`` return the 0-5 displayed rating from the
  bias hierarchy plus a small learned-weight factor term (``explicit_blend_alpha``).
- Ranking uses the iALS dot product (cold-start ridge head for unseen items), then
  the bundle's tuned serving transform (``metadata["serving"]``: popularity penalty
  + MMR) via ``recommender.scoring`` - the same code the offline evaluation runs.
"""
from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count

from custom_auth.models import Review
from movies.models import Movie
from movies.services.recommender.cold_start import (
    ColdStartHead,
    UserColdStartHead,
    predict_factors as cold_start_predict_factors,
    predict_user_factor,
)
from movies.services.recommender.data_loading import TMDB_GENRES, CatalogLookups
from movies.services.recommender.ease import EaseModel
from movies.services.recommender.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    ItemFeatureTable,
    compute_features,
    generate_candidates,
    user_context,
)
from movies.services.recommender.scoring import LEGACY_SERVING, ItemArrays, ServingParams, rank_row

User = get_user_model()
logger = logging.getLogger(__name__)


_OVERLAY_RELOAD_INTERVAL_SECONDS = 300  # 5 min TTL for picking up fold-in updates


@dataclass
class _CandidateScope:
    """Per-item arrays for one candidate list (see ``MovieRecommender._get_scope``)."""
    key: int
    tmdb_ids: np.ndarray
    item_idx: np.ndarray       # (m,) index into the trained item space, -1 for untrained items
    factors: np.ndarray        # (m, k) ranking factors; cold-start head rows for unseen items
    has_factor: np.ndarray     # (m,) bool
    items: ItemArrays          # popularity / genre / language / runtime / decade for serving transforms
    item_bias: np.ndarray      # (m,) item bias, cold estimate where untrained
    years: list
    languages: list
    runtimes: list


class MovieRecommender:
    """Loads the trained model + overlay; serves predictions and recommendations."""

    def __init__(self, bundle: Optional[dict] = None):
        """Loads svd_model_latest.pkl, or uses ``bundle`` (an in-memory model dict) if given."""
        self._movie_content_type: Optional[ContentType] = None
        self.model_data: Optional[dict] = None
        self.known_tmdb_ids: set[int] = set()
        self._genre_combo_avg_bias: dict = {}

        # v5.0 sections
        self.cold_start_head: Optional[ColdStartHead] = None
        self.user_cold_start_head: Optional[UserColdStartHead] = None
        self.catalog: CatalogLookups = CatalogLookups()
        self.user_time_trend: dict = {}
        self.user_time_norm: dict = {}
        self.explicit_blend_alpha: float = 0.0
        self.serving: ServingParams = LEGACY_SERVING
        self._pop_stats: Optional[tuple[float, float]] = None
        self._scopes: dict[str, "_CandidateScope"] = {}
        self._cold_factor_cache: dict[int, Optional[np.ndarray]] = {}
        self.ease: Optional[EaseModel] = None
        self.positive_threshold: float = 3.5
        self.reranker: Optional[dict] = None
        self._feature_table: Optional[ItemFeatureTable] = None
        self._disabled_tiers: set[str] = set()

        # Overlay state
        self._overlay: dict = {}
        self._overlay_mtime: float = 0.0
        self._overlay_loaded_at: float = 0.0

        if bundle is not None:
            self.model_data = bundle
            self._init_from_bundle()
        else:
            self._load_model()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def movie_content_type(self) -> ContentType:
        if self._movie_content_type is None:
            self._movie_content_type = ContentType.objects.get_for_model(Movie)
        return self._movie_content_type

    def _load_model(self) -> None:
        try:
            d = os.path.join(settings.BASE_DIR, "movies", "ml_models")
            for name in ("svd_model_latest.pkl", "svd_model.pkl"):
                p = os.path.join(d, name)
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        self.model_data = pickle.load(f)
                    break
            if self.model_data:
                self._init_from_bundle()
        except Exception:
            logger.exception("Failed to load recommender model")

    def _init_from_bundle(self) -> None:
        try:
            data = self.model_data
            ranking = data.get("ranking", {})
            biases = data.get("biases", {})
            catalog_dict = data.get("catalog", {})

            # Ranking factors / index maps (prefer v5.0 'ranking' section, fall back to legacy flat keys)
            self.user_to_idx = ranking.get("user_to_idx") or data.get("user_to_idx", {})
            self.item_to_idx = ranking.get("item_to_idx") or data.get("item_to_idx", {})
            self.user_factors = ranking.get("user_factors")
            if self.user_factors is None:
                self.user_factors = data.get("user_factors")
            self.item_factors = ranking.get("item_factors")
            if self.item_factors is None:
                self.item_factors = data.get("item_factors")

            # Legacy SVD path
            self.U = data.get("U")
            self.Sigma = data.get("Sigma")
            self.Vt = data.get("Vt")

            metadata = data.get("metadata", {})
            self.model_type = metadata.get("model_type", "svd")
            self.model_version = data.get("model_version") or metadata.get("model_version", "<legacy>")

            # Biases (prefer 'biases' subdict, fall back to flat legacy keys)
            self.global_mean = float(biases.get("global_mean", data.get("global_mean", 3.5)))
            self.year_biases = biases.get("year_biases") or data.get("year_biases", {})
            self.item_biases = biases.get("item_biases") or data.get("item_biases", {})
            self.user_biases = biases.get("user_biases") or data.get("user_biases", {})
            self.user_genre_biases = biases.get("user_genre_biases") or data.get("user_genre_biases", {})
            self.user_decade_biases = biases.get("user_decade_biases") or data.get("user_decade_biases", {})
            self.user_language_biases = biases.get("user_language_biases") or data.get("user_language_biases", {})
            self.user_runtime_biases = biases.get("user_runtime_biases") or data.get("user_runtime_biases", {})
            self.user_time_trend = biases.get("user_time_trend", {})
            self.user_time_norm = biases.get("user_time_norm", {})

            # Catalog lookups
            self.catalog.tmdb_to_genres = catalog_dict.get("tmdb_to_genres") or data.get("tmdb_to_genres", {})
            self.catalog.tmdb_to_language = catalog_dict.get("tmdb_to_language") or data.get("tmdb_to_language", {})
            self.catalog.tmdb_to_runtime_bucket = catalog_dict.get("tmdb_to_runtime_bucket") or data.get("tmdb_to_runtime_bucket", {})
            self.catalog.tmdb_to_year = catalog_dict.get("tmdb_to_year") or data.get("tmdb_id_to_year", {})
            self.catalog.tmdb_vote_data = catalog_dict.get("tmdb_vote_data") or data.get("tmdb_vote_data", {})
            self.catalog.tmdb_to_director = catalog_dict.get("tmdb_to_director", {})
            self.catalog.tmdb_to_top_cast = catalog_dict.get("tmdb_to_top_cast", {})

            # Aliases preserved for templates / call sites that still read these names
            self.tmdb_to_genres = self.catalog.tmdb_to_genres
            self.tmdb_to_language = self.catalog.tmdb_to_language
            self.tmdb_to_runtime_bucket = self.catalog.tmdb_to_runtime_bucket
            self.tmdb_id_to_year = self.catalog.tmdb_to_year
            self.tmdb_vote_data = self.catalog.tmdb_vote_data
            self.genre_mapping = data.get("genre_mapping", {})  # empty in v5 (= identity)

            self.known_tmdb_ids = set(data.get("known_tmdb_ids") or list(self.item_to_idx.keys()))
            self.explicit_blend_alpha = float(metadata.get("explicit_blend_alpha", 0.0))
            # Tuned serving transform; bundles from before v5.1 get the transform they were served with.
            self.serving = ServingParams.from_dict(metadata["serving"]) if metadata.get("serving") else LEGACY_SERVING
            self._pop_stats = ItemArrays.log_votes_stats(self.item_to_idx.keys(), self.catalog)
            self.positive_threshold = float(ranking.get("positive_threshold", metadata.get("positive_threshold", 3.5)))
            self.ease = self._load_ease(data.get("ease"))
            self.reranker = self._load_reranker(data.get("reranker"))

            # Cold-start head
            cold = data.get("cold_start")
            if cold is not None:
                self.cold_start_head = ColdStartHead(
                    coef=np.asarray(cold["coef"], dtype=np.float32),
                    intercept=np.asarray(cold["intercept"], dtype=np.float32),
                    decades=list(cold["decades"]),
                    languages=list(cold["languages"]),
                    feature_dim=int(cold["feature_dim"]),
                    directors=list(cold.get("directors", [])),
                    top_cast=list(cold.get("top_cast", [])),
                )

            # Symmetric user cold-start head (item 2): lets brand-new users with a
            # handful of ratings get a real factor instead of a popularity fallback.
            user_cold = data.get("user_cold_start")
            if user_cold is not None:
                self.user_cold_start_head = UserColdStartHead(
                    coef=np.asarray(user_cold["coef"], dtype=np.float32),
                    intercept=np.asarray(user_cold["intercept"], dtype=np.float32),
                    feature_dim=int(user_cold["feature_dim"]),
                )

            self._build_cold_item_lookup()
            self._maybe_reload_overlay(force=True)

            if metadata:
                logger.info(
                    "Loaded recommender v%s (%s, %d items, %d users%s)",
                    self.model_version, self.model_type,
                    metadata.get("n_items", len(self.item_to_idx)),
                    metadata.get("n_local_users", 0),
                    ", IPS" if metadata.get("ips_debiasing") else "",
                )
        except Exception:
            logger.exception("Failed to load recommender model")

    def _load_ease(self, section: Optional[dict]) -> Optional[EaseModel]:
        """Re-index the bundle's EASE vocabulary (stored as TMDB ids) into this model's
        item space. Any mismatch disables EASE and serving falls back to plain iALS."""
        if not section or self.item_factors is None:
            return None
        try:
            vocab = np.array([self.item_to_idx.get(int(t), -1) for t in section["item_ids"]], dtype=np.int64)
            if (vocab < 0).any():
                logger.warning("EASE vocabulary has %d items unknown to the ranking model; EASE disabled",
                               int((vocab < 0).sum()))
                return None
            return EaseModel(vocab=vocab, indptr=np.asarray(section["indptr"]),
                             indices=np.asarray(section["indices"]), data=np.asarray(section["data"]),
                             lam=float(section.get("lambda", 0.0)), topk=section.get("topk"),
                             n_items_total=len(self.item_to_idx))
        except Exception:
            logger.exception("Failed to load the EASE section; serving without it")
            return None

    @staticmethod
    def _load_reranker(section: Optional[dict]) -> Optional[dict]:
        """Accept the reranker only if it was trained on exactly this code's feature layout."""
        if not section:
            return None
        if (section.get("format") != "lgbm_numpy_v1"
                or section.get("feature_schema_version") != FEATURE_SCHEMA_VERSION
                or list(section.get("feature_names") or []) != list(FEATURE_NAMES)):
            logger.warning("Reranker feature schema mismatch; serving without the reranker")
            return None
        return section

    def _get_feature_table(self) -> ItemFeatureTable:
        """Reranker item features, built lazily (one pass over the trained catalog)."""
        if self._feature_table is None:
            from movies.services.recommender.evaluation import popularity_percentile

            idx_to_item = np.empty(len(self.item_to_idx), dtype=np.int64)
            for t, i in self.item_to_idx.items():
                idx_to_item[i] = t
            counts = (self.model_data.get("item_stats") or {}).get("interaction_counts")
            if counts is None:
                counts = np.zeros(len(idx_to_item))
            biases = {
                "global_mean": self.global_mean, "year_biases": self.year_biases, "item_biases": self.item_biases,
                "user_decade_biases": self.user_decade_biases, "user_language_biases": self.user_language_biases,
            }
            self._feature_table = ItemFeatureTable.build(idx_to_item, self.catalog, biases,
                                                         popularity_percentile(np.asarray(counts)), self.item_factors)
        return self._feature_table

    # ------------------------------------------------------------------
    # Overlay
    # ------------------------------------------------------------------

    def _overlay_path(self) -> str:
        return os.path.join(settings.BASE_DIR, "movies", "ml_models", "svd_overlay_latest.pkl")

    def _maybe_reload_overlay(self, *, force: bool = False) -> None:
        """TTL-driven overlay reload. Cheap stat() call; only re-reads on mtime change."""
        path = self._overlay_path()
        if not os.path.exists(path):
            self._overlay = {}
            self._overlay_mtime = 0.0
            return
        now = time.monotonic()
        if not force and now - self._overlay_loaded_at < _OVERLAY_RELOAD_INTERVAL_SECONDS:
            return
        try:
            mtime = os.path.getmtime(path)
            if mtime == self._overlay_mtime and not force:
                self._overlay_loaded_at = now
                return
            with open(path, "rb") as f:
                overlay = pickle.load(f)
            metadata = self.model_data.get("metadata", {}) if self.model_data else {}
            base_at = metadata.get("trained_at")
            if isinstance(overlay, dict) and overlay.get("base_trained_at") == base_at:
                self._overlay = overlay
            else:
                # Stale overlay (different base) — ignore
                self._overlay = {}
            self._overlay_mtime = mtime
            self._overlay_loaded_at = now
        except (pickle.PickleError, EOFError, OSError):
            logger.warning("Overlay reload failed; ignoring")
            self._overlay = {}

    def _ov_user_bias(self, user_id: str) -> Optional[float]:
        return (self._overlay.get("user_biases") or {}).get(user_id)

    def _ov_category_bias(self, kind: str, key, user_id: str) -> Optional[float]:
        block = (self._overlay.get(kind) or {}).get(key)
        if not block:
            return None
        return block.get(user_id)

    def _ov_user_factor(self, user_id: str) -> Optional[np.ndarray]:
        return (self._overlay.get("ranking_user_factors") or {}).get(user_id)

    # ------------------------------------------------------------------
    # Cold-item helpers (legacy fallback when there is no cold-start head)
    # ------------------------------------------------------------------

    def _build_cold_item_lookup(self) -> None:
        from collections import defaultdict
        if not self.item_biases or not self.tmdb_to_genres:
            self._genre_combo_avg_bias = {}
            return
        combo_biases: dict[frozenset, list[float]] = defaultdict(list)
        for tmdb_id, genres in self.tmdb_to_genres.items():
            if tmdb_id not in self.item_biases:
                continue
            gs = frozenset(genres or [])
            if gs:
                combo_biases[gs].append(float(self.item_biases[tmdb_id]))
        self._genre_combo_avg_bias = {
            gs: float(np.mean(vals)) for gs, vals in combo_biases.items() if vals
        }

    def _estimate_cold_item_bias(self, tmdb_id_int: int) -> float:
        """Bayesian popularity prior + genre-combo averaging. Used only when item_bias is missing."""
        popularity_bias = 0.0
        vote = self.tmdb_vote_data.get(tmdb_id_int) if self.tmdb_vote_data else None
        if vote:
            vote_avg, vote_count = vote
            if vote_count > 0 and vote_avg > 0:
                C = 300
                bayes = (C * self.global_mean + vote_count * (vote_avg / 2.0)) / (C + vote_count)
                popularity_bias = bayes - self.global_mean

        genre_bias = 0.0
        gs = self.tmdb_to_genres.get(tmdb_id_int) or []
        if gs:
            target = frozenset(gs)
            if target in self._genre_combo_avg_bias:
                genre_bias = self._genre_combo_avg_bias[target]
            else:
                matches = [b for combo, b in self._genre_combo_avg_bias.items()
                           if (len(target & combo) / max(len(target | combo), 1)) >= 0.5]
                if matches:
                    genre_bias = float(np.mean(matches))

        if vote and vote[1] > 50:
            return 0.6 * popularity_bias + 0.4 * genre_bias
        return 0.3 * popularity_bias + 0.7 * genre_bias

    # ------------------------------------------------------------------
    # Rating prediction (UI display)
    # ------------------------------------------------------------------

    def predict_rating(
        self, user_id_str: str, tmdb_id_int: int, year: Optional[int] = None,
        user_factor_override: Optional[np.ndarray] = None,
    ) -> float:
        """Return a 0-5 explicit-rating estimate from the bias hierarchy.

        Adds two small optional terms on top of the bias hierarchy: a per-user
        linear time-drift term (``user_time_trend``/``user_time_norm``) and a
        learned-weight iALS factor dot product (``explicit_blend_alpha``, fit by
        ``evaluation.fit_explicit_blend_weight`` at training time so it stays on
        a comparable scale to the bias terms instead of distorting the display).
        ``user_factor_override`` lets ephemeral cold-start user factors (item 2)
        be scored without needing an entry in ``user_to_idx``.
        """
        if not self.model_data:
            return 0.0
        self._maybe_reload_overlay()

        # Item bias (with cold fallback)
        b_i = self.item_biases.get(tmdb_id_int)
        if b_i is None:
            b_i = self._estimate_cold_item_bias(int(tmdb_id_int))

        b_u = self._ov_user_bias(user_id_str)
        if b_u is None:
            b_u = self.user_biases.get(user_id_str, 0.0)

        b_y = 0.0
        b_dec = 0.0
        if year is None:
            year = self.tmdb_id_to_year.get(tmdb_id_int)
        if year is not None:
            b_y = self.year_biases.get(int(year), 0.0)
            decade = (int(year) // 10) * 10
            ov = self._ov_category_bias("user_decade_biases", decade, user_id_str)
            if ov is not None:
                b_dec = ov
            else:
                b_dec = self.user_decade_biases.get(decade, {}).get(user_id_str, 0.0)

        # Genre (multi-hot sum) — overlay overrides per-(genre, user)
        b_g = 0.0
        for g_raw in self.tmdb_to_genres.get(tmdb_id_int, []) or []:
            mapped = self.genre_mapping.get(g_raw, g_raw) if self.genre_mapping else g_raw
            if not mapped:
                continue
            ov = self._ov_category_bias("user_genre_biases", mapped, user_id_str)
            if ov is not None:
                b_g += ov
            elif mapped in self.user_genre_biases:
                b_g += self.user_genre_biases[mapped].get(user_id_str, 0.0)

        # Language
        b_lang = 0.0
        if self.user_language_biases:
            lang = self.tmdb_to_language.get(tmdb_id_int, "en") if self.tmdb_to_language else "en"
            ov = self._ov_category_bias("user_language_biases", lang, user_id_str)
            b_lang = ov if ov is not None else self.user_language_biases.get(lang, {}).get(user_id_str, 0.0)

        # Runtime
        b_rt = 0.0
        if self.user_runtime_biases:
            rt = self.tmdb_to_runtime_bucket.get(tmdb_id_int, "standard") if self.tmdb_to_runtime_bucket else "standard"
            ov = self._ov_category_bias("user_runtime_biases", rt, user_id_str)
            b_rt = ov if ov is not None else self.user_runtime_biases.get(rt, {}).get(user_id_str, 0.0)

        # Per-user linear time-drift (see biases.compute_user_time_trend_biases). Projects
        # "now" onto the user's own [t_min, t_max] rating window the trend was fit on;
        # extrapolation is clamped to [-1.0, 1.0] since the ridge-shrunk slope is only
        # reliable near the observed range.
        b_time = 0.0
        bounds = self.user_time_norm.get(user_id_str)
        slope = self.user_time_trend.get(user_id_str)
        if bounds is not None and slope is not None:
            t_min, t_max = bounds
            span = (t_max - t_min) or 1.0
            t_norm = np.clip((time.time() - t_min) / span - 0.5, -1.0, 1.0)
            b_time = float(slope) * float(t_norm)

        # Learned-weight iALS factor dot product (see fit_explicit_blend_weight).
        b_factor = 0.0
        if self.explicit_blend_alpha:
            u = self._user_factor(user_id_str, override=user_factor_override)
            v = self._item_factor(tmdb_id_int)
            if u is not None and v is not None:
                b_factor = self.explicit_blend_alpha * float(np.dot(u, v))

        return self.global_mean + b_i + b_u + b_y + b_g + b_dec + b_lang + b_rt + b_time + b_factor

    # ------------------------------------------------------------------
    # Ranking score (iALS dot product, with cold-start fallback)
    # ------------------------------------------------------------------

    def _user_factor(self, user_id_str: str, override: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
        if override is not None:
            return override
        ov = self._ov_user_factor(user_id_str)
        if ov is not None:
            return np.asarray(ov, dtype=np.float32)
        idx = self.user_to_idx.get(user_id_str)
        if idx is not None and self.user_factors is not None:
            return np.asarray(self.user_factors[idx], dtype=np.float32)
        return None

    def _item_factor(self, tmdb_id: int) -> Optional[np.ndarray]:
        idx = self.item_to_idx.get(tmdb_id)
        if idx is not None and self.item_factors is not None:
            return np.asarray(self.item_factors[idx], dtype=np.float32)
        if self.cold_start_head is not None and self.catalog.tmdb_to_genres:
            try:
                vec = cold_start_predict_factors(self.cold_start_head, [int(tmdb_id)], self.catalog)
                return vec[0]
            except Exception:
                return None
        return None

    def _get_cold_start_user_factor(self, user_id, min_ratings: int = 1) -> Optional[np.ndarray]:
        """Symmetric user cold-start (item 2): predict an iALS-shaped factor for a
        user who isn't in ``user_to_idx``/the overlay yet, from their existing
        (local) ``Review`` ratings, so brand-new users get real personalization
        instead of an immediate popularity-only fallback.
        """
        if self.user_cold_start_head is None or self.cold_start_head is None:
            return None
        try:
            rows = list(
                Review.objects.filter(
                    user_id=user_id, content_type=self.movie_content_type, rating__gte=3.0,
                ).values_list("object_id", "rating")
            )
            if len(rows) < min_ratings:
                return None
            movie_ids = [mid for mid, _ in rows]
            tmdb_by_movie_id = dict(
                Movie.objects.filter(id__in=movie_ids, tmdb_id__isnull=False)
                .values_list("id", "tmdb_id")
            )
            rated_tmdb_ids, weights = [], []
            for mid, rating in rows:
                tid = tmdb_by_movie_id.get(mid)
                if tid is not None:
                    rated_tmdb_ids.append(int(tid))
                    weights.append(max(float(rating), 0.1))
            if not rated_tmdb_ids:
                return None
            return predict_user_factor(
                self.user_cold_start_head, rated_tmdb_ids, weights, self.catalog, self.cold_start_head,
            )
        except Exception:
            logger.exception("Cold-start user factor prediction failed for user_id=%s", user_id)
            return None

    # ------------------------------------------------------------------
    # Vectorized candidate scoring
    # ------------------------------------------------------------------

    def _get_scope(self, name: str, tmdb_ids: list[int], years: Optional[list] = None) -> "_CandidateScope":
        """Per-item arrays for a candidate list, cached until the list changes (the
        local catalog / external candidate set change rarely, per-user exclusions are
        applied as masks on top)."""
        key = hash((tuple(tmdb_ids), tuple(years) if years is not None else None))
        scope = self._scopes.get(name)
        if scope is not None and scope.key == key:
            return scope

        m = len(tmdb_ids)
        k = self.item_factors.shape[1] if self.item_factors is not None else 0
        factors = np.zeros((m, k), dtype=np.float32)
        has_factor = np.zeros(m, dtype=bool)
        cold_rows: list[int] = []
        for row, tid in enumerate(tmdb_ids):
            idx = self.item_to_idx.get(tid)
            if idx is not None and self.item_factors is not None:
                factors[row] = self.item_factors[idx]
                has_factor[row] = True
            else:
                cold_rows.append(row)
        if cold_rows and self.cold_start_head is not None and self.catalog.tmdb_to_genres:
            todo = [tmdb_ids[r] for r in cold_rows if tmdb_ids[r] not in self._cold_factor_cache]
            if todo:
                try:
                    vecs = cold_start_predict_factors(self.cold_start_head, todo, self.catalog)
                    self._cold_factor_cache.update({t: np.asarray(v, dtype=np.float32) for t, v in zip(todo, vecs)})
                except Exception:
                    logger.exception("Batched cold-start item factor prediction failed")
                    self._cold_factor_cache.update({t: None for t in todo})
            for r in cold_rows:
                vec = self._cold_factor_cache.get(tmdb_ids[r])
                if vec is not None:
                    factors[r] = vec
                    has_factor[r] = True

        if years is None:
            years = [None] * m
        years = [y if y is not None else self.tmdb_id_to_year.get(t) for t, y in zip(tmdb_ids, years)]
        item_bias = np.array([
            self.item_biases[t] if t in self.item_biases else self._estimate_cold_item_bias(int(t))
            for t in tmdb_ids
        ], dtype=np.float64)
        scope = _CandidateScope(
            key=key,
            tmdb_ids=np.asarray(tmdb_ids, dtype=np.int64),
            item_idx=np.array([self.item_to_idx.get(t, -1) for t in tmdb_ids], dtype=np.int64),
            factors=factors,
            has_factor=has_factor,
            items=ItemArrays.build(tmdb_ids, self.catalog, log_votes_stats=self._pop_stats),
            item_bias=item_bias,
            years=years,
            languages=[self.tmdb_to_language.get(t, "en") if self.tmdb_to_language else "en" for t in tmdb_ids],
            runtimes=[self.tmdb_to_runtime_bucket.get(t, "standard") if self.tmdb_to_runtime_bucket else "standard"
                      for t in tmdb_ids],
        )
        self._scopes[name] = scope
        return scope

    def _user_category_bias(self, kind: str, key, user_id_str: str) -> float:
        """Overlay-first per-user category bias, exactly as ``predict_rating`` reads it."""
        ov = self._ov_category_bias(kind, key, user_id_str)
        if ov is not None:
            return float(ov)
        return float((getattr(self, kind, None) or {}).get(key, {}).get(user_id_str, 0.0))

    def _predict_ratings(self, user_id_str: str, scope: "_CandidateScope",
                         user_factor: Optional[np.ndarray]) -> np.ndarray:
        """Vectorized ``predict_rating`` for every item in ``scope`` (same terms, same
        overlay precedence; see the parity test in movies/tests)."""
        self._maybe_reload_overlay()
        m = len(scope.tmdb_ids)
        b_u = self._ov_user_bias(user_id_str)
        if b_u is None:
            b_u = self.user_biases.get(user_id_str, 0.0)
        est = np.full(m, self.global_mean + float(b_u), dtype=np.float64) + scope.item_bias

        # Year + decade (both zero when the year is unknown)
        year_term: dict = {}
        for y in set(scope.years):
            if y is None:
                year_term[None] = 0.0
            else:
                decade = (int(y) // 10) * 10
                year_term[y] = (float(self.year_biases.get(int(y), 0.0))
                                + self._user_category_bias("user_decade_biases", decade, user_id_str))
        est += np.array([year_term[y] for y in scope.years], dtype=np.float64)

        # Genre (multi-hot sum over the canonical TMDB genres)
        genre_vec = np.array([self._user_category_bias("user_genre_biases", g, user_id_str) for g in TMDB_GENRES])
        est += scope.items.genre_mat.astype(np.float64) @ genre_vec

        if self.user_language_biases:
            lang_term = {l: self._user_category_bias("user_language_biases", l, user_id_str)
                         for l in set(scope.languages)}
            est += np.array([lang_term[l] for l in scope.languages], dtype=np.float64)
        if self.user_runtime_biases:
            rt_term = {r: self._user_category_bias("user_runtime_biases", r, user_id_str)
                       for r in set(scope.runtimes)}
            est += np.array([rt_term[r] for r in scope.runtimes], dtype=np.float64)

        bounds = self.user_time_norm.get(user_id_str)
        slope = self.user_time_trend.get(user_id_str)
        if bounds is not None and slope is not None:
            t_min, t_max = bounds
            span = (t_max - t_min) or 1.0
            est += float(slope) * float(np.clip((time.time() - t_min) / span - 0.5, -1.0, 1.0))

        if self.explicit_blend_alpha and user_factor is not None:
            est += np.where(scope.has_factor, self.explicit_blend_alpha * (scope.factors @ user_factor), 0.0)
        return est

    def _user_positive_item_idx(self, user_id) -> np.ndarray:
        """Trained-item indices of the user's positive reviews (EASE input), read live from
        the DB so new ratings count immediately. Local reviews are on a 0-10 scale."""
        rows = Review.objects.filter(
            user_id=user_id, content_type=self.movie_content_type, rating__gte=self.positive_threshold * 2,
        ).values_list("object_id", flat=True)
        tmdb_ids = Movie.objects.filter(id__in=list(rows), tmdb_id__isnull=False).values_list("tmdb_id", flat=True)
        idx = [self.item_to_idx.get(int(t)) for t in tmdb_ids]
        return np.array([i for i in idx if i is not None], dtype=np.int64)

    def _ranking_scores(self, user_id, user_factor: np.ndarray, scope: "_CandidateScope",
                        excluded_rows: Optional[np.ndarray] = None
                        ) -> tuple[np.ndarray, Optional[tuple[float, float]]]:
        """Scores for ``scope`` from the best available tier: reranker -> blend -> iALS.

        A tier that raises is logged once and skipped for the rest of the process.
        ``excluded_rows`` (bool over scope rows) marks items that can't be served.
        """
        if self.reranker is not None and "reranker" not in self._disabled_tiers:
            try:
                return self._reranker_scores(user_id, user_factor, scope, excluded_rows)
            except Exception:
                logger.exception("Reranker scoring failed; falling back to the blend/iALS tier")
                self._disabled_tiers.add("reranker")
        if "blend" not in self._disabled_tiers:
            try:
                return self._blend_scores(user_id, user_factor, scope)
            except Exception:
                logger.exception("Blend scoring failed; falling back to plain iALS")
                self._disabled_tiers.add("blend")
        raw = np.where(scope.has_factor, scope.factors @ user_factor, -np.inf).astype(np.float32)
        return raw, None

    def _user_history(self, user_id) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(ratings on the 0-5 scale, trained item index or -1, timestamps) of the user's reviews."""
        rows = list(Review.objects.filter(user_id=user_id, content_type=self.movie_content_type)
                    .values_list("object_id", "rating", "date_added"))
        tmdb_by_movie = dict(Movie.objects.filter(id__in=[r[0] for r in rows], tmdb_id__isnull=False)
                             .values_list("id", "tmdb_id"))
        ratings, items, ts = [], [], []
        for movie_id, rating, date_added in rows:
            tid = tmdb_by_movie.get(movie_id)
            ratings.append(float(rating) / 2.0)
            items.append(self.item_to_idx.get(int(tid), -1) if tid is not None else -1)
            ts.append(date_added.timestamp() if date_added else time.time())
        return np.array(ratings), np.array(items, dtype=np.int64), np.array(ts)

    def _reranker_scores(self, user_id, user_factor: np.ndarray, scope: "_CandidateScope",
                         excluded_rows: Optional[np.ndarray]) -> tuple[np.ndarray, None]:
        """LambdaRank scores (numpy trees) for the reranker's candidates within ``scope``;
        everything else in scope gets -inf. Mirrors ``reranker.RerankFeatureBuilder``."""
        from movies.services.recommender.reranker import predict_numpy_trees

        user_id_str = f"loc_{user_id}"
        table = self._get_feature_table()
        ratings, items, ts = self._user_history(user_id)
        b_u = self._ov_user_bias(user_id_str)
        ctx = user_context(
            user_factor, table, ratings=ratings, item_idx=items, timestamps=ts, query_ts=time.time(),
            positive_threshold=self.positive_threshold,
            bias_lookup=lambda kind, key: self._user_category_bias(kind, key, user_id_str),
            user_bias=float(b_u if b_u is not None else self.user_biases.get(user_id_str, 0.0)),
        )
        ials_full = self.item_factors @ user_factor
        ease_full = None
        if self.ease is not None:
            pos = items[(ratings >= self.positive_threshold) & (items >= 0)]
            ease_full = self.ease.score_one(pos)

        # Only items that are in scope, trained and servable may become candidates.
        servable = np.zeros(len(ials_full), dtype=bool)
        ok = scope.item_idx >= 0
        if excluded_rows is not None:
            ok &= ~excluded_rows
        servable[scope.item_idx[ok]] = True
        servable[items[items >= 0]] = False
        counts = (self.model_data.get("item_stats") or {}).get("interaction_counts")
        pop_scores = np.asarray(counts, dtype=np.float64) if counts is not None else np.zeros(len(ials_full))
        k = self.reranker.get("candidate_k") or {}
        cand, src = generate_candidates(ials_full, ease_full, pop_scores, ~servable,
                                        k_ials=int(k.get("ials", 200)), k_ease=int(k.get("ease", 200)),
                                        k_pop=int(k.get("pop", 50)))
        cand_scores = np.full(len(ials_full), -np.inf, dtype=np.float32)
        if len(cand):
            cand_scores[cand] = predict_numpy_trees(self.reranker["trees"],
                                                    compute_features(ctx, cand, src, table, ials_full, ease_full))
        scores = np.full(len(scope.item_idx), -np.inf, dtype=np.float32)
        scores[ok] = cand_scores[scope.item_idx[ok]]
        return scores, None

    def _blend_scores(self, user_id, user_factor: np.ndarray,
                      scope: "_CandidateScope") -> tuple[np.ndarray, Optional[tuple[float, float]]]:
        """(scores for ``scope``, their mean/std over the full trained catalog).

        Plain iALS dot products, or - when the bundle ships EASE with ``ease_beta`` > 0 -
        z(iALS) + ease_beta * z(EASE) exactly like ``evaluation.blend_scorer``. The full-
        catalog stats are the z-scale the popularity penalty was tuned on offline
        (a candidate subset would distort it).
        """
        raw = np.where(scope.has_factor, scope.factors @ user_factor, -np.inf).astype(np.float32)
        if self.item_factors is None:
            return raw, None
        full = (self.item_factors @ user_factor).astype(np.float64)
        beta = float(self.serving.ease_beta or 0.0)
        if not beta or self.ease is None:
            stats = (float(full.mean()), float(full.std())) if self.serving.pop_normalize else None
            return raw, stats

        m_i, s_i = float(full.mean()), float(full.std()) or 1.0
        e_full = self.ease.score_one(self._user_positive_item_idx(user_id)).astype(np.float64)
        in_vocab = ~np.isnan(e_full)
        if in_vocab.any():
            m_e, s_e = float(e_full[in_vocab].mean()), (float(e_full[in_vocab].std()) or 1.0)
        else:
            m_e, s_e = 0.0, 1.0
        ez_full = np.where(in_vocab, (np.nan_to_num(e_full) - m_e) / s_e, 0.0)
        blend_full = (full - m_i) / s_i + beta * ez_full

        known = scope.item_idx >= 0
        ez_scope = np.zeros(len(raw))
        ez_scope[known] = ez_full[scope.item_idx[known]]
        scores = np.where(np.isfinite(raw), (raw - m_i) / s_i + beta * ez_scope, -np.inf).astype(np.float32)
        stats = (float(blend_full.mean()), float(blend_full.std())) if self.serving.pop_normalize else None
        return scores, stats

    def _resolve_user_factor(self, user_id) -> Optional[np.ndarray]:
        """Overlay / base-index factor, else the symmetric cold-start head's prediction
        from the user's reviews (brand-new users), else None."""
        factor = self._user_factor(f"loc_{user_id}")
        return factor if factor is not None else self._get_cold_start_user_factor(user_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_recommendations_for_user(self, user_id, max_recommendations: int = 10, scope: str = "local"):
        if scope == "external":
            return self._get_external_recommendations(user_id, max_recommendations)

        rated = set(
            Review.objects.filter(user_id=user_id, content_type=self.movie_content_type)
            .values_list("object_id", flat=True)
        )
        if not self.model_data:
            return self._get_popular_movies(max_recommendations, rated)
        self._maybe_reload_overlay()

        # Known in the base index or the overlay, else the symmetric cold-start head;
        # otherwise fall back to popularity.
        user_factor = self._resolve_user_factor(user_id)
        if user_factor is None:
            return self._get_popular_movies(max_recommendations, rated)

        movies = list(
            Movie.objects.filter(tmdb_id__isnull=False)
            .order_by("id")
            .values_list("id", "tmdb_id", "release_date")
        )
        if not movies:
            return self._get_popular_movies(max_recommendations, rated)
        movie_ids = np.array([m[0] for m in movies])
        cand = self._get_scope(
            "local",
            [int(m[1]) for m in movies],
            [m[2].year if m[2] else None for m in movies],
        )

        rated_rows = np.isin(movie_ids, list(rated))
        scores, stats = self._ranking_scores(user_id, user_factor, cand, excluded_rows=rated_rows)
        scores[rated_rows] = -np.inf
        order = rank_row(scores, cand.items, max_recommendations, self.serving, score_stats=stats)
        if len(order) == 0:
            return self._get_popular_movies(max_recommendations, rated)
        est = self._predict_ratings(f"loc_{user_id}", cand, user_factor)

        movies_by_id = {m.id: m for m in Movie.objects.filter(id__in=[int(movie_ids[i]) for i in order])}
        out = []
        for i in order:
            m = movies_by_id.get(int(movie_ids[i]))
            if m is not None:
                m.predicted_rating = round(float(np.clip(est[i], 0.5, 5.0)) * 2, 1)  # display in 0-10
                out.append(m)
        return out or self._get_popular_movies(max_recommendations, rated)

    # Only suggest external movies the displayed rating expects the user to like.
    _EXTERNAL_MIN_PREDICTED = 3.2

    def _get_external_recommendations(self, user_id, max_recommendations: int):
        if not self.model_data or not self.known_tmdb_ids:
            return []
        self._maybe_reload_overlay()

        user_factor = self._resolve_user_factor(user_id)
        if user_factor is None:
            return []

        local_tmdb_ids = set(
            Movie.objects.exclude(tmdb_id__isnull=True).values_list("tmdb_id", flat=True)
        )
        candidates = sorted(t for t in self.known_tmdb_ids - local_tmdb_ids if t in self.item_to_idx)
        if not candidates:
            return []
        cand = self._get_scope("external", candidates)

        est = np.clip(self._predict_ratings(f"loc_{user_id}", cand, user_factor), 0.5, 5.0)
        low = est < self._EXTERNAL_MIN_PREDICTED
        scores, stats = self._ranking_scores(user_id, user_factor, cand, excluded_rows=low)
        scores[low] = -np.inf
        order = rank_row(scores, cand.items, max_recommendations, self.serving, score_stats=stats)
        return [
            {
                "tmdb_id": int(cand.tmdb_ids[i]),
                "predicted_rating": round(float(est[i]) * 2, 1),
                "ranking_score": float(scores[i]),
            }
            for i in order
        ]

    # ------------------------------------------------------------------
    # Taste-map helpers (movie network graph)
    # ------------------------------------------------------------------

    _TASTE_PROFILE_KINDS = (
        ("genres", "user_genre_biases"),
        ("decades", "user_decade_biases"),
        ("languages", "user_language_biases"),
        ("runtime", "user_runtime_biases"),
    )

    def get_user_taste_profile(self, user_id_str: str) -> dict:
        """The user's learned preference terms per genre/decade/language/runtime bucket.

        These are the same per-user bias terms ``predict_rating`` adds up, read with
        the same overlay-first precedence, and returned on the 0-10 display scale
        (x2) - i.e. "how many points above/below expectation this user rates X".
        Zero/missing terms are omitted.
        """
        if not self.model_data:
            return {}
        self._maybe_reload_overlay()
        profile = {}
        for label, kind in self._TASTE_PROFILE_KINDS:
            base = getattr(self, kind, None) or {}
            keys = set(base.keys()) | set((self._overlay.get(kind) or {}).keys())
            values = {}
            for key in keys:
                value = self._ov_category_bias(kind, key, user_id_str)
                if value is None:
                    value = (base.get(key) or {}).get(user_id_str)
                if value is not None and abs(value) > 1e-4:
                    values[str(key)] = round(float(value) * 2, 2)
            profile[label] = values
        return profile

    def get_user_factor(self, user_id) -> Optional[np.ndarray]:
        """Ranking-space factor for a local user: overlay/base index first, then the
        symmetric cold-start head from their reviews. None if neither is available."""
        if not self.model_data:
            return None
        self._maybe_reload_overlay()
        factor = self._user_factor(f"loc_{user_id}")
        if factor is None:
            factor = self._get_cold_start_user_factor(user_id)
        return factor

    def get_item_factors(self, tmdb_ids: list[int]) -> dict[int, np.ndarray]:
        """Ranking-space factors for the given movies (cold-start head for unseen ones)."""
        out = {}
        cold = []
        for tmdb_id in tmdb_ids:
            idx = self.item_to_idx.get(int(tmdb_id))
            if idx is not None and self.item_factors is not None:
                out[int(tmdb_id)] = np.asarray(self.item_factors[idx], dtype=np.float32)
            else:
                cold.append(int(tmdb_id))
        # one batched cold-start call instead of _item_factor's per-movie one
        if cold and self.cold_start_head is not None and self.catalog.tmdb_to_genres:
            try:
                vecs = cold_start_predict_factors(self.cold_start_head, cold, self.catalog)
                out.update({tid: np.asarray(v, dtype=np.float32) for tid, v in zip(cold, vecs)})
            except Exception:
                logger.exception("Batched cold-start item factor prediction failed")
        return out

    def _get_popular_movies(self, limit: int = 10, exclude_movie_ids: Optional[set] = None) -> list:
        qs = Review.objects.filter(content_type=self.movie_content_type)
        if exclude_movie_ids:
            qs = qs.exclude(object_id__in=exclude_movie_ids)
        popular = (
            qs.values("object_id")
            .annotate(avg_rating=Avg("rating"), rating_count=Count("id"))
            .filter(rating_count__gte=1)
            .order_by("-avg_rating")[:limit]
        )
        ids = [m["object_id"] for m in popular]
        return list(Movie.objects.filter(id__in=ids))


_MODEL_FILENAME = "svd_model_latest.pkl"
_shared_recommender: Optional[MovieRecommender] = None
_shared_recommender_stamp: Optional[float] = None
_shared_recommender_lock = threading.Lock()


def model_stamp() -> Optional[float]:
    """mtime of the trained model file - a cheap version tag for caches built from it.

    ``model_version`` stays "5.0" across retrains, so it can't be used to bust caches.
    Returns None when no model file is present.
    """
    try:
        return os.path.getmtime(os.path.join(settings.BASE_DIR, "movies", "ml_models", _MODEL_FILENAME))
    except OSError:
        return None


def get_shared_recommender() -> MovieRecommender:
    """Process-wide MovieRecommender, rebuilt only when the model file changes.

    The model pickle is ~300 MB, so building ``MovieRecommender()`` per call re-reads
    it every time. Intended for background (Django Q) work - web requests should read
    results those tasks cached rather than keep the model resident in every worker.
    """
    global _shared_recommender, _shared_recommender_stamp
    stamp = model_stamp()
    with _shared_recommender_lock:
        if _shared_recommender is None or stamp != _shared_recommender_stamp:
            _shared_recommender = MovieRecommender()
            _shared_recommender_stamp = stamp
        return _shared_recommender
