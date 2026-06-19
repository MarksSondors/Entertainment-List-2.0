"""Per-user fold-in: incrementally refresh a local user's biases and ranking factor.

Runs on the inference VM after new local reviews land. The base pickle stays
untouched; an "overlay" pickle holds the per-user diff. Inference layers the
overlay on top of the base when computing scores.

Usage::

    python manage.py update_recommender --all-stale       # every user with reviews newer than base
    python manage.py update_recommender --user-id 42      # one user
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import numpy as np
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from movies.services.recommender.data_loading import (
    RUNTIME_BUCKETS,
    TMDB_GENRES,
    _runtime_bucket,
)
from movies.services.recommender.model_io import (
    is_overlay_compatible,
    load_bundle,
    load_overlay,
    now_iso,
    save_overlay,
    _empty_overlay,
)

logger = logging.getLogger(__name__)


def _stale_user_ids(base_trained_at_iso: str) -> list[int]:
    """Return PKs of users with at least one Movie Review newer than the base model."""
    from movies.models import Movie
    from custom_auth.models import Review

    cutoff = parse_datetime(base_trained_at_iso) or datetime.now(timezone.utc)
    movie_ct = ContentType.objects.get_for_model(Movie)
    return list(
        Review.objects
        .filter(content_type=movie_ct, date_added__gt=cutoff)
        .values_list("user_id", flat=True)
        .distinct()
    )


def _user_reviews_payload(user_pk: int, bundle: dict) -> list[dict]:
    """Materialize one user's movie ratings in the same shape as the trainer expects.

    Skips reviews whose TMDB id is not in either the trained item-bias dict
    OR the cold-start head (would produce zero-info rows).
    """
    from movies.models import Movie
    from custom_auth.models import Review

    movie_ct = ContentType.objects.get_for_model(Movie)
    rows = list(
        Review.objects
        .filter(content_type=movie_ct, user_id=user_pk)
        .values_list("object_id", "rating", "date_added")
    )
    if not rows:
        return []

    movies = (
        Movie.objects
        .filter(id__in=[r[0] for r in rows])
        .exclude(tmdb_id__isnull=True)
        .prefetch_related("genres")
    )
    movie_meta: dict[int, dict] = {}
    for m in movies:
        year = m.release_date.year if m.release_date else 1900
        names = [g.name for g in m.genres.all() if g.name in TMDB_GENRES]
        movie_meta[m.id] = {"tmdb_id": int(m.tmdb_id), "year": int(year), "genres": names}

    catalog = bundle.get("catalog", {})
    tmdb_to_lang = catalog.get("tmdb_to_language", bundle.get("tmdb_to_language", {}))
    tmdb_to_rb = catalog.get("tmdb_to_runtime_bucket", bundle.get("tmdb_to_runtime_bucket", {}))
    tmdb_to_genres = catalog.get("tmdb_to_genres", bundle.get("tmdb_to_genres", {}))

    item_biases = bundle.get("biases", {}).get("item_biases") or bundle.get("item_biases", {})
    item_to_idx = bundle.get("ranking", {}).get("item_to_idx") or bundle.get("item_to_idx", {})
    cold = bundle.get("cold_start")

    payload = []
    for movie_pk, rating, _date_added in rows:
        meta = movie_meta.get(movie_pk)
        if not meta:
            continue
        tid = meta["tmdb_id"]
        # Reject rows where we can't compute item bias and have no cold-start head
        if tid not in item_biases and tid not in item_to_idx and cold is None:
            continue
        # Prefer catalog-provided genre list, fall back to local DB genres
        genres = tmdb_to_genres.get(tid) or meta["genres"]
        payload.append({
            "tmdb_id": tid,
            "rating": float(rating) / 2.0,
            "year": meta["year"],
            "decade": (meta["year"] // 10) * 10,
            "genres": list(genres or []),
            "language": str(tmdb_to_lang.get(tid, "en")),
            "runtime_bucket": str(tmdb_to_rb.get(tid, "standard")),
        })
    return payload


def _build_feature_row(rev: dict, decades: list[int], languages: list[str]) -> np.ndarray:
    """Build one row of the per-user category-bias feature matrix mirroring the trainer's layout."""
    F = len(TMDB_GENRES) + len(decades) + len(languages) + len(RUNTIME_BUCKETS)
    x = np.zeros(F, dtype=np.float32)
    g_off = 0
    d_off = g_off + len(TMDB_GENRES)
    l_off = d_off + len(decades)
    r_off = l_off + len(languages)

    genre_idx = {g: i for i, g in enumerate(TMDB_GENRES)}
    for g in rev["genres"]:
        col = genre_idx.get(g)
        if col is not None:
            x[g_off + col] = 1.0

    if rev["decade"] in decades:
        x[d_off + decades.index(rev["decade"])] = 1.0
    if rev["language"] in languages:
        x[l_off + languages.index(rev["language"])] = 1.0
    rt = rev["runtime_bucket"]
    if rt in RUNTIME_BUCKETS:
        x[r_off + RUNTIME_BUCKETS.index(rt)] = 1.0
    else:
        x[r_off + RUNTIME_BUCKETS.index("standard")] = 1.0
    return x


