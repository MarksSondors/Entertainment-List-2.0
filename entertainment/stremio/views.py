import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from types import SimpleNamespace
from typing import NamedTuple

from django.http import JsonResponse, HttpResponse, HttpResponseNotFound, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Avg, Case, Count, F, IntegerField, Max, Q, When
import requests

from .authentication import decode_config, get_user_from_config, is_rate_limited, require_stremio_auth
from .catalog_cache import catalog_generation
from .formatters import (
    FINISHED_TVSHOW_STATUSES, get_poster_url, resize_tmdb_image, to_stremio_catalog_item, to_stremio_meta,
)
from .poster import bulk_poster_states, content_version, get_cached_poster

from movies.models import Movie, MovieOfWeekPick
from tvshows.models import TVShow
from custom_auth.models import Watchlist, Review, Genre
from custom_auth.services.recommendation_cache import SCOPE_MOVIES, SCOPE_STREMIO, get_cached, set_cached
from movies.services.recommendation import MovieRecommender
from api.services.movies import MoviesService

logger = logging.getLogger(__name__)


# Constants
PAGE_SIZE = 100
MAX_SKIP = 10_000
SEARCH_LIMIT = 50
RECOMMENDATIONS_SIZE = 60
# The warm task refreshes these every 50 minutes, so the life has to be longer
# than that interval: with a 1 hour life the cache expired for ten minutes
# every hour, and the next addon request then paid the full model load
# (~7 s, measured up to 19 s in production).
RECOMMENDATIONS_CACHE_TTL = 7200
DISCOVER_EXTERNAL_CANDIDATE_COUNT = 60
DISCOVER_EXTERNAL_CACHE_TTL = 7200
CATALOG_CACHE_TTL = 120  # short TTL for DB-backed catalogs, since Stremio polls catalogs often
MANIFEST_GENRES_CACHE_TTL = 3600
WARM_DEDUPE_TTL = 900  # don't re-queue the same poster render more often than this
# These catalogs manage their own caching internally (or, for search, aren't worth caching)
NO_OUTER_CACHE_CATALOGS = {'recommendations', 'discover-external', 'search-movies', 'search-series'}
# Only these poster contexts change the rendered image; anything else is treated as no context
POSTER_CONTEXTS = {'cw'}
# Bump on any catalogs/config change so Stremio's client detects it and offers "Update" in Addons
MANIFEST_VERSION = '1.3.0'

# IMDb ratings come from Cinemeta (Stremio's own keyless metadata addon); the site only stores TMDB scores
CINEMETA_META_URL = 'https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json'
CINEMETA_TIMEOUT = 2.5
IMDB_RATING_TTL = 7 * 24 * 3600
IMDB_RATING_MISS_TTL = 24 * 3600      # Cinemeta has no rating for this title
IMDB_RATING_ERROR_TTL = 3600          # Cinemeta unreachable; retry sooner
_IMDB_RATING_RE = re.compile(r'\d{1,2}(?:\.\d)?')
# Genre links open the first of these the user has enabled ("Top Rated (Unseen)" first)
GENRE_LINK_CATALOG_KEYS = {
    'movie': ['top-rated-movies', 'watchlist-movies'],
    'series': ['top-rated-series', 'watchlist-series', 'waiting-for-new-episodes'],
}

CATALOG_HTTP_CACHE = 'private, max-age=60, stale-while-revalidate=300, stale-if-error=86400'
META_HTTP_CACHE = 'private, max-age=300, stale-if-error=86400'
MANIFEST_HTTP_CACHE = 'private, max-age=300'

# Stremio sends extra params as one path segment ("genre=Drama&skip=100")
_EXTRA_RE = re.compile(r'(?:^|&)(skip|genre|search)=(.*?)(?=&(?:skip|genre|search)=|$)')


class CatalogDef(NamedTuple):
    key: str  # what the config's "catalogs" list stores; unique even where `id` is shared across types
    id: str
    type: str
    name: str
    extra: str | None  # 'skip', 'filter' (genre + skip), 'search', or None


CATALOGS = [
    CatalogDef('continue-watching', 'continue-watching', 'series', 'Continue Watching', 'skip'),
    CatalogDef('watchlist-movies', 'watchlist-movies', 'movie', 'My Watchlist', 'filter'),
    CatalogDef('watchlist-series', 'watchlist-series', 'series', 'Not Started', 'filter'),
    CatalogDef('waiting-for-new-episodes', 'waiting-for-new-episodes', 'series', 'Waiting for New Episodes', 'filter'),
    CatalogDef('community-picks', 'community-picks', 'movie', 'Movie of the Week', 'skip'),
    CatalogDef('recommendations', 'recommendations', 'movie', 'Recommended For You', None),
    CatalogDef('discover-external', 'discover-external', 'movie', 'Discover', 'skip'),
    CatalogDef('top-rated-movies', 'top-rated', 'movie', 'Top Rated (Unseen)', 'filter'),
    CatalogDef('top-rated-series', 'top-rated', 'series', 'Top Rated (Unseen)', 'filter'),
    CatalogDef('search-movies', 'search-movies', 'movie', 'Entertainment List', 'search'),
    CatalogDef('search-series', 'search-series', 'series', 'Entertainment List', 'search'),
]


def catalog_choices() -> list[dict]:
    """Checkbox options for the configure/install pages."""
    return [
        {
            'key': c.key,
            'label': 'Search' if c.extra == 'search' else c.name,
            'type_label': 'Movies' if c.type == 'movie' else 'Series',
        }
        for c in CATALOGS
    ]


