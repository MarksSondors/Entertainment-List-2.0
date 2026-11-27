"""IMDb's public rating for every movie and TV show we have an IMDb id for.

Source: IMDb's own dataset, https://datasets.imdbws.com/title.ratings.tsv.gz (no key, regenerated
daily, roughly 1.6M rows). One download covers every title, so the sync is one request a day rather
than one per title. IMDb licenses the datasets for personal, non-commercial use only.

A failed or partial download writes nothing, and a title missing from the file keeps the rating it
already has: a blank never replaces a stored number.
"""
from __future__ import annotations

import gzip
import logging
from decimal import Decimal, InvalidOperation

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

RATINGS_URL = 'https://datasets.imdbws.com/title.ratings.tsv.gz'
TIMEOUT = (10, 120)  # connect, read
# The real file has ~1.6M rows. Far fewer means a truncated or wrong file, not real data.
MIN_ROWS = 100_000
HEADER = ['tconst', 'averageRating', 'numVotes']
FIELDS = ['imdb_rating', 'imdb_votes', 'imdb_rating_updated']
TENTH = Decimal('0.1')


def parse_ratings(fileobj, wanted: set[str]) -> tuple[dict[str, tuple[Decimal, int]], int]:
    """Read the gzipped TSV and return ``({imdb_id: (rating, votes)}, total_rows)`` for ``wanted`` ids."""
    found: dict[str, tuple[Decimal, int]] = {}
    rows = 0
    with gzip.open(fileobj, 'rt', encoding='utf-8', newline='') as text:
        header = next(text, '').rstrip('\r\n').split('\t')
        if header[:3] != HEADER:
            raise ValueError(f'unexpected header {header!r}')
        for line in text:
            rows += 1
            imdb_id, _, rest = line.partition('\t')
            if imdb_id not in wanted:
                continue
            rating, _, votes = rest.rstrip('\r\n').partition('\t')
            try:
                value, count = Decimal(rating).quantize(TENTH), int(votes)
            except (InvalidOperation, ValueError):
                continue
            if Decimal('1') <= value <= Decimal('10') and count >= 0:
                found[imdb_id] = (value, count)
    return found, rows


def _targets():
    """``{imdb_id: [Movie/TVShow rows]}`` for every title with an IMDb id."""
    from movies.models import Movie
    from tvshows.models import TVShow

    targets: dict[str, list] = {}
    for model in (Movie, TVShow):
        rows = (model.objects.exclude(imdb_id__isnull=True).exclude(imdb_id='')
                .only('id', 'imdb_id', *FIELDS))
        for obj in rows:
            targets.setdefault(obj.imdb_id.strip(), []).append(obj)
    return targets


def sync_imdb_ratings(fileobj=None, min_rows: int = MIN_ROWS) -> dict:
    """Download IMDb's ratings file and store the rating and vote count on matching titles.

    ``fileobj`` (a gzipped TSV) replaces the download, for tests. Only rows whose values changed are
    written, via ``bulk_update``, which leaves ``date_updated`` alone so the TMDB refresh rotation
    (oldest ``date_updated`` first) is unaffected.
    """
    targets = _targets()
    response = None
    try:
        if fileobj is None:
            response = requests.get(RATINGS_URL, stream=True, timeout=TIMEOUT)
            response.raise_for_status()
            response.raw.decode_content = False  # the body is the .gz file itself
            fileobj = response.raw
        found, rows = parse_ratings(fileobj, set(targets))
    except (requests.RequestException, OSError, EOFError, ValueError) as exc:
        logger.warning("IMDb ratings sync skipped, download or parse failed: %s", exc)
        return {'error': str(exc)}
    finally:
        if response is not None:
            response.close()

    if rows < min_rows:
        logger.warning("IMDb ratings sync skipped: file had %s rows, expected at least %s", rows, min_rows)
        return {'error': f'only {rows} rows', 'rows': rows}

    now = timezone.now()
    changed: dict[type, list] = {}
    for imdb_id, objs in targets.items():
        hit = found.get(imdb_id)
        if hit is None:  # not rated on IMDb (yet): keep what we have
            continue
        rating, votes = hit
        for obj in objs:
            if obj.imdb_rating != rating or obj.imdb_votes != votes:
                obj.imdb_rating, obj.imdb_votes, obj.imdb_rating_updated = rating, votes, now
                changed.setdefault(type(obj), []).append(obj)

    result = {'rows': rows, 'matched': len(found), 'titles': len(targets)}
    for model, objs in changed.items():
        model.objects.bulk_update(objs, FIELDS, batch_size=500)
        result[f'updated_{model._meta.model_name}'] = len(objs)
    logger.info("IMDb ratings sync: %s", result)
    return result


def imdb_rating_text(media) -> str | None:
    """The stored rating as Stremio's ``imdbRating`` string (``'8.4'``), or None."""
    rating = getattr(media, 'imdb_rating', None)
    return f'{rating:.1f}' if rating is not None else None