def _solve_user_bias(payload: list[dict], bundle: dict, damping: float) -> tuple[float, np.ndarray]:
    """Compute the user's bias and the residuals after subtracting global/year/item/user."""
    biases = bundle.get("biases", {}) or bundle
    global_mean = float(biases["global_mean"])
    year_b = biases["year_biases"]
    item_b = biases["item_biases"]

    ratings = np.array([r["rating"] for r in payload], dtype=np.float32)
    yb = np.array([float(year_b.get(int(r["year"]), 0.0)) for r in payload], dtype=np.float32)
    ib = np.array([float(item_b.get(int(r["tmdb_id"]), 0.0)) for r in payload], dtype=np.float32)

    resid = ratings - global_mean - yb - ib
    user_bias = float(np.sum(resid) / (len(resid) + damping))
    base_resid = (resid - user_bias).astype(np.float32)
    return user_bias, base_resid


def _solve_category_biases(
    payload: list[dict],
    base_resid: np.ndarray,
    decades: list[int],
    languages: list[str],
    ridge_lambda: float,
) -> dict:
    """Joint ridge over genre/decade/language/runtime for one user. Returns per-block dicts
    with bias values keyed by category name (not user-id)."""
    F = len(TMDB_GENRES) + len(decades) + len(languages) + len(RUNTIME_BUCKETS)
    X = np.stack([_build_feature_row(r, decades, languages) for r in payload], axis=0)
    A = X.T @ X + ridge_lambda * np.eye(F, dtype=np.float32)
    b = X.T @ base_resid
    try:
        beta = np.linalg.solve(A, b).astype(np.float32)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(A, b, rcond=None)[0].astype(np.float32)

    out: dict = {"genre": {}, "decade": {}, "language": {}, "runtime": {}}
    col = 0
    for name, keys in (
        ("genre", TMDB_GENRES),
        ("decade", decades),
        ("language", languages),
        ("runtime", RUNTIME_BUCKETS),
    ):
        for ki, key in enumerate(keys):
            v = float(beta[col + ki])
            if abs(v) > 1e-6:
                out[name][key] = v
        col += len(keys)
    return out


def _solve_user_factor(
    payload: list[dict],
    bundle: dict,
    *,
    alpha: float,
    local_user_weight: float,
    threshold: float,
    reg: float,
) -> np.ndarray | None:
    """Closed-form solve  u = (V^T C V + λ I)^-1 V^T C r  for one user, V fixed.

    Returns ``None`` if the user has no positive interactions over known items.
    """
    ranking = bundle.get("ranking", {})
    item_to_idx: dict[int, int] = ranking.get("item_to_idx") or bundle.get("item_to_idx", {})
    item_factors: np.ndarray | None = ranking.get("item_factors")
    if item_factors is None:
        item_factors = bundle.get("item_factors")
    if item_factors is None or not item_to_idx:
        return None

    pos = [r for r in payload if r["rating"] >= threshold and int(r["tmdb_id"]) in item_to_idx]
    if not pos:
        return None

    span = max(5.0 - threshold, 1e-3)
    F = item_factors.shape[1]
    VtV = item_factors.T @ item_factors                                # (F, F)
    delta = np.zeros((F, F), dtype=np.float32)
    rhs = np.zeros(F, dtype=np.float32)
    for r in pos:
        idx = item_to_idx[int(r["tmdb_id"])]
        v = item_factors[idx]                                           # (F,)
        strength = max(0.0, min(1.0, (r["rating"] - threshold) / span))
        c = (1.0 + alpha * strength) * local_user_weight
        delta += (c - 1.0) * np.outer(v, v)
        rhs += c * v
    A = (VtV + delta + reg * np.eye(F, dtype=np.float32)).astype(np.float32)
    return np.linalg.solve(A, rhs).astype(np.float32)