def enabled_catalog_keys(config_data: dict) -> set[str] | None:
    """Catalog keys the user picked, or None for "all" (older installs have no "catalogs" entry)."""
    selected = config_data.get('catalogs')
    if not isinstance(selected, list):
        return None
    return {key for key in selected if isinstance(key, str)}


def parse_extra(extra: str | None) -> dict:
    """Parse Stremio's `extra` path segment into skip/genre/search.

    Django has already percent-decoded the path, so a genre like "Sci-Fi & Fantasy" arrives with a
    bare "&" in it; only an "&" that starts another known key separates params.
    """
    parsed = {'skip': 0, 'genre': None, 'search': None}
    if not extra:
        return parsed
    for key, value in _EXTRA_RE.findall(extra):
        if key == 'skip':
            try:
                parsed['skip'] = max(0, min(int(value), MAX_SKIP))
            except ValueError:
                pass
        else:
            parsed[key] = value.strip() or None
    return parsed


def cors_response(data: dict, status: int = 200, cache_control: str | None = None) -> JsonResponse:
    """Create a JsonResponse with CORS headers for Stremio."""
    response = JsonResponse(data, status=status)
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type'
    if cache_control:
        response['Cache-Control'] = cache_control
    return response


def cors_preflight_response() -> HttpResponse:
    """Handle CORS preflight OPTIONS request."""
    response = HttpResponse()
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type'
    response['Access-Control-Max-Age'] = '86400'
    return response


def _poster_url(poster_base: str, media_type: str, imdb_id: str, ctx: str | None = None, v: str | None = None) -> str:
    """Build the overlay poster URL embedded into catalog items.

    `v` is a poster-art hash: changing it changes the URL, which busts Stremio's own
    image cache so updated posters show up on the next catalog refresh instead of
    whenever Stremio's long-lived cache decides to revalidate.
    """
    url = f"{poster_base}/{media_type}/{imdb_id}.png"
    params = []
    if ctx:
        params.append(f"ctx={ctx}")
    if v:
        params.append(f"v={v}")
    if params:
        url += "?" + "&".join(params)
    return url


def _get_by_imdb(model, imdb_id: str, *prefetch):
    """imdb_id isn't unique in the DB; pick the oldest row instead of raising MultipleObjectsReturned."""
    return model.objects.filter(imdb_id=imdb_id).prefetch_related(*prefetch).order_by('id').first()


def _has_imdb_id() -> Q:
    return ~(Q(imdb_id__isnull=True) | Q(imdb_id=''))


def configure(request, config: str = None):
    """
    Stremio configure page.
    Shows API key input and generates install URL.
    If config is provided, pre-fills the API key and catalog selection.
    """
    config_data = decode_config(config) if config else {}
    api_key = config_data.get('api_key', '')
    enabled = enabled_catalog_keys(config_data)

    return render(request, 'stremio/configure.html', {
        'base_url': request.build_absolute_uri('/stremio/'),
        'api_key': api_key if isinstance(api_key, str) else '',
        'catalog_choices': catalog_choices(),
        'enabled_catalogs': enabled,
        'manifest_version': MANIFEST_VERSION,
    })


def _manifest_genres(cache_key: str, genre_filter: Q) -> list[str]:
    names = cache.get(cache_key)
    if names is None:
        names = list(Genre.objects.filter(genre_filter).values_list('name', flat=True).distinct().order_by('name'))
        cache.set(cache_key, names, MANIFEST_GENRES_CACHE_TTL)
    return names


def _manifest_catalog(c: CatalogDef, genre_options: dict[str, list[str]]) -> dict:
    entry = {'id': c.id, 'name': c.name, 'type': c.type}
    if c.extra == 'skip':
        entry['extra'] = [{'name': 'skip', 'isRequired': False}]
    elif c.extra == 'filter':
        entry['extra'] = [
            {'name': 'genre', 'options': genre_options[c.type], 'isRequired': False},
            {'name': 'skip', 'isRequired': False},
        ]
    elif c.extra == 'search':
        entry['extra'] = [{'name': 'search', 'isRequired': True}]
    return entry


@csrf_exempt
def manifest(request, config: str = None):
    """
    Stremio manifest endpoint.
    Can be called with or without config for initial addon installation.
    When called with a valid config, configurationRequired is set to False
    so Stremio shows the Install button.
    """
    if request.method == 'OPTIONS':
        return cors_preflight_response()

    # Check if config is provided and valid
    is_configured = bool(config) and get_user_from_config(config) is not None
    enabled = enabled_catalog_keys(decode_config(config)) if config else None

    # Movies and TV shows don't share the same genre set, so keep separate cached option lists
    genre_options = {
        'movie': _manifest_genres('stremio_manifest_genres_movie', Q(movie__isnull=False)),
        'series': _manifest_genres('stremio_manifest_genres_series', Q(tvshow__isnull=False)),
    }

    manifest_data = {
        'id': 'com.entertainment-list.addon',
        'version': MANIFEST_VERSION,
        'name': 'Entertainment List',
        'description': 'Your personal entertainment tracking addon - watchlists, recommendations, and community picks',
        'logo': request.build_absolute_uri('/static/images/logo.png'),
        # player/library: Stremio sends playback and watched events to addons that declare them (#33)
        'resources': ['catalog', 'meta', 'player', 'library'],
        'types': ['movie', 'series'],
        'idPrefixes': ['tt'],
        'catalogs': [
            _manifest_catalog(c, genre_options)
            for c in CATALOGS
            if enabled is None or c.key in enabled
        ],
        'behaviorHints': {
            'configurable': True,
            'configurationRequired': not is_configured,  # False when valid config provided
        },
        'config': [
            {
                'key': 'api_key',
                'type': 'text',
                'title': 'API Key',
                'required': True,
            }
        ]
    }

    return cors_response(manifest_data, cache_control=MANIFEST_HTTP_CACHE)


