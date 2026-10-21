"""Data service behind the People Explorer page (``/people/``).

How it works
------------
1. **Credit index** (shared, cached): for every media item that has at least
   one review or watchlist entry, collect who is credited on it and in which
   *category* (director, actor, writer, ...). Album artists are included even
   though albums link people through ``Album.primary_artist`` /
   ``Album.featured_artists`` instead of ``MediaPerson``. The cache key is a
   fingerprint of that media set, so the index is only rebuilt when a title
   gets its first review / watchlist entry (or after ``CREDIT_INDEX_TTL``, to
   pick up credit syncs). It lives both in-process and in the shared cache.
2. **Rating context** (per request, cheap GROUP BY): per-media community
   averages, the current user's own ratings and the user's watchlist.
3. **Aggregation** (in Python): per person *and* per category, so a director
   is rated only on films they directed, not on films they merely acted in.

People are ranked by a Bayesian average by default so somebody with a single
10/10 film does not outrank a director with twenty 8/10 films.

Works are identified by a packed int key ``(content_type_id << 32) | object_id``
to keep the cached index small.
"""
from __future__ import annotations

import hashlib
import threading
import time
import unicodedata
from array import array
from dataclasses import dataclass
from typing import Iterable

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db import connection
from django.db.models import Avg, Count
from django.urls import reverse

from explorer.utils import build_thumbnail

ALBUM_ARTIST_ROLE = "Artist"
ALBUM_FEATURED_ROLE = "Featured Artist"

#: Category -> MediaPerson roles counted for it. ``None`` = every credit.
CATEGORY_ROLES: dict[str, frozenset[str] | None] = {
    "all": None,
    "directors": frozenset({"Director"}),
    "actors": frozenset({"Actor"}),
    "writers": frozenset({
        "Screenplay", "Writer", "Story", "Original Story",
        "Novel", "Comic Book", "Graphic Novel", "Book",
    }),
    "composers": frozenset({"Original Music Composer"}),
    "tv_creators": frozenset({"Creator"}),
    "authors": frozenset({"Author"}),
    "musicians": frozenset({ALBUM_ARTIST_ROLE, ALBUM_FEATURED_ROLE}),
}
CATEGORY_LABELS = {
    "directors": "Director",
    "actors": "Actor",
    "writers": "Writer",
    "composers": "Composer",
    "tv_creators": "TV Creator",
    "authors": "Author",
    "musicians": "Musician",
}
#: Old category keys still accepted by the API.
CATEGORY_ALIASES = {"other_creators": "authors"}
DEFAULT_CATEGORY = "directors"

#: Bucket for credits that do not belong to any named category
#: (e.g. "Executive Producer"); only counted under ``all``.
_OTHER = "_other"

_ROLE_TO_CATEGORY = {
    role: category
    for category, roles in CATEGORY_ROLES.items()
    if roles
    for role in roles
}

SCOPES = ("all", "mine", "watchlist")

SORTS = (
    "score", "rating", "rating-asc", "my-rating", "affinity",
    "seen", "works", "reviews", "watchlist", "name", "name-desc",
)
SORT_ALIASES = {"rating-desc": "score", "name-asc": "name"}
DEFAULT_SORT = "score"

#: Prior weight (in works) for the Bayesian average.
BAYESIAN_PRIOR_WORKS = 2
KNOWN_FOR_LIMIT = 4
CREDIT_INDEX_TTL = 30 * 60
_CACHE_PREFIX = "people_explorer:v3:index:"

_local_lock = threading.Lock()
_local_index: tuple[str, float, "CreditIndex"] | None = None


def work_key(content_type_id: int, object_id: int) -> int:
    return (content_type_id << 32) | object_id


def split_work_key(key: int) -> tuple[int, int]:
    return key >> 32, key & 0xFFFFFFFF


@dataclass(slots=True)
class PersonInfo:
    name: str
    picture: str | None
    birth_year: int | None
    death_year: int | None
    media_count: int
    search_key: str