class Command(BaseCommand):
    help = "Fold-in: refresh per-user biases and ranking factor for users with new local reviews."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, default=None,
                            help="Update a single user (overrides --all-stale).")
        parser.add_argument("--all-stale", action="store_true",
                            help="Update every local user with reviews newer than the base model.")
        parser.add_argument("--ridge-lambda", type=float, default=10.0)
        parser.add_argument("--user-damping", type=float, default=10.0)
        parser.add_argument("--factor-reg", type=float, default=0.05)

    def handle(self, *args, **opts):
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                            stream=sys.stdout)
        bundle = load_bundle()
        if bundle is None:
            self.stderr.write(self.style.ERROR("No base model pickle found."))
            return
        meta = bundle.get("metadata", {})
        base_trained_at = meta.get("trained_at")
        if not base_trained_at:
            self.stderr.write(self.style.ERROR("Base bundle has no metadata.trained_at; refusing fold-in."))
            return

        # Hyperparameters baked into the base model (used to keep fold-in consistent)
        ranking_meta = bundle.get("ranking", {})
        # Mirror the confidence multiplier the trainer applies in build_confidence_matrix.
        alpha_conf = 40.0
        threshold = float(ranking_meta.get("positive_threshold", meta.get("positive_threshold", 3.5)))
        local_user_weight = 3.0  # mirror the trainer's local boost

        # Determine target users
        if opts["user_id"] is not None:
            target_user_ids = [int(opts["user_id"])]
        elif opts["all_stale"]:
            target_user_ids = _stale_user_ids(base_trained_at)
        else:
            self.stderr.write(self.style.WARNING(
                "Neither --user-id nor --all-stale provided; defaulting to --all-stale."
            ))
            target_user_ids = _stale_user_ids(base_trained_at)

        if not target_user_ids:
            self.stdout.write("No stale users to update.")
            return

        # Load or initialize overlay
        overlay = load_overlay()
        if not is_overlay_compatible(overlay, base_trained_at):
            overlay = _empty_overlay()
            overlay["base_trained_at"] = base_trained_at

        # Decade / language vocabularies must mirror what the trainer used
        decades = sorted({int(d) for d in (bundle.get("biases", {}).get("user_decade_biases") or bundle.get("user_decade_biases", {})).keys()})
        languages = sorted((bundle.get("biases", {}).get("user_language_biases") or bundle.get("user_language_biases", {})).keys())

        n_done = 0
        n_skipped = 0
        for user_pk in target_user_ids:
            user_id_str = f"loc_{user_pk}"
            payload = _user_reviews_payload(int(user_pk), bundle)
            if not payload:
                n_skipped += 1
                continue

            user_bias, base_resid = _solve_user_bias(
                payload, bundle, damping=float(opts["user_damping"])
            )
            cat = _solve_category_biases(
                payload, base_resid, decades, languages,
                ridge_lambda=float(opts["ridge_lambda"]),
            )
            factor = _solve_user_factor(
                payload, bundle,
                alpha=alpha_conf,
                local_user_weight=local_user_weight,
                threshold=threshold,
                reg=float(opts["factor_reg"]),
            )

            overlay["user_biases"][user_id_str] = user_bias
            for genre, val in cat["genre"].items():
                overlay["user_genre_biases"].setdefault(genre, {})[user_id_str] = val
            for decade, val in cat["decade"].items():
                overlay["user_decade_biases"].setdefault(int(decade), {})[user_id_str] = val
            for lang, val in cat["language"].items():
                overlay["user_language_biases"].setdefault(lang, {})[user_id_str] = val
            for rt, val in cat["runtime"].items():
                overlay["user_runtime_biases"].setdefault(rt, {})[user_id_str] = val
            if factor is not None:
                overlay["ranking_user_factors"][user_id_str] = factor.astype(np.float32)
                overlay["ranking_user_to_idx"][user_id_str] = -1  # placeholder; inference looks up by id
            n_done += 1

        overlay["updated_at"] = now_iso()
        save_overlay(overlay)
        self.stdout.write(self.style.SUCCESS(
            f"Fold-in complete: {n_done} users updated, {n_skipped} skipped (no usable reviews)."
        ))
