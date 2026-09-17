"""Local TMDB CSV enrichment for the network graph.

Reads popularity/budget/revenue/studio data straight from the user-maintained
``data/TMDB_movie_dataset_v11.csv`` Kaggle dump, keyed by ``tmdb_id``. This never
hits the network - it only enriches graph nodes with numbers we don't persist
on the Movie model. Parsing the multi-hundred-MB CSV is cached to disk, keyed by
the file's size+mtime, so it's only redone when the user replaces the dataset.
"""

import hashlib
import logging
import pickle
from pathlib import Path
from typing import Dict, List, Optional, TypedDict

from django.conf import settings

logger = logging.getLogger(__name__)

_CACHE_SCHEMA_VERSION = 1


class MovieEnrichment(TypedDict):
    popularity: float
    budget: int
    revenue: int
    studios: List[str]


def _csv_path() -> Path:
    return Path(settings.BASE_DIR) / "data" / "TMDB_movie_dataset_v11.csv"


def _cache_dir() -> Path:
    path = Path(settings.BASE_DIR) / "data" / ".network_graph_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(csv_file: Path) -> Optional[str]:
    if not csv_file.exists():
        return None
    stat = csv_file.stat()
    fingerprint = f"schema={_CACHE_SCHEMA_VERSION}|{csv_file.name}:{stat.st_size}:{int(stat.st_mtime)}"
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]


def load_movie_enrichment() -> Dict[int, MovieEnrichment]:
    """Return a ``tmdb_id -> {popularity, budget, revenue, studios}`` lookup.

    Returns an empty dict (with a warning logged) if the CSV is missing, so
    callers can treat enrichment as a pure best-effort bonus.
    """
    csv_file = _csv_path()
    cache_key = _cache_key(csv_file)
    if cache_key is None:
        logger.warning("TMDB enrichment CSV not found at %s - skipping enrichment", csv_file)
        return {}

    cache_file = _cache_dir() / f"{cache_key}.pkl"
    if cache_file.exists():
        try:
            with open(cache_file, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            logger.exception("Failed to load enrichment cache; rebuilding from CSV")

    try:
        import pandas as pd

        df = pd.read_csv(
            csv_file,
            usecols=["id", "popularity", "budget", "revenue", "production_companies"],
            dtype={"id": "float64"},
        ).dropna(subset=["id"])
    except Exception:
        logger.exception("Failed to read TMDB enrichment CSV at %s", csv_file)
        return {}

    df["id"] = df["id"].astype("int64")
    df["popularity"] = pd.to_numeric(df["popularity"], errors="coerce").fillna(0.0)
    df["budget"] = pd.to_numeric(df["budget"], errors="coerce").fillna(0).astype("int64")
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0).astype("int64")

    lookup: Dict[int, MovieEnrichment] = {}
    for row in df.itertuples(index=False):
        studios: List[str] = []
        companies = getattr(row, "production_companies", None)
        if isinstance(companies, str) and companies.strip():
            studios = [name.strip() for name in companies.split(",") if name.strip()][:3]
        lookup[int(row.id)] = {
            "popularity": float(row.popularity),
            "budget": int(row.budget),
            "revenue": int(row.revenue),
            "studios": studios,
        }

    try:
        with open(cache_file, "wb") as fh:
            pickle.dump(lookup, fh)
        for stale in _cache_dir().glob("*.pkl"):
            if stale.name != cache_file.name:
                stale.unlink(missing_ok=True)
    except Exception:
        logger.exception("Failed to write enrichment cache (continuing without it)")

    logger.info("Loaded TMDB enrichment for %d movies from %s", len(lookup), csv_file.name)
    return lookup
