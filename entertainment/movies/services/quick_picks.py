"""Selection logic behind the Discover page's "Quickest Watch" and "Random Movie" actions.

Both pickers return a :class:`QuickPick` so the JSON endpoint (which powers the
in-page preview dialog) and the plain redirect views (no-JS fallback) always
agree on which movie is served.

* Quickest Watch: the shortest released movie on the user's watchlist that they
  have not reviewed yet. Without a usable watchlist it falls back to the
  shortest unwatched movie in the catalogue. Repeated calls with ``exclude_ids``
  walk the list in runtime order ("Next shortest").
* Random Movie: a random released movie the user has not reviewed, preferring
  movies nobody in the community has reviewed, then the least-reviewed ones.
"""

from dataclasses import dataclass, field

from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, IntegerField, Min, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

from custom_auth.models import Review, Watchlist
from movies.models import Movie

MODE_QUICKEST = 'quickest'
MODE_RANDOM = 'random'
MODES = (MODE_QUICKEST, MODE_RANDOM)

MAX_EXCLUDED_IDS = 50
POSTER_SIZE = 'w342'


@dataclass
class QuickPick:
    movie: Movie
    mode: str
    source: str  # 'watchlist' | 'catalog' | 'unwatched' | 'least_watched'
    reason: str
    position: int | None = None
    pool_size: int = 0
    wrapped: bool = False
    extra: dict = field(default_factory=dict)


def parse_exclude_ids(raw):
    """Parse a comma-separated id list from the query string, ignoring junk."""
    ids = []
    for part in (raw or '').split(','):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
        if len(ids) >= MAX_EXCLUDED_IDS:
            break
    return ids


def _movie_ct():
    return ContentType.objects.get_for_model(Movie)


def _user_reviewed_ids(user, ct):
    return Review.objects.filter(user=user, content_type=ct).order_by().values('object_id')


def pick_quickest_movie(user, exclude_ids=()):
    ct = _movie_ct()
    candidates = (
        Movie.objects.filter(status='Released', runtime__gt=0)
        .exclude(id__in=_user_reviewed_ids(user, ct))
    )
    watchlist_ids = (
        Watchlist.objects.filter(user=user, content_type=ct).order_by().values('object_id')
    )
    on_watchlist = candidates.filter(id__in=watchlist_ids)

    if on_watchlist.exists():
        pool, source = on_watchlist, 'watchlist'
    else:
        pool, source = candidates, 'catalog'

    ordered = pool.order_by('runtime', 'title', 'id')
    exclude_ids = list(exclude_ids)
    movie = ordered.exclude(id__in=exclude_ids).first() if exclude_ids else ordered.first()
    wrapped = False
    if movie is None:
        # Everything was already shown in this session — start over from the top.
        movie = ordered.first()
        wrapped = bool(exclude_ids)
    if movie is None:
        return None

    pool_size = pool.count()
    position = ordered.filter(runtime__lt=movie.runtime).count() + 1
    if source == 'watchlist':
        reason = f"#{position} shortest of {pool_size} unwatched on your watchlist"
    else:
        reason = "Your watchlist is empty, so here is the shortest unwatched movie in the library"
    return QuickPick(
        movie=movie, mode=MODE_QUICKEST, source=source, reason=reason,
        position=position, pool_size=pool_size, wrapped=wrapped,
    )


def pick_random_movie(user, exclude_ids=()):
    ct = _movie_ct()
    review_count = (
        Review.objects.filter(content_type=ct, object_id=OuterRef('pk'))
        .order_by().values('object_id').annotate(n=Count('id')).values('n')
    )
    base = (
        Movie.objects.filter(status='Released')
        .exclude(id__in=_user_reviewed_ids(user, ct))
        .annotate(review_count=Coalesce(
            Subquery(review_count, output_field=IntegerField()), Value(0),
        ))
    )
    exclude_ids = list(exclude_ids)
    pool = base.exclude(id__in=exclude_ids) if exclude_ids else base
    wrapped = False
    if not pool.exists():
        pool, wrapped = base, bool(exclude_ids)
    if not pool.exists():
        return None

    min_reviews = pool.aggregate(m=Min('review_count'))['m'] or 0
    tier = pool.filter(review_count=min_reviews)
    movie = tier.order_by('?').first()
    pool_size = tier.count()
    if min_reviews == 0:
        source = 'unwatched'
        reason = f"Nobody has reviewed this yet — 1 of {pool_size} untouched movies"
    else:
        source = 'least_watched'
        noun = 'review' if min_reviews == 1 else 'reviews'
        reason = f"One of the least-watched movies you haven't seen ({min_reviews} {noun})"
    return QuickPick(
        movie=movie, mode=MODE_RANDOM, source=source, reason=reason,
        pool_size=pool_size, wrapped=wrapped,
    )


def pick_movie(mode, user, exclude_ids=()):
    if mode == MODE_QUICKEST:
        return pick_quickest_movie(user, exclude_ids)
    if mode == MODE_RANDOM:
        return pick_random_movie(user, exclude_ids)
    raise ValueError(f"Unknown quick pick mode: {mode}")


def _sized_poster(url):
    if url and 'image.tmdb.org/t/p/' in url:
        return url.replace('/t/p/original/', f'/t/p/{POSTER_SIZE}/')
    return url or ''


def serialize_pick(pick, user):
    """Payload for the Discover preview dialog."""
    movie = pick.movie
    ct = _movie_ct()
    hours, minutes = divmod(movie.runtime or 0, 60)
    runtime_display = (f"{hours}h {minutes}m" if hours else f"{minutes}m") if movie.runtime else ''
    return {
        'mode': pick.mode,
        'source': pick.source,
        'reason': pick.reason,
        'position': pick.position,
        'pool_size': pick.pool_size,
        'wrapped': pick.wrapped,
        'movie': {
            'id': movie.id,
            'tmdb_id': movie.tmdb_id,
            'title': movie.title,
            'year': movie.release_date.year if movie.release_date else None,
            'runtime': movie.runtime,
            'runtime_display': runtime_display,
            'rating': round(movie.rating, 1) if movie.rating is not None else None,
            'poster': _sized_poster(movie.poster),
            'description': movie.description or '',
            'genres': list(movie.genres.order_by('name').values_list('name', flat=True)[:3]),
            'countries': list(movie.countries.order_by('name').values_list('name', flat=True)[:2]),
            'url': movie.get_absolute_url(),
            'on_watchlist': Watchlist.objects.filter(
                user=user, content_type=ct, object_id=movie.id).exists(),
            'watched': Review.objects.filter(
                user=user, content_type=ct, object_id=movie.id).exists(),
        },
    }