@csrf_exempt
@require_stremio_auth
def catalog(request, config: str, media_type: str, catalog_id: str, extra: str = None):
    """
    Stremio catalog endpoint.
    Returns paginated list of media items for the specified catalog.
    """
    if request.method == 'OPTIONS':
        return cors_preflight_response()
    user = request.stremio_user

    params = parse_extra(extra)
    skip, genre, search = params['skip'], params['genre'], params['search']

    poster_base = request.build_absolute_uri(f'/stremio/{config}/poster')

    # Route to appropriate catalog handler
    catalog_handlers = {
        ('series', 'continue-watching'): lambda: get_continue_watching(user, poster_base, skip),
        ('movie', 'watchlist-movies'): lambda: get_watchlist_movies(user, poster_base, skip, genre),
        ('series', 'watchlist-series'): lambda: get_watchlist_series(user, poster_base, skip, genre),
        ('series', 'waiting-for-new-episodes'): lambda: get_waiting_for_new_episodes(user, poster_base, skip, genre),
        ('movie', 'community-picks'): lambda: get_community_picks(user, poster_base, skip),
        ('movie', 'recommendations'): lambda: get_recommendations(user, poster_base),
        ('movie', 'discover-external'): lambda: get_discover_external(user, skip),
        ('movie', 'top-rated'): lambda: get_top_rated(user, poster_base, skip, genre),
        ('series', 'top-rated'): lambda: get_top_rated_series(user, poster_base, skip, genre),
        ('movie', 'search-movies'): lambda: search_local(Movie, 'movie', search, poster_base, user),
        ('series', 'search-series'): lambda: search_local(TVShow, 'series', search, poster_base, user),
    }

    handler = catalog_handlers.get((media_type, catalog_id))
    if not handler:
        return cors_response({'metas': []})

    try:
        if catalog_id in NO_OUTER_CACHE_CATALOGS:
            metas = handler()
        else:
            generation = catalog_generation(user.id)
            cache_key = f"stremio_catalog_{media_type}_{catalog_id}_{user.id}_g{generation}_{skip}_{genre or ''}"
            metas = cache.get(cache_key)
            if metas is None:
                metas = handler()
                cache.set(cache_key, metas, CATALOG_CACHE_TTL)
                _warm_catalog_posters(metas, media_type, user.id)
    except Exception:
        # one broken catalog shouldn't surface as an HTML 500 (no CORS) and blank the whole board
        logger.exception("Stremio catalog %s/%s failed for user %s", media_type, catalog_id, user.id)
        return cors_response({'metas': []})

    return cors_response({'metas': metas}, cache_control=CATALOG_HTTP_CACHE)


def _warm_catalog_posters(metas: list[dict], media_type: str, user_id: int) -> None:
    """Fire background renders so Stremio's own poster requests land on a warm cache."""
    from django_q.tasks import async_task

    for item in metas:
        imdb_id = item.get('id')
        if not imdb_id:
            continue
        poster = item.get('poster') or ''
        ctx = poster.split('ctx=', 1)[1].split('&', 1)[0] if 'ctx=' in poster else None
        version = poster.split('v=', 1)[1].split('&', 1)[0] if 'v=' in poster else ''
        # only queue a render when this exact content version hasn't been queued yet
        if not cache.add(f"stremio:warmq:{media_type}:{imdb_id}:{user_id}:{ctx or ''}:{version}", 1, WARM_DEDUPE_TTL):
            continue
        async_task('stremio.tasks.warm_poster', media_type, imdb_id, user_id, ctx)


def _catalog_items(medias, media_type: str, poster_base: str, user, ctx: str | None = None) -> list[dict]:
    """Serialize a page of Movie/TVShow objects with batched poster-state queries (no N+1)."""
    medias = [m for m in medias if m and m.imdb_id]
    if not medias:
        return []
    states = bulk_poster_states(medias, media_type, user, ctx)
    show_plot = _show_plot(user)
    # Only ratings already fetched for a meta page; a catalog page never waits on Cinemeta
    ratings = cache.get_many([_imdb_rating_key(media_type, m.imdb_id) for m in medias])
    metas = []
    for media in medias:
        v = content_version(media, media_type, user, ctx, state=states.get(media.id))
        item = to_stremio_catalog_item(
            media, media_type,
            poster_url=_poster_url(poster_base, media_type, media.imdb_id, ctx=ctx, v=v),
            show_plot=show_plot,
            imdb_rating=ratings.get(_imdb_rating_key(media_type, media.imdb_id)) or None,
        )
        if item:
            metas.append(item)
    return metas


