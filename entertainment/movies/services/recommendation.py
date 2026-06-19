"""Inference-time movie recommender.

Loads a v5.0 bundle (or a legacy v4 pickle for backward compat) and a separate
overlay pickle for per-user fold-in updates. CPU-only — never imports CuPy or
``implicit.gpu``.

Scoring:
- ``predict_rating`` returns a 0-5 explicit score from the bias hierarchy
  (used for UI display). Does NOT add iALS factors, which live on a different
  scale and would distort the displayed rating.
- ``_score_for_ranking`` returns the iALS dot-product score used to rank the
  candidate set. Falls back to the cold-start ridge head for unseen items.
"""
from __future__ import annotations

import logging
import os
import pickle
import time
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
    predict_factors as cold_start_predict_factors,
)
from movies.services.recommender.data_loading import CatalogLookups

User = get_user_model()
logger = logging.getLogger(__name__)


_OVERLAY_RELOAD_INTERVAL_SECONDS = 300  # 5 min TTL for picking up fold-in updates


class MovieRecommender:
    """Loads the trained model + overlay; serves predictions and recommendations."""

    def __init__(self):
        self._movie_content_type: Optional[ContentType] = None
        self.model_data: Optional[dict] = None
        self.known_tmdb_ids: set[int] = set()
        self._genre_combo_avg_bias: dict = {}

        # v5.0 sections
        self.cold_start_head: Optional[ColdStartHead] = None
        self.catalog: CatalogLookups = CatalogLookups()

        # Overlay state
        self._overlay: dict = {}
        self._overlay_mtime: float = 0.0
        self._overlay_loaded_at: float = 0.0

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
            if not self.model_data:
                return

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

            # Catalog lookups
            self.catalog.tmdb_to_genres = catalog_dict.get("tmdb_to_genres") or data.get("tmdb_to_genres", {})
            self.catalog.tmdb_to_language = catalog_dict.get("tmdb_to_language") or data.get("tmdb_to_language", {})
            self.catalog.tmdb_to_runtime_bucket = catalog_dict.get("tmdb_to_runtime_bucket") or data.get("tmdb_to_runtime_bucket", {})
            self.catalog.tmdb_to_year = catalog_dict.get("tmdb_to_year") or data.get("tmdb_id_to_year", {})
            self.catalog.tmdb_vote_data = catalog_dict.get("tmdb_vote_data") or data.get("tmdb_vote_data", {})

            # Aliases preserved for templates / call sites that still read these names
            self.tmdb_to_genres = self.catalog.tmdb_to_genres
            self.tmdb_to_language = self.catalog.tmdb_to_language
            self.tmdb_to_runtime_bucket = self.catalog.tmdb_to_runtime_bucket
            self.tmdb_id_to_year = self.catalog.tmdb_to_year
            self.tmdb_vote_data = self.catalog.tmdb_vote_data
            self.genre_mapping = data.get("genre_mapping", {})  # empty in v5 (= identity)

            self.known_tmdb_ids = set(data.get("known_tmdb_ids") or list(self.item_to_idx.keys()))

            # Cold-start head
            cold = data.get("cold_start")
            if cold is not None:
                self.cold_start_head = ColdStartHead(
                    coef=np.asarray(cold["coef"], dtype=np.float32),
                    intercept=np.asarray(cold["intercept"], dtype=np.float32),
                    decades=list(cold["decades"]),
                    languages=list(cold["languages"]),
                    feature_dim=int(cold["feature_dim"]),
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

    def predict_rating(self, user_id_str: str, tmdb_id_int: int, year: Optional[int] = None) -> float:
        """Return a 0-5 explicit-rating estimate from the bias hierarchy.

        Does NOT add the iALS factor dot product — those scores live on a
        different scale and would corrupt the displayed rating. Use
        ``_score_for_ranking`` to *order* candidates.
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

        return self.global_mean + b_i + b_u + b_y + b_g + b_dec + b_lang + b_rt

    # ------------------------------------------------------------------
    # Ranking score (iALS dot product, with cold-start fallback)
    # ------------------------------------------------------------------

    def _user_factor(self, user_id_str: str) -> Optional[np.ndarray]:
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

    def _score_for_ranking(self, user_id_str: str, tmdb_id_int: int) -> float:
        """iALS dot-product score; 0 if no factors are available."""
        u = self._user_factor(user_id_str)
        if u is None:
            return 0.0
        v = self._item_factor(int(tmdb_id_int))
        if v is None:
            return 0.0
        return float(np.dot(u, v))

    # ------------------------------------------------------------------
    # Diversity re-ranking (MMR) — unchanged behaviour
    # ------------------------------------------------------------------

    def _rerank_mmr(self, candidates: list[dict], max_recommendations: int, diversity_alpha: float = 0.7) -> list[dict]:
        if not candidates:
            return []

        scores = [c.get("ranking_score", c["predicted_rating"]) for c in candidates]
        s_max, s_min = max(scores), min(scores)
        s_range = s_max - s_min if s_max > s_min else 1.0

        feats: dict[int, dict] = {}
        for c in candidates:
            tid = c["tmdb_id"]
            raw = self.tmdb_to_genres.get(tid, []) or []
            genres = set(self.genre_mapping.get(g, g) if self.genre_mapping else g for g in raw)
            year = self.tmdb_id_to_year.get(tid)
            feats[tid] = {
                "genres": genres,
                "language": self.tmdb_to_language.get(tid, "en") if self.tmdb_to_language else "en",
                "runtime_bucket": self.tmdb_to_runtime_bucket.get(tid, "standard") if self.tmdb_to_runtime_bucket else "standard",
                "decade": (year // 10) * 10 if year else None,
            }

        remaining = sorted(candidates, key=lambda x: x.get("ranking_score", x["predicted_rating"]), reverse=True)
        selected = [remaining.pop(0)] if remaining else []

        while len(selected) < max_recommendations and remaining:
            best_score = -float("inf")
            best_idx = -1
            for i, item in enumerate(remaining):
                i_feat = feats.get(item["tmdb_id"], {})
                i_gens = i_feat.get("genres", set())
                max_sim = 0.0
                for sel in selected:
                    s_feat = feats.get(sel["tmdb_id"], {})
                    s_gens = s_feat.get("genres", set())
                    genre_sim = (len(i_gens & s_gens) / len(i_gens | s_gens)) if (i_gens and s_gens) else 0.0
                    lang_sim = 1.0 if i_feat.get("language") == s_feat.get("language") else 0.0
                    rt_sim = 1.0 if i_feat.get("runtime_bucket") == s_feat.get("runtime_bucket") else 0.0
                    dec_sim = 1.0 if i_feat.get("decade") and i_feat["decade"] == s_feat.get("decade") else 0.0
                    sim = 0.50 * genre_sim + 0.20 * lang_sim + 0.15 * rt_sim + 0.15 * dec_sim
                    if sim > max_sim:
                        max_sim = sim
                norm = (item.get("ranking_score", item["predicted_rating"]) - s_min) / s_range
                mmr = diversity_alpha * norm - (1.0 - diversity_alpha) * max_sim
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i
            selected.append(remaining.pop(best_idx if best_idx >= 0 else 0))

        return selected

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

        user_id_str = f"loc_{user_id}"
        # User must be known in either the base index or the overlay
        has_user = (user_id_str in self.user_to_idx) or (self._ov_user_factor(user_id_str) is not None)
        if not has_user:
            return self._get_popular_movies(max_recommendations, rated)

        candidates = (
            Movie.objects.exclude(id__in=rated)
            .filter(tmdb_id__isnull=False)
            .values("id", "tmdb_id", "release_date")
        )

        predictions = []
        for movie in candidates:
            tmdb_id = int(movie["tmdb_id"])
            year = movie["release_date"].year if movie["release_date"] else None
            ranking_score = self._score_for_ranking(user_id_str, tmdb_id)
            est = self.predict_rating(user_id_str, tmdb_id, year=year)
            if est == 0:
                continue
            est = max(0.5, min(5.0, est))
            predictions.append({
                "id": movie["id"],
                "tmdb_id": tmdb_id,
                "predicted_rating": round(est * 2, 1),  # display in 0-10
                "ranking_score": ranking_score,
            })

        predictions.sort(key=lambda x: x["ranking_score"], reverse=True)
        pool = predictions[: max_recommendations * 3]
        top = self._rerank_mmr(pool, max_recommendations)

        ids = [p["id"] for p in top]
        movies_by_id = {m.id: m for m in Movie.objects.filter(id__in=ids)}
        out = []
        for item in top:
            m = movies_by_id.get(item["id"])
            if m is not None:
                m.predicted_rating = item["predicted_rating"]
                out.append(m)
        return out or self._get_popular_movies(max_recommendations, rated)

    def _get_external_recommendations(self, user_id, max_recommendations: int):
        if not self.model_data or not self.known_tmdb_ids:
            return []

        local_tmdb_ids = set(
            Movie.objects.exclude(tmdb_id__isnull=True).values_list("tmdb_id", flat=True)
        )
        candidates = self.known_tmdb_ids - local_tmdb_ids

        user_id_str = f"loc_{user_id}"
        if user_id_str not in self.user_to_idx and self._ov_user_factor(user_id_str) is None:
            return []

        predictions = []
        for tmdb_id in candidates:
            if tmdb_id not in self.item_to_idx:
                continue
            ranking_score = self._score_for_ranking(user_id_str, int(tmdb_id))
            est = self.predict_rating(user_id_str, int(tmdb_id))
            est = max(0.5, min(5.0, est))
            if est < 3.2:
                continue
            predictions.append({
                "tmdb_id": int(tmdb_id),
                "predicted_rating": round(est * 2, 1),
                "ranking_score": ranking_score,
            })

        predictions.sort(key=lambda x: x["ranking_score"], reverse=True)
        return self._rerank_mmr(predictions[: max_recommendations * 3], max_recommendations)

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
