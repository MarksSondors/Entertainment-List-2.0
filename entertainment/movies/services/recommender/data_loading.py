"""Load MovieLens ratings + TMDB catalog + local Reviews into a single DataFrame.

Output schema (all columns lowercase):
    user_id, tmdb_id, rating, timestamp, year, decade,
    genres (list[str], TMDB names), language, runtime_bucket, source ('ml'|'loc')

Differences from the legacy loader:
- TMDB genres are kept native (no lossy mapping to MovieLens genres)
- No row-duplication boost — local users get a sample_weight downstream
- Per-(user, item) dedup keeping latest timestamp
- Item-side pruning (drop items with < min_item_ratings ratings)
"""
from __future__ import annotations

import gc
import hashlib
import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from django.conf import settings
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger(__name__)

# Bump when load_dataset's output schema/logic changes so stale cached datasets
# (built by an older version of this loader) are never silently reused. Pickled
# CatalogLookups sidecars restore __dict__ directly (bypassing dataclass defaults),
# so adding a new CatalogLookups field also requires a bump here — otherwise old
# cached catalogs silently lack the new attribute entirely (AttributeError, not a
# missing-key KeyError, since it's not a dict).
_CACHE_SCHEMA_VERSION = 2


# Canonical TMDB genre vocabulary used across training / inference.
TMDB_GENRES: list[str] = [
    "Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary",
    "Drama", "Family", "Fantasy", "History", "Horror", "Music", "Mystery",
    "Romance", "Science Fiction", "TV Movie", "Thriller", "War", "Western",
]

RUNTIME_BUCKETS: list[str] = ["short", "standard", "long", "epic"]


# MovieLens has its own genre vocabulary. We map to TMDB names so the whole
# pipeline uses one vocabulary. Lossy entries collapse to the closest TMDB name;
# unknown ML names are dropped silently.
ML_TO_TMDB: dict[str, str] = {
    "Action": "Action",
    "Adventure": "Adventure",
    "Animation": "Animation",
    "Children's": "Family",
    "Children": "Family",
    "Comedy": "Comedy",
    "Crime": "Crime",
    "Documentary": "Documentary",
    "Drama": "Drama",
    "Fantasy": "Fantasy",
    "Film-Noir": "Crime",
    "Horror": "Horror",
    "Musical": "Music",
    "Mystery": "Mystery",
    "Romance": "Romance",
    "Sci-Fi": "Science Fiction",
    "Thriller": "Thriller",
    "War": "War",
    "Western": "Western",
    "IMAX": None,
    "(no genres listed)": None,
}


@dataclass
class CatalogLookups:
    """Per-tmdb metadata exported alongside the model for inference."""
    tmdb_to_genres: dict[int, list[str]] = field(default_factory=dict)
    tmdb_to_language: dict[int, str] = field(default_factory=dict)
    tmdb_to_runtime_bucket: dict[int, str] = field(default_factory=dict)
    tmdb_to_year: dict[int, int] = field(default_factory=dict)
    tmdb_vote_data: dict[int, tuple[float, int]] = field(default_factory=dict)
    # Cast/crew signal for cold-start (only populated for movies present in the local
    # DB — MovieLens/TMDB CSV catalogs carry no cast data). Sparse by nature; the
    # cold-start feature builder treats a missing entry as "unknown".
    tmdb_to_director: dict[int, Optional[int]] = field(default_factory=dict)   # tmdb_id -> director Person.id
    tmdb_to_top_cast: dict[int, list[int]] = field(default_factory=dict)      # tmdb_id -> up to 3 actor Person.id


def _runtime_bucket(runtime: float) -> str:
    if runtime <= 0:
        return "standard"
    if runtime <= 90:
        return "short"
    if runtime <= 120:
        return "standard"
    if runtime <= 150:
        return "long"
    return "epic"