def get_watchlist_movies(user, poster_base: str, skip: int = 0, genre: str = None) -> list[dict]:
    """Get movies from user's watchlist."""
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    if genre:
        # If filtering by genre, we must fetch all valid items, sort manually, and then paginate
        watchlist_items = Watchlist.objects.filter(
            user=user,
            content_type=movie_ct
        )
        
        # Map object_id -> date_added for sorting
        date_map = {item.object_id: item.date_added for item in watchlist_items}
        
        movies = Movie.objects.filter(
            id__in=date_map.keys(),
            genres__name=genre
        ).exclude(
            Q(imdb_id__isnull=True) | Q(imdb_id='')
        ).prefetch_related('genres')
        
        # Sort by date_added descending
        sorted_movies = sorted(movies, key=lambda m: date_map.get(m.id), reverse=True)
        
        # Apply pagination
        paginated_movies = sorted_movies[skip:skip + PAGE_SIZE]
        return _catalog_items(paginated_movies, 'movie', poster_base, user)

    else:
        # Standard efficient pagination
        watchlist_items = Watchlist.objects.filter(
            user=user,
            content_type=movie_ct
        ).order_by('-date_added')[skip:skip + PAGE_SIZE]
        
        movie_ids = [item.object_id for item in watchlist_items]
        movies = Movie.objects.filter(
            id__in=movie_ids
        ).exclude(
            Q(imdb_id__isnull=True) | Q(imdb_id='')
        ).prefetch_related('genres')
        
        # Preserve watchlist order
        movie_dict = {m.id: m for m in movies}
        ordered = [movie_dict[movie_id] for movie_id in movie_ids if movie_id in movie_dict]
        return _catalog_items(ordered, 'movie', poster_base, user)


def _bulk_watch_stats(user, tvshow_ids) -> dict:
    """Per-show watch progress % (aired episodes only) and last-watched time, in 2 queries."""
    from tvshows.models import Episode
    from django.utils import timezone

    total_episodes = Episode.objects.filter(
        season__show_id__in=tvshow_ids,
        air_date__isnull=False,
        air_date__lte=timezone.now()
    ).exclude(season__season_number=0).values('season__show_id').annotate(count=Count('id'))
    watched_episodes = user.watched_episodes.filter(
        episode__season__show_id__in=tvshow_ids
    ).exclude(episode__season__season_number=0).values('episode__season__show_id').annotate(
        count=Count('id'), last_watched=Max('watched_date')
    )

    total_map = {row['season__show_id']: row['count'] for row in total_episodes}
    watched_map = {row['episode__season__show_id']: row for row in watched_episodes}

    stats = {}
    for show_id in tvshow_ids:
        watched = watched_map.get(show_id)
        watched_count = watched['count'] if watched else 0
        stats[show_id] = {
            'progress': (watched_count / total_map[show_id] * 100) if total_map.get(show_id) else 0,
            'last_watched': watched['last_watched'] if watched else None,
        }
    return stats


def _bulk_watch_progress(user, tvshow_ids) -> dict:
    """Compute per-show watch progress % in 2 queries instead of N x get_watch_progress() calls."""
    return {show_id: s['progress'] for show_id, s in _bulk_watch_stats(user, tvshow_ids).items()}


def get_continue_watching(user, poster_base: str, skip: int = 0) -> list[dict]:
    """Get TV shows that user has started but not finished, most recently watched first."""
    tvshow_ct = ContentType.objects.get_for_model(TVShow)

    tvshow_ids = list(
        Watchlist.objects.filter(user=user, content_type=tvshow_ct).values_list('object_id', flat=True)
    )
    if not tvshow_ids:
        return []

    stats = _bulk_watch_stats(user, tvshow_ids)
    in_progress_ids = [show_id for show_id, s in stats.items() if 0 < s['progress'] < 100]
    if not in_progress_ids:
        return []

    tvshows = TVShow.objects.filter(id__in=in_progress_ids).filter(_has_imdb_id()).prefetch_related('genres')
    tvshow_dict = {t.id: t for t in tvshows}

    # in progress means at least one watched episode, so last_watched is always set here
    sorted_ids = sorted(
        (show_id for show_id in in_progress_ids if show_id in tvshow_dict),
        key=lambda show_id: stats[show_id]['last_watched'],
        reverse=True,
    )

    page = [tvshow_dict[tvshow_id] for tvshow_id in sorted_ids[skip:skip + PAGE_SIZE]]
    return _catalog_items(page, 'series', poster_base, user, ctx='cw')


def get_watchlist_series(user, poster_base: str, skip: int = 0, genre: str = None) -> list[dict]:
    """Get TV shows from user's watchlist that haven't been started yet (0% progress)."""
    tvshow_ct = ContentType.objects.get_for_model(TVShow)
    
    if genre:
        watchlist_items = Watchlist.objects.filter(
            user=user,
            content_type=tvshow_ct
        )
        date_map = {item.object_id: item.date_added for item in watchlist_items}
        
        tvshows = TVShow.objects.filter(
            id__in=date_map.keys(),
            genres__name=genre
        ).exclude(
            Q(imdb_id__isnull=True) | Q(imdb_id='')
        ).prefetch_related('genres')

        # Sort by date added
        tvshows_sorted = sorted(tvshows, key=lambda t: date_map.get(t.id), reverse=True)
        progress_map = _bulk_watch_progress(user, [t.id for t in tvshows_sorted])

        unstarted = [t for t in tvshows_sorted if progress_map.get(t.id, 0) == 0]
        return _catalog_items(unstarted[skip:skip + PAGE_SIZE], 'series', poster_base, user)

    else:
        watchlist_items = Watchlist.objects.filter(
            user=user,
            content_type=tvshow_ct
        ).order_by('-date_added')
        
        tvshow_ids = [item.object_id for item in watchlist_items]
        tvshows = TVShow.objects.filter(
            id__in=tvshow_ids
        ).exclude(
            Q(imdb_id__isnull=True) | Q(imdb_id='')
        ).prefetch_related('genres')
        
        # Build dict for ordering
        tvshow_dict = {t.id: t for t in tvshows}
        progress_map = _bulk_watch_progress(user, tvshow_ids)

        unstarted = [
            tvshow_dict[tid]
            for tid in tvshow_ids
            if tid in tvshow_dict and progress_map.get(tid, 0) == 0
        ]
        return _catalog_items(unstarted[skip:skip + PAGE_SIZE], 'series', poster_base, user)