@dataclass(slots=True)
class CreditIndex:
    #: person_id -> category (or ``_OTHER``) -> work keys
    works: dict[int, dict[str, frozenset[int]]]
    people: dict[int, PersonInfo]


@dataclass(slots=True)
class RatingContext:
    #: work -> (average rating, review count), over every user
    media: dict[int, tuple[float, int]]
    #: work -> (average rating, review count) for the current user
    mine: dict[int, tuple[float, int]]
    watchlist: set[int]
    global_mean: float


def normalize_text(value: str) -> str:
    """Casefold and strip accents so "inarritu" matches "Iñárritu"."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def resolve_category(value: str | None) -> str:
    value = CATEGORY_ALIASES.get(value, value)
    return value if value in CATEGORY_ROLES else DEFAULT_CATEGORY


def resolve_scope(value: str | None) -> str:
    return value if value in SCOPES else "all"


def resolve_sort(value: str | None) -> str:
    value = SORT_ALIASES.get(value, value)
    return value if value in SORTS else DEFAULT_SORT


# --------------------------------------------------------------------------
# Credit index
# --------------------------------------------------------------------------

def _chunk_size() -> int:
    # SQLite caps bound parameters per statement; Postgres is far more lenient.
    return 500 if connection.vendor == "sqlite" else 5000


def _chunks(values: list, size: int) -> Iterable[list]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _relevant_media() -> dict[int, set[int]]:
    """content_type_id -> object ids that have a review or a watchlist entry."""
    from custom_auth.models import Review, Watchlist

    relevant: dict[int, set[int]] = {}
    for model in (Review, Watchlist):
        # `.order_by()` drops Meta.ordering, otherwise DISTINCT includes it.
        pairs = model.objects.order_by().values_list("content_type_id", "object_id").distinct()
        for ct_id, obj_id in pairs:
            relevant.setdefault(ct_id, set()).add(obj_id)
    return relevant


def _fingerprint(relevant: dict[int, set[int]]) -> str:
    keys = sorted(work_key(ct, oid) for ct, ids in relevant.items() for oid in ids)
    return hashlib.blake2b(array("q", keys).tobytes(), digest_size=10).hexdigest()


def _build_credit_index(relevant: dict[int, set[int]]) -> CreditIndex:
    from custom_auth.models import MediaPerson, Person
    from music.models import Album

    size = _chunk_size()
    works: dict[int, dict[str, set[int]]] = {}
    album_ct = ContentType.objects.get_for_model(Album).pk

    def add(person_id: int | None, role: str, key: int) -> None:
        if person_id is None:
            return
        bucket = _ROLE_TO_CATEGORY.get(role, _OTHER)
        works.setdefault(person_id, {}).setdefault(bucket, set()).add(key)

    for ct_id, object_ids in relevant.items():
        ids = sorted(object_ids)
        base = ct_id << 32
        for chunk in _chunks(ids, size):
            rows = (
                MediaPerson.objects.order_by()
                .filter(content_type_id=ct_id, object_id__in=chunk)
                .values_list("person_id", "object_id", "role")
            )
            for person_id, obj_id, role in rows.iterator(chunk_size=5000):
                add(person_id, role, base | obj_id)

        if ct_id == album_ct:
            featured = Album.featured_artists.through.objects
            for chunk in _chunks(ids, size):
                for album_id, artist_id in Album.objects.filter(id__in=chunk).values_list("id", "primary_artist_id"):
                    add(artist_id, ALBUM_ARTIST_ROLE, base | album_id)
                for album_id, artist_id in featured.filter(album_id__in=chunk).values_list("album_id", "person_id"):
                    add(artist_id, ALBUM_FEATURED_ROLE, base | album_id)

    people: dict[int, PersonInfo] = {}
    for chunk in _chunks(list(works), size):
        rows = Person.objects.order_by().filter(id__in=chunk).values_list(
            "id", "name", "profile_picture", "date_of_birth", "date_of_death", "media_count",
        )
        for pid, name, picture, born, died, media_count in rows:
            people[pid] = PersonInfo(
                name=name,
                picture=build_thumbnail(picture, "w185"),
                birth_year=born.year if born else None,
                death_year=died.year if died else None,
                media_count=media_count,
                search_key=normalize_text(name),
            )

    # Credits pointing at people that no longer exist are dropped.
    frozen = {
        pid: {bucket: frozenset(keys) for bucket, keys in buckets.items()}
        for pid, buckets in works.items()
        if pid in people
    }
    return CreditIndex(works=frozen, people=people)


def get_credit_index() -> CreditIndex:
    """Return the credit index, rebuilding it only when the media set changed."""
    global _local_index

    relevant = _relevant_media()
    key = _CACHE_PREFIX + _fingerprint(relevant)
    now = time.monotonic()

    local = _local_index
    if local is not None and local[0] == key and local[1] > now:
        return local[2]

    with _local_lock:
        local = _local_index
        if local is not None and local[0] == key and local[1] > now:
            return local[2]
        index = cache.get(key)
        if index is None:
            index = _build_credit_index(relevant)
            cache.set(key, index, CREDIT_INDEX_TTL)
        _local_index = (key, now + CREDIT_INDEX_TTL, index)
        return index


def clear_local_index() -> None:
    """Drop the in-process copy (used by tests)."""
    global _local_index
    _local_index = None


# --------------------------------------------------------------------------
# Ratings
# --------------------------------------------------------------------------

def get_rating_context(user) -> RatingContext:
    from custom_auth.models import Review, Watchlist

    def grouped(qs) -> dict[int, tuple[float, int]]:
        rows = (
            qs.order_by()
            .values("content_type_id", "object_id")
            .annotate(avg=Avg("rating"), cnt=Count("id"))
        )
        return {
            work_key(row["content_type_id"], row["object_id"]): (row["avg"], row["cnt"])
            for row in rows
        }

    media = grouped(Review.objects.all())
    mine: dict[int, tuple[float, int]] = {}
    watchlist: set[int] = set()
    if user is not None and user.is_authenticated:
        mine = grouped(Review.objects.filter(user=user))
        watchlist = {
            work_key(ct, oid)
            for ct, oid in Watchlist.objects.order_by().filter(user=user)
            .values_list("content_type_id", "object_id")
        }
    global_mean = sum(avg for avg, _ in media.values()) / len(media) if media else 0.0
    return RatingContext(media=media, mine=mine, watchlist=watchlist, global_mean=global_mean)


@dataclass(slots=True)
class PersonStats:
    works: frozenset[int]
    works_rated: int = 0
    rating_count: int = 0
    avg_rating: float | None = None
    score: float | None = None
    my_count: int = 0
    my_avg: float | None = None
    #: Mean of (my rating - everyone else's average) on shared works.
    affinity: float | None = None
    #: Affinity shrunk towards 0 for people with few shared works (sort key).
    affinity_rank: float | None = None
    watchlist_count: int = 0


def summarize(works: frozenset[int], ctx: RatingContext) -> PersonStats:
    """Aggregate ratings over a set of works (each work weighs the same)."""
    stats = PersonStats(works=works)
    rating_sum = 0.0
    my_sum = 0.0
    delta_sum = 0.0
    delta_n = 0
    media_get = ctx.media.get
    mine_get = ctx.mine.get
    watchlist = ctx.watchlist
    for key in works:
        media = media_get(key)
        if media is None:
            if key in watchlist:
                stats.watchlist_count += 1
            continue
        avg, cnt = media
        stats.works_rated += 1
        stats.rating_count += cnt
        rating_sum += avg
        mine = mine_get(key)
        if mine is not None:
            my_avg, my_cnt = mine
            stats.my_count += 1
            my_sum += my_avg
            others = cnt - my_cnt
            if others > 0:
                delta_sum += my_avg - (avg * cnt - my_avg * my_cnt) / others
                delta_n += 1
        if key in watchlist:
            stats.watchlist_count += 1
    if stats.works_rated:
        stats.avg_rating = rating_sum / stats.works_rated
        prior = BAYESIAN_PRIOR_WORKS
        stats.score = (rating_sum + prior * ctx.global_mean) / (stats.works_rated + prior)
    if stats.my_count:
        stats.my_avg = my_sum / stats.my_count
    if delta_n:
        stats.affinity = delta_sum / delta_n
        stats.affinity_rank = delta_sum / (delta_n + 1)
    return stats


def _works_for(buckets: dict[str, frozenset[int]], category: str) -> frozenset[int]:
    if category == "all":
        if len(buckets) == 1:
            return next(iter(buckets.values()))
        return frozenset().union(*buckets.values())
    return buckets.get(category, frozenset())


_LAST = float("-inf")


def _sort_rows(rows: list[tuple[int, PersonInfo, PersonStats]], sort: str) -> None:
    def num(value):
        return _LAST if value is None else value

    def by_name(row):
        return row[1].search_key

    rows.sort(key=by_name)  # stable secondary order
    if sort == "name":
        return
    if sort == "name-desc":
        rows.sort(key=by_name, reverse=True)
        return
    if sort == "rating-asc":
        rows.sort(key=lambda r: (r[2].avg_rating is None, r[2].avg_rating or 0))
        return
    keyfuncs = {
        "score": lambda r: num(r[2].score),
        "rating": lambda r: (num(r[2].avg_rating), r[2].works_rated),
        "my-rating": lambda r: (num(r[2].my_avg), r[2].my_count),
        "affinity": lambda r: (num(r[2].affinity_rank), r[2].my_count),
        "seen": lambda r: (r[2].my_count, num(r[2].my_avg)),
        "works": lambda r: (r[2].works_rated, num(r[2].score)),
        "reviews": lambda r: (r[2].rating_count, num(r[2].score)),
        "watchlist": lambda r: (r[2].watchlist_count, num(r[2].score)),
    }
    rows.sort(key=keyfuncs[sort], reverse=True)


# --------------------------------------------------------------------------
# Known-for strip
# --------------------------------------------------------------------------

_MEDIA_FIELDS = {
    # "app_label.model": (image field, date field, type label)
    "movies.movie": ("poster", "release_date", "Movie"),
    "tvshows.tvshow": ("poster", "first_air_date", "TV"),
    "books.book": ("image_url", "published_date", "Book"),
    "games.game": ("poster", "release_date", "Game"),
    "music.album": ("cover", "release_date", "Album"),
}


def _media_url(label: str, obj) -> str | None:
    if label == "books.book":
        return reverse("books:book_page", kwargs={"book_id": obj.pk})
    get_url = getattr(obj, "get_absolute_url", None)
    return get_url() if get_url else None


def _load_media(keys: set[int]) -> dict[int, dict]:
    by_ct: dict[int, set[int]] = {}
    for key in keys:
        ct_id, obj_id = split_work_key(key)
        by_ct.setdefault(ct_id, set()).add(obj_id)

    loaded: dict[int, dict] = {}
    for ct_id, ids in by_ct.items():
        ct = ContentType.objects.get_for_id(ct_id)
        label = f"{ct.app_label}.{ct.model}"
        model = ct.model_class()
        if model is None or label not in _MEDIA_FIELDS:
            continue
        image_field, date_field, type_label = _MEDIA_FIELDS[label]
        only = {"id", "title", image_field, date_field}
        if any(f.name == "tmdb_id" for f in model._meta.fields):
            only.add("tmdb_id")
        for obj in model.objects.filter(id__in=ids).only(*only):
            when = getattr(obj, date_field, None)
            loaded[work_key(ct_id, obj.pk)] = {
                "title": obj.title,
                "year": when.year if when else None,
                "poster": build_thumbnail(getattr(obj, image_field, None), "w154"),
                "url": _media_url(label, obj),
                "type": type_label,
            }
    return loaded


def _known_for(stats: PersonStats, ctx: RatingContext, scope: str) -> list[int]:
    def key(work):
        avg, cnt = ctx.media.get(work, (0.0, 0))
        if scope == "mine":
            mine = ctx.mine.get(work)
            return (mine is not None, mine[0] if mine else 0, cnt, avg)
        if scope == "watchlist":
            return (work in ctx.watchlist, cnt, avg)
        return (cnt, avg)

    return sorted(stats.works, key=key, reverse=True)[:KNOWN_FOR_LIMIT]


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def build_listing(
    user,
    *,
    category: str | None = DEFAULT_CATEGORY,
    scope: str | None = "all",
    search: str = "",
    sort: str | None = DEFAULT_SORT,
    min_works: int = 1,
    page: int = 1,
    per_page: int = 30,
) -> dict:
    """Return one page of the People Explorer plus per-category counts.

    ``counts`` honours the scope, search and ``min_works`` filters so the tabs
    show how many people each category would list.
    """
    category = resolve_category(category)
    scope = resolve_scope(scope)
    sort = resolve_sort(sort)
    tokens = normalize_text(search).split()

    index = get_credit_index()
    ctx = get_rating_context(user)

    # Works that make a person show up in the current scope. Counting via C-level
    # set intersection keeps the full `summarize` off the hot path: it only runs
    # for people listed in the selected category.
    if scope == "mine":
        scope_keys = set(ctx.mine)
    elif scope == "watchlist":
        scope_keys = ctx.watchlist
    else:
        scope_keys = set(ctx.media)
    needed = max(min_works, 1)

    counts = dict.fromkeys(CATEGORY_ROLES, 0)
    rows: list[tuple[int, PersonInfo, PersonStats]] = []
    for pid, buckets in index.works.items():
        info = index.people[pid]
        if tokens and not all(token in info.search_key for token in tokens):
            continue
        for cat in ("all", *(b for b in buckets if b != _OTHER)):
            works = _works_for(buckets, cat)
            if needed == 1:
                if works.isdisjoint(scope_keys):
                    continue
            elif len(works & scope_keys) < needed:
                continue
            counts[cat] += 1
            if cat == category:
                rows.append((pid, info, summarize(works, ctx)))

    _sort_rows(rows, sort)

    total = len(rows)
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]

    known = {pid: _known_for(stats, ctx, scope) for pid, _, stats in page_rows}
    media = _load_media({work for works in known.values() for work in works})

    people = []
    for pid, info, stats in page_rows:
        buckets = index.works[pid]
        known_for = []
        for work in known[pid]:
            item = media.get(work)
            if item is None:
                continue
            community = ctx.media.get(work)
            mine = ctx.mine.get(work)
            known_for.append({
                **item,
                "avg_rating": _round(community[0]) if community else None,
                "my_rating": _round(mine[0]) if mine else None,
                "on_watchlist": work in ctx.watchlist,
            })
        people.append({
            "id": pid,
            "name": info.name,
            "profile_picture": info.picture,
            "birth_year": info.birth_year,
            "death_year": info.death_year,
            "roles": [label for cat, label in CATEGORY_LABELS.items() if cat in buckets],
            "num_works": info.media_count,
            "works_rated": stats.works_rated,
            "rating_count": stats.rating_count,
            "avg_rating": _round(stats.avg_rating),
            "score": _round(stats.score, 2),
            "my_count": stats.my_count,
            "my_avg": _round(stats.my_avg),
            "affinity": _round(stats.affinity),
            "watchlist_count": stats.watchlist_count,
            "known_for": known_for,
        })

    return {
        "success": True,
        "category": category,
        "scope": scope,
        "sort": sort,
        "people": people,
        "counts": counts,
        "global_mean": _round(ctx.global_mean, 2),
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page,
            "has_more": start + per_page < total,
        },
    }