def _parse_tmdb_genres(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [g.strip() for g in raw.split(",") if g.strip() in TMDB_GENRES or g.strip()]


def _parse_ml_genres(raw: object) -> list[str]:
    if not isinstance(raw, str) or raw == "(no genres listed)":
        return []
    out: list[str] = []
    for ml in raw.split("|"):
        mapped = ML_TO_TMDB.get(ml.strip())
        if mapped:
            out.append(mapped)
    return out


def _load_movielens_meta(data_dir) -> tuple[Optional[pd.DataFrame], dict[int, list[str]]]:
    movies_file = data_dir / "movies.csv"
    links_file = data_dir / "links.csv"
    if not movies_file.exists() or not links_file.exists():
        logger.error("MovieLens metadata not found in %s", data_dir)
        return None, {}

    links = pd.read_csv(
        links_file,
        usecols=["movieId", "tmdbId"],
        dtype={"movieId": "int32", "tmdbId": "float"},
    ).dropna().astype({"movieId": "int32", "tmdbId": "int32"})

    movies = pd.read_csv(
        movies_file,
        usecols=["movieId", "title", "genres"],
        dtype={"movieId": "int32", "genres": "string"},
    )
    movies["year"] = (
        movies["title"].str.extract(r"\((\d{4})\)", expand=False).fillna(1900).astype("int32")
    )
    meta = pd.merge(movies, links, on="movieId", how="inner")

    ml_genre_map: dict[int, list[str]] = {}
    for row in meta.itertuples(index=False):
        ml_genre_map[int(row.tmdbId)] = _parse_ml_genres(row.genres)

    return meta[["movieId", "tmdbId", "year"]], ml_genre_map


def _load_tmdb_catalog(data_dir) -> Optional[pd.DataFrame]:
    catalog_file = data_dir.parent / "TMDB_movie_dataset_v11.csv"
    if not catalog_file.exists():
        logger.warning("TMDB catalog not found at %s — skipping enrichment", catalog_file)
        return None
    df = pd.read_csv(
        catalog_file,
        usecols=[
            "id", "vote_average", "vote_count", "runtime",
            "original_language", "status", "genres",
        ],
        dtype={"id": "float64"},
    ).dropna(subset=["id"])
    df["id"] = df["id"].astype("int32")
    df = df.rename(columns={"id": "tmdb_id"})

    df["vote_average"] = pd.to_numeric(df["vote_average"], errors="coerce").fillna(0).astype("float32")
    df["vote_count"] = pd.to_numeric(df["vote_count"], errors="coerce").fillna(0).astype("int32")
    df["runtime"] = pd.to_numeric(df["runtime"], errors="coerce").fillna(0).astype("float32")
    df["original_language"] = df["original_language"].fillna("en").astype(str)
    df["status"] = df["status"].fillna("Unknown").astype(str)
    df["runtime_bucket"] = df["runtime"].map(_runtime_bucket)
    return df


def _load_local_reviews(catalog: CatalogLookups) -> pd.DataFrame:
    """Pull local Reviews into the same schema as MovieLens. Always uses TMDB
    genres from the local DB (Movie.genres -> TMDB names verbatim)."""
    from movies.models import Movie  # late import: avoids circular imports
    from custom_auth.models import Review

    movie_ct = ContentType.objects.get_for_model(Movie)
    rows = list(
        Review.objects
        .filter(content_type=movie_ct)
        .values_list("user_id", "object_id", "rating", "date_added")
    )
    if not rows:
        return pd.DataFrame()

    movie_qs = (
        Movie.objects
        .exclude(tmdb_id__isnull=True)
        .prefetch_related("genres")
        .values("id", "tmdb_id", "release_date")
    )
    movie_meta: dict[int, tuple[int, int]] = {}
    for m in movie_qs:
        year = m["release_date"].year if m["release_date"] else 1900
        movie_meta[m["id"]] = (m["tmdb_id"], year)

    # Backfill genres from local DB so movies missing from MovieLens still get tagged
    for movie in Movie.objects.exclude(tmdb_id__isnull=True).prefetch_related("genres"):
        names = [g.name for g in movie.genres.all() if g.name in TMDB_GENRES]
        if names and (movie.tmdb_id not in catalog.tmdb_to_genres or not catalog.tmdb_to_genres[movie.tmdb_id]):
            catalog.tmdb_to_genres[movie.tmdb_id] = names

    records = []
    for user_pk, movie_pk, rating, date_added in rows:
        if movie_pk not in movie_meta:
            continue
        tmdb_id, year = movie_meta[movie_pk]
        records.append({
            "user_id": f"loc_{user_pk}",
            "tmdb_id": int(tmdb_id),
            "rating": float(rating) / 2.0,  # local 0-10 -> ML 0-5 scale
            "timestamp": int(date_added.timestamp()),
            "year": int(year),
            "source": "loc",
        })
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    return df


def _load_person_lookups(catalog: CatalogLookups) -> None:
    """Populate ``catalog.tmdb_to_director`` / ``tmdb_to_top_cast`` from local
    MediaPerson rows. Only local-DB movies have cast/crew data (MovieLens/TMDB
    CSV catalogs don't carry it), so this is inherently sparse \u2014 cold-start
    features fall back to "unknown" for everything else. Two bulk queries
    (director, top-3 cast) instead of per-movie property access to avoid N+1s.
    """
    from movies.models import Movie  # late import: avoids circular imports
    from custom_auth.models import MediaPerson

    movie_ct = ContentType.objects.get_for_model(Movie)
    tmdb_by_pk: dict[int, int] = dict(
        Movie.objects.exclude(tmdb_id__isnull=True).values_list("id", "tmdb_id")
    )
    if not tmdb_by_pk:
        return

    director_rows = (
        MediaPerson.objects
        .filter(content_type=movie_ct, role="Director", person__tmdb_id__isnull=False)
        .order_by("object_id", "order")
        .values_list("object_id", "person__tmdb_id")
    )
    seen_director: set[int] = set()
    for movie_pk, person_tmdb_id in director_rows:
        tmdb_id = tmdb_by_pk.get(movie_pk)
        if tmdb_id is None or movie_pk in seen_director:
            continue
        seen_director.add(movie_pk)
        catalog.tmdb_to_director[tmdb_id] = int(person_tmdb_id)

    cast_rows = (
        MediaPerson.objects
        .filter(content_type=movie_ct, role="Actor", person__tmdb_id__isnull=False)
        .order_by("object_id", "order")
        .values_list("object_id", "person__tmdb_id")
    )
    cast_by_pk: dict[int, list[int]] = {}
    for movie_pk, person_tmdb_id in cast_rows:
        bucket = cast_by_pk.setdefault(movie_pk, [])
        if len(bucket) < 3:
            bucket.append(int(person_tmdb_id))
    for movie_pk, cast in cast_by_pk.items():
        tmdb_id = tmdb_by_pk.get(movie_pk)
        if tmdb_id is not None:
            catalog.tmdb_to_top_cast[tmdb_id] = cast

    logger.info(
        "Person lookups: %d movies with a known director, %d with top-cast",
        len(catalog.tmdb_to_director), len(catalog.tmdb_to_top_cast),
    )


def load_watchlist_pairs() -> pd.DataFrame:
    """Local Watchlist entries (Movie only) as (user_id, tmdb_id) pairs, in the same
    ``loc_{pk}`` user-id namespace as ratings. Used as extra low-confidence implicit
    positives for iALS \u2014 users watchlist far more often than they rate, so this
    densifies the confidence matrix for users/items that already have some signal
    elsewhere. Returns an empty DataFrame if the Watchlist app/table isn't available.
    """
    from movies.models import Movie
    from custom_auth.models import Watchlist

    movie_ct = ContentType.objects.get_for_model(Movie)
    tmdb_by_pk: dict[int, int] = dict(
        Movie.objects.exclude(tmdb_id__isnull=True).values_list("id", "tmdb_id")
    )
    rows = list(
        Watchlist.objects
        .filter(content_type=movie_ct)
        .values_list("user_id", "object_id")
    )
    records = [
        {"user_id": f"loc_{user_pk}", "tmdb_id": int(tmdb_by_pk[movie_pk])}
        for user_pk, movie_pk in rows
        if movie_pk in tmdb_by_pk
    ]
    if not records:
        return pd.DataFrame(columns=["user_id", "tmdb_id"])
    return pd.DataFrame(records).drop_duplicates()


def _dataset_cache_dir() -> Path:
    p = Path(settings.BASE_DIR) / "data" / ".recommender_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dataset_cache_key(
    data_dir: Path,
    min_user_ratings: int,
    min_item_ratings: int,
    released_only: bool,
) -> str:
    """Fingerprint everything that affects ``load_dataset``'s output: source file
    sizes/mtimes, local Review count/recency, and the tunable parameters. Cheap to
    compute (stat calls + one DB count query) relative to reprocessing 32M rows.
    """
    from custom_auth.models import Review

    parts = [f"schema={_CACHE_SCHEMA_VERSION}"]
    for f in (
        data_dir / "ratings.csv",
        data_dir / "movies.csv",
        data_dir / "links.csv",
        data_dir.parent / "TMDB_movie_dataset_v11.csv",
    ):
        if f.exists():
            st = f.stat()
            parts.append(f"{f.name}:{st.st_size}:{int(st.st_mtime)}")
        else:
            parts.append(f"{f.name}:missing")

    review_count = Review.objects.count()
    latest_review = Review.objects.order_by("-date_added").values_list("date_added", flat=True).first()
    parts.append(f"reviews={review_count}:{latest_review.isoformat() if latest_review else 'none'}")
    parts.append(f"params={min_user_ratings}:{min_item_ratings}:{released_only}")

    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def load_dataset(
    *,
    min_user_ratings: int = 10,
    min_item_ratings: int = 5,
    released_only: bool = True,
    use_cache: bool = True,
) -> tuple[Optional[pd.DataFrame], CatalogLookups]:
    """Build the full training DataFrame. Returns (df, catalog_lookups).

    Raises nothing on missing ML files; returns (None, empty CatalogLookups) instead.

    When ``use_cache`` is True (default), the assembled dataset is cached to Parquet
    (+ a pickled ``CatalogLookups`` sidecar) under ``data/.recommender_cache/``, keyed
    by a fingerprint of the source CSVs, local Review count/recency, and the tunable
    parameters. Re-running training without touching any inputs reuses the cache
    instead of re-reading and re-merging the full ml-32m CSVs every time.
    """
    data_dir = settings.BASE_DIR / "data" / "ml-32m"
    catalog = CatalogLookups()

    cache_key: Optional[str] = None
    if use_cache:
        try:
            cache_key = _dataset_cache_key(data_dir, min_user_ratings, min_item_ratings, released_only)
            cache_dir = _dataset_cache_dir()
            df_path = cache_dir / f"{cache_key}.parquet"
            catalog_path = cache_dir / f"{cache_key}.catalog.pkl"
            if df_path.exists() and catalog_path.exists():
                logger.info("Loading cached training dataset (key=%s) from %s", cache_key, df_path)
                df = pd.read_parquet(df_path)
                with open(catalog_path, "rb") as fh:
                    catalog = pickle.load(fh)
                return df, catalog
        except Exception:
            logger.exception("Dataset cache lookup failed; falling back to a full reload")

    # 1. MovieLens metadata + ratings
    meta_df, ml_genre_map = _load_movielens_meta(data_dir)
    if meta_df is None:
        return None, catalog

    ratings_file = data_dir / "ratings.csv"
    ratings = pd.read_csv(
        ratings_file,
        usecols=["userId", "movieId", "rating", "timestamp"],
        dtype={"userId": "int32", "movieId": "int32", "rating": "float32", "timestamp": "int64"},
    )
    df = pd.merge(ratings, meta_df, on="movieId", how="inner")
    del ratings, meta_df
    gc.collect()

    df = df.rename(columns={"userId": "user_id", "tmdbId": "tmdb_id"})
    df["user_id"] = "ml_" + df["user_id"].astype(str)
    df["source"] = "ml"
    df = df[["user_id", "tmdb_id", "rating", "timestamp", "year", "source"]]

    # Seed catalog genres from MovieLens-derived map
    for tmdb_id, genres in ml_genre_map.items():
        if genres:
            catalog.tmdb_to_genres[int(tmdb_id)] = list(genres)

    # 2. TMDB catalog enrichment
    tmdb_catalog = _load_tmdb_catalog(data_dir)
    if tmdb_catalog is not None:
        # Override genres with native TMDB genres where available
        for row in tmdb_catalog.itertuples(index=False):
            tid = int(row.tmdb_id)
            tmdb_genres = _parse_tmdb_genres(row.genres)
            if tmdb_genres:
                catalog.tmdb_to_genres[tid] = tmdb_genres
            catalog.tmdb_to_language[tid] = str(row.original_language)
            catalog.tmdb_to_runtime_bucket[tid] = str(row.runtime_bucket)
            catalog.tmdb_vote_data[tid] = (float(row.vote_average), int(row.vote_count))

        if released_only:
            released_ids = set(tmdb_catalog.loc[tmdb_catalog["status"] == "Released", "tmdb_id"])
            before = len(df)
            df = df[df["tmdb_id"].isin(released_ids)]
            logger.info("Status filter: %d -> %d ratings (Released only)", before, len(df))
        del tmdb_catalog
        gc.collect()

    # 3. Local reviews
    local_df = _load_local_reviews(catalog)
    if not local_df.empty:
        logger.info("Adding %d local reviews from %d users", len(local_df), local_df["user_id"].nunique())
        df = pd.concat([df, local_df], ignore_index=True)
        del local_df

    # 3b. Director/cast lookups for cold-start features (local DB only, sparse)
    _load_person_lookups(catalog)

    # 4. Per-(user, item) dedup, keep latest
    before = len(df)
    df = df.sort_values("timestamp").drop_duplicates(["user_id", "tmdb_id"], keep="last")
    if before != len(df):
        logger.info("Dedup: %d -> %d ratings (kept latest per user/item)", before, len(df))

    # 5. Pruning — users
    if min_user_ratings > 0:
        user_counts = df["user_id"].value_counts()
        keep_users = user_counts[user_counts >= min_user_ratings].index
        before = len(df)
        df = df[df["user_id"].isin(keep_users)]
        logger.info("User prune (>=%d ratings): %d -> %d", min_user_ratings, before, len(df))

    # 6. Pruning — items
    if min_item_ratings > 0:
        item_counts = df["tmdb_id"].value_counts()
        keep_items = item_counts[item_counts >= min_item_ratings].index
        before = len(df)
        df = df[df["tmdb_id"].isin(keep_items)]
        logger.info("Item prune (>=%d ratings): %d -> %d", min_item_ratings, before, len(df))

    # 7. Derived columns
    df["decade"] = (df["year"].astype("int32") // 10 * 10).astype("int32")
    df["language"] = df["tmdb_id"].map(catalog.tmdb_to_language).fillna("en").astype(str)
    df["runtime_bucket"] = (
        df["tmdb_id"].map(catalog.tmdb_to_runtime_bucket).fillna("standard").astype(str)
    )

    # Materialize the genres column as list[str] (TMDB names) for downstream masks
    df["genres"] = df["tmdb_id"].map(catalog.tmdb_to_genres)
    df["genres"] = df["genres"].apply(lambda v: v if isinstance(v, list) else [])

    # year-only lookup
    catalog.tmdb_to_year = (
        df[["tmdb_id", "year"]].drop_duplicates("tmdb_id").set_index("tmdb_id")["year"].astype(int).to_dict()
    )

    # Compact repeated low-cardinality string columns to categorical dtype. With
    # ~32M rows over a few hundred thousand users, storing user_id/language/
    # runtime_bucket as plain object (str) columns means millions of duplicate
    # Python str objects — categorical stores them once plus an int32 code per row.
    df["user_id"] = df["user_id"].astype("category")
    df["language"] = df["language"].astype("category")
    df["runtime_bucket"] = df["runtime_bucket"].astype("category")

    df = df.reset_index(drop=True)

    if use_cache and cache_key is not None:
        try:
            cache_dir = _dataset_cache_dir()
            df.to_parquet(cache_dir / f"{cache_key}.parquet", index=False)
            with open(cache_dir / f"{cache_key}.catalog.pkl", "wb") as fh:
                pickle.dump(catalog, fh)
            logger.info("Cached assembled training dataset (key=%s)", cache_key)
        except Exception:
            logger.exception("Failed to write dataset cache (continuing without it)")

    return df, catalog