def get_waiting_for_new_episodes(user, poster_base: str, skip: int = 0, genre: str = None) -> list[dict]:
    """Get watchlist shows fully caught up on aired episodes but still renewed for more."""
    tvshow_ct = ContentType.objects.get_for_model(TVShow)

    watchlist_items = Watchlist.objects.filter(user=user, content_type=tvshow_ct).order_by('-date_added')
    date_map = {item.object_id: item.date_added for item in watchlist_items}
    tvshow_ids = list(date_map.keys())
    if not tvshow_ids:
        return []

    tvshows_qs = TVShow.objects.filter(
        id__in=tvshow_ids
    ).exclude(
        Q(imdb_id__isnull=True) | Q(imdb_id='')
    ).exclude(
        status__in=FINISHED_TVSHOW_STATUSES
    ).prefetch_related('genres')

    if genre:
        tvshows_qs = tvshows_qs.filter(genres__name=genre)

    tvshows = list(tvshows_qs)
    progress_map = _bulk_watch_progress(user, [t.id for t in tvshows])

    # Caught up on everything aired so far, but the show itself isn't finished
    caught_up = [t for t in tvshows if progress_map.get(t.id, 0) >= 100]
    caught_up.sort(key=lambda t: date_map.get(t.id), reverse=True)

    return _catalog_items(caught_up[skip:skip + PAGE_SIZE], 'series', poster_base, user)


def get_community_picks(user, poster_base: str, skip: int = 0) -> list[dict]:
    """Get past and current Movie of the Week picks that the user hasn't reviewed, newest first."""
    movie_ct = ContentType.objects.get_for_model(Movie)

    # Get movie IDs the user has reviewed
    user_reviewed_movie_ids = set(
        Review.objects.filter(
            user=user,
            content_type=movie_ct
        ).values_list('object_id', flat=True)
    )

    # Queued picks haven't been featured yet (and have no end_date, which would sort them first)
    motw_picks = MovieOfWeekPick.objects.exclude(status='queued').select_related('movie').prefetch_related(
        'movie__genres'
    ).order_by(F('end_date').desc(nulls_last=True), F('start_date').desc(nulls_last=True))

    picked_movies = []
    seen_movie_ids = set()

    for pick in motw_picks:
        movie = pick.movie
        # Skip reviewed, repeat picks of the same movie, and movies without an imdb_id
        if movie.id in user_reviewed_movie_ids or movie.id in seen_movie_ids or not movie.imdb_id:
            continue
        seen_movie_ids.add(movie.id)
        picked_movies.append(movie)

    return _catalog_items(picked_movies[skip:skip + PAGE_SIZE], 'movie', poster_base, user)


def _serialize_web_movie_recommendations(recommendations) -> list[dict]:
    """Serialize MovieRecommender results in the shape /movies/recommendations/ returns."""
    data = []
    for rec in recommendations:
        movie = rec if isinstance(rec, Movie) else rec[0] if isinstance(rec, tuple) else None
        if movie is None:
            continue
        data.append({
            'id': movie.id,
            'tmdb_id': movie.tmdb_id,
            'title': movie.title,
            'poster': movie.poster,
            'release_date': movie.release_date,
            'rating': movie.rating,
            'predicted_rating': getattr(movie, 'predicted_rating', None),
            'collection': movie.collection.name if movie.collection else None,
        })
    return data


def _build_recommendation_movie_ids(user, *, recommender=None) -> list[int]:
    """Run the ML recommender and cache the resulting movie ids; shared by the live view and the warm task.

    Also populates the web ``/movies/recommendations/`` cache (limit=10) from the same
    model pass so web visitors don't pay a 10 s cold unpickle in a gunicorn worker.
    """
    if recommender is None:
        recommender = MovieRecommender()
    recommendations = recommender.get_recommendations_for_user(
        user.id,
        max_recommendations=RECOMMENDATIONS_SIZE
    )

    # Recommendations returns Movie instances or tuples
    movie_ids = []
    for rec in recommendations:
        movie = rec if isinstance(rec, Movie) else rec[0] if isinstance(rec, tuple) else None
        if movie and movie.imdb_id:
            movie_ids.append(movie.id)

    set_cached(SCOPE_STREMIO, user.id, RECOMMENDATIONS_SIZE, movie_ids, ttl=RECOMMENDATIONS_CACHE_TTL)
    web_payload = _serialize_web_movie_recommendations(recommendations[:10])
    set_cached(SCOPE_MOVIES, user.id, 10, web_payload, ttl=RECOMMENDATIONS_CACHE_TTL)
    return movie_ids


def get_recommendations(user, poster_base: str) -> list[dict]:
    """Get personalized movie recommendations (cached per user; poster URLs built fresh per request)."""
    movie_ids = get_cached(SCOPE_STREMIO, user.id, RECOMMENDATIONS_SIZE)
    if movie_ids is None:
        movie_ids = _build_recommendation_movie_ids(user)

    movies_by_id = Movie.objects.prefetch_related('genres').in_bulk(movie_ids)
    ordered = [movies_by_id[mid] for mid in movie_ids if mid in movies_by_id]
    return _catalog_items(ordered, 'movie', poster_base, user)


def _parse_tmdb_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _external_meta(details: dict) -> dict:
    """Full meta for a TMDB-only movie, served by the meta endpoint since it isn't in the local DB."""
    media = SimpleNamespace(
        imdb_id=details['imdb_id'],
        title=details.get('title', ''),
        overview=details.get('overview', ''),
        poster_path=details.get('poster_path'),
        backdrop_path=details.get('backdrop_path'),
        release_date=_parse_tmdb_date(details.get('release_date')),
        runtime=details.get('runtime'),
        genres=[g['name'] for g in details.get('genres') or [] if g.get('name')],
    )
    meta = to_stremio_meta(media, 'movie')
    meta['poster'] = get_poster_url(media, 'w500')
    return meta


def _build_discover_external(user, *, recommender=None) -> list[dict]:
    """Fetch+cache the external discover list; shared by the live view and the background warm task."""
    if recommender is None:
        recommender = MovieRecommender()
    recommendations = recommender.get_recommendations_for_user(
        user.id, DISCOVER_EXTERNAL_CANDIDATE_COUNT, scope='external'
    )

    movies_service = MoviesService()
    show_plot = _show_plot(user)

    def fetch_item(rec):
        try:
            details = movies_service.get_movie_details(rec['tmdb_id'])
        except Exception:
            return None
        if not details or not details.get('imdb_id') or not details.get('poster_path'):
            return None
        media = SimpleNamespace(
            imdb_id=details['imdb_id'],
            title=details.get('title', ''),
            overview=details.get('overview', ''),
            release_date=_parse_tmdb_date(details.get('release_date')),
            genres=[g['name'] for g in details.get('genres') or [] if g.get('name')],
        )
        poster_url = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        item = to_stremio_catalog_item(media, 'movie', poster_url=poster_url, show_plot=show_plot)
        return rec['ranking_score'], item, _external_meta(details)

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_item, rec) for rec in recommendations]
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    # Preserve the recommender's ranking order (TMDB lookups complete out of order)
    results.sort(key=lambda r: r[0], reverse=True)
    metas = [item for _, item, _ in results]
    cache.set(f"stremio_discover_external_{user.id}", metas, DISCOVER_EXTERNAL_CACHE_TTL)
    # TMDB data isn't personal, so these are shared across users, keyed by imdb id
    cache.set_many({f"stremio_ext_meta_{meta['id']}": meta for _, _, meta in results}, DISCOVER_EXTERNAL_CACHE_TTL)
    return metas


def get_discover_external(user, skip: int = 0) -> list[dict]:
    """Get personalized TMDB recommendations for movies not yet in the local DB (cached per user)."""
    metas = cache.get(f"stremio_discover_external_{user.id}")
    if metas is None:
        metas = _build_discover_external(user)

    return metas[skip:skip + PAGE_SIZE]


def _top_rated(model, media_type: str, user, poster_base: str, skip: int, genre: str | None) -> list[dict]:
    """Highest community-rated titles of `model` that the user hasn't reviewed."""
    ct = ContentType.objects.get_for_model(model)

    # Restrict to titles Stremio can show before slicing, so pages aren't cut short afterwards
    eligible = model.objects.filter(_has_imdb_id())
    if genre:
        eligible = eligible.filter(genres__name=genre)

    user_reviewed = Review.objects.filter(user=user, content_type=ct).values('object_id')
    rated = Review.objects.filter(
        content_type=ct, object_id__in=eligible.values('id')
    ).exclude(
        object_id__in=user_reviewed
    ).values('object_id').annotate(
        avg_rating=Avg('rating')
    ).order_by('-avg_rating', 'object_id')[skip:skip + PAGE_SIZE]  # object_id tie-break keeps pages stable

    ids = [r['object_id'] for r in rated]
    by_id = model.objects.prefetch_related('genres').in_bulk(ids)
    ordered = [by_id[media_id] for media_id in ids if media_id in by_id]
    return _catalog_items(ordered, media_type, poster_base, user)


def get_top_rated(user, poster_base: str, skip: int = 0, genre: str = None) -> list[dict]:
    """Get highest rated movies that the user hasn't reviewed."""
    return _top_rated(Movie, 'movie', user, poster_base, skip, genre)


def get_top_rated_series(user, poster_base: str, skip: int = 0, genre: str = None) -> list[dict]:
    """Get highest rated TV shows that the user hasn't reviewed (averaged across all season reviews)."""
    return _top_rated(TVShow, 'series', user, poster_base, skip, genre)


def search_local(model, media_type: str, query: str | None, poster_base: str, user=None) -> list[dict]:
    """Title search over the local DB: exact matches, then prefix matches, then best rated."""
    if not query:
        return []
    query = query[:100]

    results = list(model.objects.filter(
        Q(title__icontains=query) | Q(original_title__icontains=query)
    ).filter(_has_imdb_id()).annotate(
        match_rank=Case(
            When(title__iexact=query, then=0),
            When(title__istartswith=query, then=1),
            default=2,
            output_field=IntegerField(),
        )
    ).order_by('match_rank', F('rating').desc(nulls_last=True), 'id').prefetch_related('genres')[:SEARCH_LIMIT])

    return _catalog_items(results, media_type, poster_base, user)


@csrf_exempt
def poster_image(request, config: str, media_type: str, imdb_id: str):
    """Serve a catalog poster with overlay, falling back to the plain TMDB image on any failure."""
    if imdb_id.endswith('.png'):
        imdb_id = imdb_id[:-4]
    ctx = request.GET.get('ctx')
    if ctx not in POSTER_CONTEXTS:
        ctx = None

    user = get_user_from_config(config)
    model = {'movie': Movie, 'series': TVShow}.get(media_type)
    if not user or model is None:
        return HttpResponseNotFound()
    if is_rate_limited(user.id, 'poster'):
        return HttpResponse(status=429)

    media = _get_by_imdb(model, imdb_id)
    if media is None:
        return HttpResponseNotFound()

    image_bytes = get_cached_poster(media, media_type, user, ctx)

    if not image_bytes:
        fallback_url = get_poster_url(media)
        if fallback_url:
            return HttpResponseRedirect(fallback_url)
        return HttpResponseNotFound()

    response = HttpResponse(image_bytes, content_type='image/jpeg')
    # per-user overlay (watch progress), so shared caches must not store it. A URL whose ?v=
    # matches the current content is immutable: any change produces a different URL.
    requested = request.GET.get('v')
    if requested and requested == content_version(media, media_type, user, ctx):
        response['Cache-Control'] = 'private, max-age=31536000, immutable'
    else:
        response['Cache-Control'] = 'private, max-age=300'
    return response


@csrf_exempt
@require_stremio_auth
def meta(request, config: str, media_type: str, imdb_id: str):
    """
    Stremio meta endpoint.
    Returns detailed metadata for a specific item, including user's ratings.
    A 404 tells Stremio to use the next addon's meta (e.g. Cinemeta) instead of an empty page.
    """
    if request.method == 'OPTIONS':
        return cors_preflight_response()

    user = request.stremio_user

    # Remove .json suffix if present
    if imdb_id.endswith('.json'):
        imdb_id = imdb_id[:-5]

    links = {
        'site_base': request.build_absolute_uri('/'),
        'manifest_url': request.build_absolute_uri(f'/stremio/{config}/manifest.json'),
        'genre_catalog': genre_link_catalog(media_type, enabled_catalog_keys(decode_config(config))),
    }

    try:
        if media_type == 'movie':
            meta_data = get_movie_meta(user, imdb_id, links)
        elif media_type == 'series':
            meta_data = get_series_meta(user, imdb_id, links)
        else:
            meta_data = None
    except Exception:
        logger.exception("Stremio meta %s/%s failed for user %s", media_type, imdb_id, user.id)
        meta_data = None

    if not meta_data:
        return cors_response({'meta': None}, status=404)

    return cors_response({'meta': meta_data}, cache_control=META_HTTP_CACHE)


def genre_link_catalog(media_type: str, enabled: set[str] | None) -> str | None:
    """Catalog id that genre links should open: one the user actually has enabled, else none."""
    ids = {c.key: c.id for c in CATALOGS}
    for key in GENRE_LINK_CATALOG_KEYS.get(media_type, []):
        if enabled is None or key in enabled:
            return ids[key]
    return None


def _imdb_rating_key(media_type: str, imdb_id: str) -> str:
    return f"stremio_imdb_rating_{media_type}_{imdb_id}"


def imdb_rating(media_type: str, imdb_id: str) -> str | None:
    """IMDb rating from Cinemeta, cached per title (misses too, so a title is looked up at most daily)."""
    key = _imdb_rating_key(media_type, imdb_id)
    cached = cache.get(key)
    if cached is not None:
        return cached or None

    rating, ttl = '', IMDB_RATING_MISS_TTL
    try:
        response = requests.get(
            CINEMETA_META_URL.format(media_type=media_type, imdb_id=imdb_id), timeout=CINEMETA_TIMEOUT
        )
        if response.ok:
            value = str((response.json().get('meta') or {}).get('imdbRating') or '')
            if _IMDB_RATING_RE.fullmatch(value):
                rating, ttl = value, IMDB_RATING_TTL
    except (requests.RequestException, ValueError, AttributeError):
        ttl = IMDB_RATING_ERROR_TTL
    cache.set(key, rating, ttl)
    return rating or None


def _show_plot(user) -> bool:
    user_settings = getattr(user, 'settings', None)  # reverse one-to-one; missing row raises AttributeError subclass
    return getattr(user_settings, 'show_plot', True)


def _community_rating(ct, object_id: int) -> dict:
    return Review.objects.filter(content_type=ct, object_id=object_id).aggregate(avg=Avg('rating'), count=Count('id'))


def _meta_kwargs(user, ct, media, url_name: str, links: dict) -> dict:
    return {
        'community': _community_rating(ct, media.id),
        'show_plot': _show_plot(user),
        'site_url': links['site_base'].rstrip('/') + reverse(url_name, args=[media.tmdb_id]),
        'manifest_url': links['manifest_url'],
        'genre_catalog': links.get('genre_catalog'),
        'imdb_rating': imdb_rating('movie' if url_name == 'movie_page' else 'series', media.imdb_id),
    }


def get_movie_meta(user, imdb_id: str, links: dict) -> dict | None:
    """Get movie metadata with user's rating; falls back to cached TMDB data for Discover titles."""
    movie = _get_by_imdb(Movie, imdb_id, 'genres')
    if movie is None:
        external = cache.get(f"stremio_ext_meta_{imdb_id}")
        if external:
            external = {**external, 'behaviorHints': {'defaultVideoId': imdb_id}}
            if not _show_plot(user):  # shared cache entry is built with the plot; hide it per user
                external['description'] = ''
            rating = imdb_rating('movie', imdb_id)
            if rating:
                external['imdbRating'] = rating
                # same reason as _build_links: with `links` present, only an 'imdb' link shows the badge
                external['links'] = [
                    {'name': rating, 'category': 'imdb', 'url': f"https://imdb.com/title/{imdb_id}"},
                    *[link for link in external.get('links', []) if link.get('category') != 'imdb'],
                ]
        return external

    movie_ct = ContentType.objects.get_for_model(Movie)
    review = Review.objects.filter(
        user=user,
        content_type=movie_ct,
        object_id=movie.id
    ).first()

    return to_stremio_meta(movie, 'movie', review, **_meta_kwargs(user, movie_ct, movie, 'movie_page', links))


def _series_videos(tvshow, user, show_plot: bool = True) -> list[dict]:
    """Episode list for the series page; episodes the user has watched get a ✓ prefix."""
    from tvshows.models import Episode, WatchedEpisode

    episodes = Episode.objects.filter(season__show=tvshow).select_related('season').order_by(
        'season__season_number', 'episode_number'
    )
    watched_ids = set(
        WatchedEpisode.objects.filter(user=user, episode__season__show=tvshow).values_list('episode_id', flat=True)
    )

    videos = []
    for episode in episodes:
        season_number = episode.season.season_number
        title = episode.title or f"Episode {episode.episode_number}"
        video = {
            'id': f"{tvshow.imdb_id}:{season_number}:{episode.episode_number}",
            'title': f"✓ {title}" if episode.id in watched_ids else title,
            'season': season_number,
            'episode': episode.episode_number,
            'overview': (episode.overview or '') if show_plot else '',
        }
        if episode.air_date:
            video['released'] = f"{episode.air_date.isoformat()}T00:00:00.000Z"
        if episode.still:
            video['thumbnail'] = resize_tmdb_image(episode.still, 'w500')
        videos.append(video)
    return videos


def get_series_meta(user, imdb_id: str, links: dict) -> dict | None:
    """Get TV show metadata with the user's per-season ratings and the episode list."""
    tvshow = _get_by_imdb(TVShow, imdb_id, 'genres')
    if tvshow is None:
        return None

    # Get all user's reviews for this TV show (reviews are linked via season or episode_subgroup)
    tvshow_ct = ContentType.objects.get_for_model(TVShow)
    reviews = Review.objects.filter(
        user=user,
        content_type=tvshow_ct,
        object_id=tvshow.id
    ).select_related('season', 'episode_subgroup')

    aggregated_review = None
    season_ratings = []
    for review in reviews:
        if not review.rating:
            continue
        if review.season:
            label, order = f"S{review.season.season_number}", (0, review.season.season_number, '')
        elif review.episode_subgroup:
            label, order = review.episode_subgroup.name, (1, 0, review.episode_subgroup.name)
        else:
            label, order = 'Show', (2, 0, '')
        season_ratings.append({'label': label, 'order': order, 'rating': review.rating})

    if season_ratings:
        season_ratings.sort(key=lambda s: s['order'])
        aggregated_review = {
            'avg_rating': sum(s['rating'] for s in season_ratings) / len(season_ratings),
            'season_ratings': season_ratings,
        }

    meta_data = to_stremio_meta(
        tvshow, 'series', aggregated_review, **_meta_kwargs(user, tvshow_ct, tvshow, 'tv_show_page', links)
    )
    if meta_data:
        meta_data['videos'] = _series_videos(tvshow, user, show_plot=_show_plot(user))
    return meta_data


ADDON_EVENT_ACTIONS = {
    'player': {'start', 'pause', 'stop'},
    'library': {'libraryAdd', 'libraryRemove', 'watched', 'unwatched'},
}


def _int_or_none(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@csrf_exempt
@require_stremio_auth
def addon_event(request, config: str, kind: str, media_type: str, item_id: str, extra: str = ''):
    """Record a player/library event from a Stremio client. Probe only: nothing is marked watched yet.

    Paths (stremio-core src/models/common/addon_events.rs):
      player/{type}/{videoId}/action={start|pause|stop}&currentTime={ms}&duration={ms}.json
      library/{type}/{id}/action={libraryAdd|libraryRemove|watched|unwatched}[&videoId=a,b].json
    Always answers an empty, parseable response; a failure here must never surface in the client.
    """
    if request.method == 'OPTIONS':
        return cors_preflight_response()

    from urllib.parse import parse_qsl

    from .models import AddonEvent

    extra = extra or ''
    params = dict(parse_qsl(extra, keep_blank_values=True))
    action = params.get('action', '')
    try:
        AddonEvent.objects.create(
            user=request.stremio_user,
            kind=kind,
            media_type=media_type[:20],
            item_id=item_id[:100],
            action=action[:30] if action in ADDON_EVENT_ACTIONS[kind] else f"?{action[:28]}",
            current_time=_int_or_none(params.get('currentTime')),
            duration=_int_or_none(params.get('duration')),
            video_ids=[v for v in params.get('videoId', '').split(',') if v][:200],
            extra=extra[:4000],
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:300],
        )
    except Exception:
        logger.exception("could not record stremio %s event for user %s", kind, request.stremio_user.id)
    logger.info("stremio %s event user=%s %s/%s %s", kind, request.stremio_user.id, media_type, item_id, extra[:200])
    return cors_response({'metas': []}, cache_control='no-store')
