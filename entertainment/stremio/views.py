import base64
import json
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from types import SimpleNamespace

from django.http import JsonResponse, HttpResponse, HttpResponseNotFound, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Avg, Q, Count

from .authentication import require_stremio_auth, get_user_from_config
from .formatters import to_stremio_meta, to_stremio_catalog_item, get_poster_url
from .poster import get_cached_poster

from movies.models import Movie, MovieOfWeekPick
from tvshows.models import TVShow, Season
from custom_auth.models import Watchlist, Review, Genre
from movies.services.recommendation import MovieRecommender
from api.services.movies import MoviesService


# Constants
PAGE_SIZE = 100
RECOMMENDATIONS_SIZE = 60
RECOMMENDATIONS_CACHE_TTL = 3600
DISCOVER_EXTERNAL_CANDIDATE_COUNT = 60
DISCOVER_EXTERNAL_CACHE_TTL = 3600
CATALOG_CACHE_TTL = 120  # short TTL for DB-backed catalogs, since Stremio polls catalogs often
MANIFEST_GENRES_CACHE_TTL = 3600
# TMDB statuses that mean a show won't get more episodes
FINISHED_TVSHOW_STATUSES = {'Ended', 'Canceled'}
# These catalogs already manage their own (longer, personalized) caching internally
NO_OUTER_CACHE_CATALOGS = {'recommendations', 'discover-external'}
# Bump on any catalogs/config change so Stremio's client detects it and offers "Update" in Addons
MANIFEST_VERSION = '1.1.0'


def cors_response(data: dict, status: int = 200) -> JsonResponse:
    """Create a JsonResponse with CORS headers for Stremio."""
    response = JsonResponse(data, status=status)
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


def cors_preflight_response() -> HttpResponse:
    """Handle CORS preflight OPTIONS request."""
    response = HttpResponse()
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type'
    response['Access-Control-Max-Age'] = '86400'
    return response


def _poster_url(poster_base: str, media_type: str, imdb_id: str, ctx: str = None) -> str:
    """Build the overlay poster URL embedded into catalog items."""
    url = f"{poster_base}/{media_type}/{imdb_id}.png"
    return f"{url}?ctx={ctx}" if ctx else url


def configure(request, config: str = None):
    """
    Stremio configure page.
    Shows API key input and generates install URL.
    If config is provided, pre-fills the API key.
    """
    from .authentication import decode_config
    
    base_url = request.build_absolute_uri('/stremio/')
    
    # Pre-fill API key if config is provided
    api_key = ''
    if config:
        config_data = decode_config(config)
        api_key = config_data.get('api_key', '')
    
    return render(request, 'stremio/configure.html', {
        'base_url': base_url,
        'api_key': api_key,
    })


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
    
    from .authentication import get_user_from_config
    
    # Check if config is provided and valid
    is_configured = False
    if config:
        user = get_user_from_config(config)
        is_configured = user is not None

    # Movies and TV shows don't share the same genre set, so keep separate cached option lists
    movie_genre_names = cache.get('stremio_manifest_genres_movie')
    if movie_genre_names is None:
        movie_genre_names = list(
            Genre.objects.filter(movie__isnull=False).values_list('name', flat=True).distinct().order_by('name')
        )
        cache.set('stremio_manifest_genres_movie', movie_genre_names, MANIFEST_GENRES_CACHE_TTL)

    series_genre_names = cache.get('stremio_manifest_genres_series')
    if series_genre_names is None:
        series_genre_names = list(
            Genre.objects.filter(tvshow__isnull=False).values_list('name', flat=True).distinct().order_by('name')
        )
        cache.set('stremio_manifest_genres_series', series_genre_names, MANIFEST_GENRES_CACHE_TTL)

    movie_filter_extra = [
        {'name': 'genre', 'options': movie_genre_names, 'isRequired': False},
        {'name': 'skip', 'isRequired': False}
    ]
    series_filter_extra = [
        {'name': 'genre', 'options': series_genre_names, 'isRequired': False},
        {'name': 'skip', 'isRequired': False}
    ]
    
    manifest_data = {
        'id': 'com.entertainment-list.addon',
        'version': MANIFEST_VERSION,
        'name': 'Entertainment List',
        'description': 'Your personal entertainment tracking addon - watchlists, recommendations, and community picks',
        'logo': request.build_absolute_uri('/static/images/logo.png'),
        'resources': ['catalog', 'meta'],
        'types': ['movie', 'series'],
        'idPrefixes': ['tt'],
        'catalogs': [
            {
                'id': 'continue-watching',
                'name': 'Continue Watching',
                'type': 'series',
                'extra': [{'name': 'skip', 'isRequired': False}]
            },
            {
                'id': 'watchlist-movies',
                'name': 'My Watchlist',
                'type': 'movie',
                'extra': movie_filter_extra
            },
            {
                'id': 'watchlist-series',
                'name': 'Not Started',
                'type': 'series',
                'extra': series_filter_extra
            },
            {
                'id': 'waiting-for-new-episodes',
                'name': 'Waiting for New Episodes',
                'type': 'series',
                'extra': series_filter_extra
            },
            {
                'id': 'community-picks',
                'name': 'Movie of the Week',
                'type': 'movie',
                'extra': [{'name': 'skip', 'isRequired': False}]
            },
            {
                'id': 'recommendations',
                'name': 'Recommended For You',
                'type': 'movie',
            },
            {
                'id': 'discover-external',
                'name': 'Discover',
                'type': 'movie',
                'extra': [{'name': 'skip', 'isRequired': False}]
            },
            {
                'id': 'top-rated',
                'name': 'Top Rated (Unseen)',
                'type': 'movie',
                'extra': movie_filter_extra
            },
            {
                'id': 'top-rated',
                'name': 'Top Rated (Unseen)',
                'type': 'series',
                'extra': series_filter_extra
            },
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
    
    return cors_response(manifest_data)


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
    
    # Parse skip and genre from extra
    skip = 0
    genre = None
    if extra:
        for part in extra.split('&'):
            if part.startswith('skip='):
                try:
                    skip = int(part.split('=')[1])
                except (ValueError, IndexError):
                    pass
            elif part.startswith('genre='):
                try:
                    genre = urllib.parse.unquote(part.split('=')[1])
                except (ValueError, IndexError):
                    pass
    
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
    }
    
    handler = catalog_handlers.get((media_type, catalog_id))
    if not handler:
        return cors_response({'metas': []})
    
    if catalog_id in NO_OUTER_CACHE_CATALOGS:
        metas = handler()
    else:
        cache_key = f"stremio_catalog_{media_type}_{catalog_id}_{user.id}_{skip}_{genre or ''}"
        metas = cache.get(cache_key)
        if metas is None:
            metas = handler()
            cache.set(cache_key, metas, CATALOG_CACHE_TTL)
            _warm_catalog_posters(metas, media_type, user.id)
    
    return cors_response({'metas': metas})


def _warm_catalog_posters(metas: list[dict], media_type: str, user_id: int) -> None:
    """Fire background renders so Stremio's own poster requests land on a warm cache."""
    from django_q.tasks import async_task

    for item in metas:
        imdb_id = item.get('id')
        if not imdb_id:
            continue
        poster = item.get('poster') or ''
        ctx = poster.split('ctx=', 1)[1].split('&', 1)[0] if 'ctx=' in poster else None
        async_task('stremio.tasks.warm_poster', media_type, imdb_id, user_id, ctx)


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
        
        metas = []
        for movie in paginated_movies:
            item = to_stremio_catalog_item(movie, 'movie', poster_url=_poster_url(poster_base, 'movie', movie.imdb_id))
            if item:
                metas.append(item)
        return metas

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
        metas = []
        for movie_id in movie_ids:
            if movie_id in movie_dict:
                item = to_stremio_catalog_item(movie_dict[movie_id], 'movie', poster_url=_poster_url(poster_base, 'movie', movie_dict[movie_id].imdb_id))
                if item:
                    metas.append(item)
        
        return metas


def get_continue_watching(user, poster_base: str, skip: int = 0) -> list[dict]:
    """Get TV shows that user has started but not finished watching."""
    from tvshows.models import Episode, WatchedEpisode
    
    tvshow_ct = ContentType.objects.get_for_model(TVShow)
    
    # Get all TV shows from user's watchlist
    watchlist_items = Watchlist.objects.filter(
        user=user,
        content_type=tvshow_ct
    ).order_by('-date_added')
    
    tvshow_ids = [item.object_id for item in watchlist_items]
    
    if not tvshow_ids:
        return []
    
    # Get total aired episodes count per show
    from django.utils import timezone
    total_episodes = Episode.objects.filter(
        season__show_id__in=tvshow_ids,
        season__season_number__gt=0,
        air_date__isnull=False,
        air_date__lte=timezone.now()
    ).values('season__show_id').annotate(
        count=Count('id')
    )
    
    # Get watched episodes count per show for this user
    watched_episodes = WatchedEpisode.objects.filter(
        user=user,
        episode__season__show_id__in=tvshow_ids,
        episode__season__season_number__gt=0
    ).values('episode__season__show_id').annotate(
        count=Count('id')
    )
    
    # Create mappings for fast lookup
    total_episodes_map = {item['season__show_id']: item['count'] for item in total_episodes}
    watched_episodes_map = {item['episode__season__show_id']: item['count'] for item in watched_episodes}
    
    # Find shows in progress (0 < progress < 100)
    in_progress_ids = []
    progress_map = {}
    
    for tvshow_id in tvshow_ids:
        total = total_episodes_map.get(tvshow_id, 0)
        watched = watched_episodes_map.get(tvshow_id, 0)
        
        if total > 0:
            progress = (watched / total * 100)
            if 0 < progress < 100:
                in_progress_ids.append(tvshow_id)
                progress_map[tvshow_id] = progress
    
    if not in_progress_ids:
        return []
    
    # Get TV shows and sort by most recently watched
    tvshows = TVShow.objects.filter(
        id__in=in_progress_ids
    ).exclude(
        Q(imdb_id__isnull=True) | Q(imdb_id='')
    ).prefetch_related('genres')
    
    # Sort by progress (most recently active - higher progress first) and apply pagination
    tvshow_dict = {t.id: t for t in tvshows}
    
    # Sort in_progress_ids by progress descending (shows closer to completion first)
    sorted_ids = sorted(in_progress_ids, key=lambda x: progress_map.get(x, 0), reverse=True)
    
    metas = []
    for idx, tvshow_id in enumerate(sorted_ids):
        if idx < skip:
            continue
        if len(metas) >= PAGE_SIZE:
            break
        
        if tvshow_id in tvshow_dict:
            item = to_stremio_catalog_item(
                tvshow_dict[tvshow_id], 'series',
                poster_url=_poster_url(poster_base, 'series', tvshow_dict[tvshow_id].imdb_id, ctx='cw')
            )
            if item:
                metas.append(item)
    
    return metas


def _bulk_watch_progress(user, tvshow_ids) -> dict:
    """Compute per-show watch progress % in 2 queries instead of N x get_watch_progress() calls."""
    from tvshows.models import Episode

    total_episodes = Episode.objects.filter(
        season__show_id__in=tvshow_ids
    ).exclude(season__season_number=0).values('season__show_id').annotate(count=Count('id'))
    watched_episodes = user.watched_episodes.filter(
        episode__season__show_id__in=tvshow_ids
    ).exclude(episode__season__season_number=0).values('episode__season__show_id').annotate(count=Count('id'))

    total_map = {row['season__show_id']: row['count'] for row in total_episodes}
    watched_map = {row['episode__season__show_id']: row['count'] for row in watched_episodes}

    return {
        show_id: (watched_map.get(show_id, 0) / total_map[show_id] * 100) if total_map.get(show_id) else 0
        for show_id in tvshow_ids
    }


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
        ).prefetch_related('genres', 'seasons__episodes')

        # Sort by date added
        tvshows_sorted = sorted(tvshows, key=lambda t: date_map.get(t.id), reverse=True)
        progress_map = _bulk_watch_progress(user, [t.id for t in tvshows_sorted])
        
        metas = []
        skipped = 0
        
        for tvshow in tvshows_sorted:
            # Only shows with no watched episodes at all
            if progress_map.get(tvshow.id, 0) != 0:
                continue
            
            # Handle pagination
            if skipped < skip:
                skipped += 1
                continue
            
            item = to_stremio_catalog_item(tvshow, 'series', poster_url=_poster_url(poster_base, 'series', tvshow.imdb_id))
            if item:
                metas.append(item)
                if len(metas) >= PAGE_SIZE:
                    break
        return metas

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
        ).prefetch_related('genres', 'seasons__episodes')
        
        # Build dict for ordering
        tvshow_dict = {t.id: t for t in tvshows}
        progress_map = _bulk_watch_progress(user, tvshow_ids)
        
        # Filter out fully watched shows and apply pagination
        metas = []
        skipped = 0
        
        for tvshow_id in tvshow_ids:
            if tvshow_id not in tvshow_dict:
                continue
            
            tvshow = tvshow_dict[tvshow_id]
            
            # Only shows with no watched episodes at all
            if progress_map.get(tvshow_id, 0) != 0:
                continue
            
            # Handle pagination
            if skipped < skip:
                skipped += 1
                continue
            
            item = to_stremio_catalog_item(tvshow, 'series', poster_url=_poster_url(poster_base, 'series', tvshow.imdb_id))
            if item:
                metas.append(item)
                if len(metas) >= PAGE_SIZE:
                    break
        
        return metas


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
    ).prefetch_related('genres', 'seasons__episodes')

    if genre:
        tvshows_qs = tvshows_qs.filter(genres__name=genre)

    tvshows = list(tvshows_qs)
    progress_map = _bulk_watch_progress(user, [t.id for t in tvshows])

    # Caught up on everything aired so far, but the show itself isn't finished
    caught_up = [t for t in tvshows if progress_map.get(t.id, 0) >= 100]
    caught_up.sort(key=lambda t: date_map.get(t.id), reverse=True)

    metas = []
    for tvshow in caught_up[skip:skip + PAGE_SIZE]:
        item = to_stremio_catalog_item(tvshow, 'series', poster_url=_poster_url(poster_base, 'series', tvshow.imdb_id))
        if item:
            metas.append(item)

    return metas


def get_community_picks(user, poster_base: str, skip: int = 0) -> list[dict]:
    """Get Movie of the Week picks that the user hasn't reviewed."""
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    # Get movie IDs the user has reviewed
    user_reviewed_movie_ids = set(
        Review.objects.filter(
            user=user,
            content_type=movie_ct
        ).values_list('object_id', flat=True)
    )
    
    # Get Movie of the Week picks where user hasn't reviewed the movie
    # Ordered by most recent first (newest featured movies first)
    motw_picks = MovieOfWeekPick.objects.select_related('movie').order_by('-end_date')
    
    metas = []
    count = 0
    skipped = 0
    
    for pick in motw_picks:
        movie = pick.movie
        # Skip if user has reviewed this movie or no imdb_id
        if movie.id in user_reviewed_movie_ids or not movie.imdb_id:
            continue
        
        # Handle pagination
        if skipped < skip:
            skipped += 1
            continue
        
        item = to_stremio_catalog_item(movie, 'movie', poster_url=_poster_url(poster_base, 'movie', movie.imdb_id))
        if item:
            metas.append(item)
            count += 1
            if count >= PAGE_SIZE:
                break
    
    return metas


def _build_recommendation_movie_ids(user) -> list[int]:
    """Run the ML recommender and cache the resulting movie ids; shared by the live view and the warm task."""
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

    cache.set(f"stremio_recommendations_{user.id}", movie_ids, RECOMMENDATIONS_CACHE_TTL)
    return movie_ids


def get_recommendations(user, poster_base: str) -> list[dict]:
    """Get personalized movie recommendations (cached per user; poster URLs built fresh per request)."""
    movie_ids = cache.get(f"stremio_recommendations_{user.id}")
    if movie_ids is None:
        movie_ids = _build_recommendation_movie_ids(user)

    movies_by_id = Movie.objects.in_bulk(movie_ids)
    metas = []
    for movie_id in movie_ids:
        movie = movies_by_id.get(movie_id)
        if movie and movie.imdb_id:
            item = to_stremio_catalog_item(movie, 'movie', poster_url=_poster_url(poster_base, 'movie', movie.imdb_id))
            if item:
                metas.append(item)

    return metas


def _build_discover_external(user) -> list[dict]:
    """Fetch+cache the external discover list; shared by the live view and the background warm task."""
    recommender = MovieRecommender()
    recommendations = recommender.get_recommendations_for_user(
        user.id, DISCOVER_EXTERNAL_CANDIDATE_COUNT, scope='external'
    )

    movies_service = MoviesService()

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
        )
        poster_url = f"https://image.tmdb.org/t/p/w500{details['poster_path']}"
        return rec['ranking_score'], to_stremio_catalog_item(media, 'movie', poster_url=poster_url)

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_item, rec) for rec in recommendations]
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    # Preserve the recommender's ranking order (TMDB lookups complete out of order)
    results.sort(key=lambda r: r[0], reverse=True)
    metas = [item for _, item in results]
    cache.set(f"stremio_discover_external_{user.id}", metas, DISCOVER_EXTERNAL_CACHE_TTL)
    return metas


def get_discover_external(user, skip: int = 0) -> list[dict]:
    """Get personalized TMDB recommendations for movies not yet in the local DB (cached per user)."""
    metas = cache.get(f"stremio_discover_external_{user.id}")
    if metas is None:
        metas = _build_discover_external(user)

    return metas[skip:skip + PAGE_SIZE]


def get_top_rated(user, poster_base: str, skip: int = 0, genre: str = None) -> list[dict]:
    """Get highest rated movies that the user hasn't reviewed."""
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    # Get user's reviewed movie IDs
    user_reviewed_ids = set(
        Review.objects.filter(
            user=user,
            content_type=movie_ct
        ).values_list('object_id', flat=True)
    )
    
    # Base query for reviews
    reviews_query = Review.objects.filter(
        content_type=movie_ct
    ).exclude(
        object_id__in=user_reviewed_ids
    )

    if genre:
        # Restrict to movies with the specific genre
        movie_ids_with_genre = Movie.objects.filter(genres__name=genre).values_list('id', flat=True)
        reviews_query = reviews_query.filter(object_id__in=movie_ids_with_genre)

    # Get movies with average ratings
    rated_movies = reviews_query.values('object_id').annotate(
        avg_rating=Avg('rating')
    ).order_by('-avg_rating')[skip:skip + PAGE_SIZE]
    
    movie_ids = [r['object_id'] for r in rated_movies]
    
    movies = Movie.objects.filter(
        id__in=movie_ids
    ).exclude(
        Q(imdb_id__isnull=True) | Q(imdb_id='')
    ).prefetch_related('genres')
    
    # Preserve rating order
    movie_dict = {m.id: m for m in movies}
    metas = []
    for movie_id in movie_ids:
        if movie_id in movie_dict:
            item = to_stremio_catalog_item(movie_dict[movie_id], 'movie', poster_url=_poster_url(poster_base, 'movie', movie_dict[movie_id].imdb_id))
            if item:
                metas.append(item)
    
    return metas


def get_top_rated_series(user, poster_base: str, skip: int = 0, genre: str = None) -> list[dict]:
    """Get highest rated TV shows that the user hasn't reviewed."""
    tvshow_ct = ContentType.objects.get_for_model(TVShow)
    
    # Get user's reviewed show IDs
    # Reviews for shows can be on 'series', 'season', etc., but top-rated usually aggregates show-level id reviews or similar.
    # The requirement says: "Aggregation based on `object_id` (Show ID) is correct as finding average rating across all seasons."
    # We should exclude shows if the user has reviewed them.
    
    user_reviewed_ids = set(
        Review.objects.filter(
            user=user,
            content_type=tvshow_ct
        ).values_list('object_id', flat=True)
    )
    
    # Base query for reviews
    reviews_query = Review.objects.filter(
        content_type=tvshow_ct
    ).exclude(
        object_id__in=user_reviewed_ids
    )

    if genre:
        # Restrict to shows with the specific genre
        show_ids_with_genre = TVShow.objects.filter(genres__name=genre).values_list('id', flat=True)
        reviews_query = reviews_query.filter(object_id__in=show_ids_with_genre)

    # Get shows with average ratings
    rated_shows = reviews_query.values('object_id').annotate(
        avg_rating=Avg('rating')
    ).order_by('-avg_rating')[skip:skip + PAGE_SIZE]
    
    show_ids = [r['object_id'] for r in rated_shows]
    
    shows = TVShow.objects.filter(
        id__in=show_ids
    ).exclude(
        Q(imdb_id__isnull=True) | Q(imdb_id='')
    ).prefetch_related('genres')
    
    # Preserve rating order
    show_dict = {s.id: s for s in shows}
    metas = []
    for show_id in show_ids:
        if show_id in show_dict:
            item = to_stremio_catalog_item(show_dict[show_id], 'series', poster_url=_poster_url(poster_base, 'series', show_dict[show_id].imdb_id))
            if item:
                metas.append(item)
    
    return metas


@csrf_exempt
def poster_image(request, config: str, media_type: str, imdb_id: str):
    """Serve a catalog poster with overlay, falling back to the plain TMDB image on any failure."""
    if imdb_id.endswith('.png'):
        imdb_id = imdb_id[:-4]
    ctx = request.GET.get('ctx')

    user = get_user_from_config(config)
    model = {'movie': Movie, 'series': TVShow}.get(media_type)
    if not user or model is None:
        return HttpResponseNotFound()

    try:
        media = model.objects.get(imdb_id=imdb_id)
    except model.DoesNotExist:
        return HttpResponseNotFound()

    image_bytes = get_cached_poster(media, media_type, user, ctx)

    if not image_bytes:
        fallback_url = get_poster_url(media)
        if fallback_url:
            return HttpResponseRedirect(fallback_url)
        return HttpResponseNotFound()

    response = HttpResponse(image_bytes, content_type='image/jpeg')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@csrf_exempt
@require_stremio_auth
def meta(request, config: str, media_type: str, imdb_id: str):
    """
    Stremio meta endpoint.
    Returns detailed metadata for a specific item, including user's review.
    """
    if request.method == 'OPTIONS':
        return cors_preflight_response()
    
    user = request.stremio_user
    
    # Remove .json suffix if present
    if imdb_id.endswith('.json'):
        imdb_id = imdb_id[:-5]
    
    if media_type == 'movie':
        meta_data = get_movie_meta(user, imdb_id)
    elif media_type == 'series':
        meta_data = get_series_meta(user, imdb_id)
    else:
        return cors_response({'meta': None})
    
    if not meta_data:
        return cors_response({'meta': None})
    
    return cors_response({'meta': meta_data})


def get_movie_meta(user, imdb_id: str) -> dict | None:
    """Get movie metadata with user's review."""
    try:
        movie = Movie.objects.prefetch_related('genres').get(imdb_id=imdb_id)
    except Movie.DoesNotExist:
        return None
    
    # Get user's review if exists
    movie_ct = ContentType.objects.get_for_model(Movie)
    review = Review.objects.filter(
        user=user,
        content_type=movie_ct,
        object_id=movie.id
    ).first()
    
    return to_stremio_meta(movie, 'movie', review)


def get_series_meta(user, imdb_id: str) -> dict | None:
    """Get TV show metadata with aggregated user reviews across seasons."""
    try:
        tvshow = TVShow.objects.prefetch_related('genres', 'seasons').get(imdb_id=imdb_id)
    except TVShow.DoesNotExist:
        return None
    
    # Get all user's reviews for this TV show (reviews are linked via season or episode_subgroup)
    tvshow_ct = ContentType.objects.get_for_model(TVShow)
    
    reviews = Review.objects.filter(
        user=user,
        content_type=tvshow_ct,
        object_id=tvshow.id
    ).select_related('season')
    
    if reviews.exists():
        # Build aggregated review data
        season_ratings = []
        total_rating = 0
        count = 0
        latest_review_text = None
        latest_date = None
        
        for review in reviews:
            if review.rating:
                season_num = review.season.season_number if review.season else '?'
                season_ratings.append({
                    'season': season_num,
                    'rating': review.rating
                })
                total_rating += review.rating
                count += 1
            
            if review.review_text and (latest_date is None or review.date_added > latest_date):
                latest_review_text = review.review_text
                latest_date = review.date_added
        
        aggregated_review = {
            'avg_rating': total_rating / count if count > 0 else None,
            'season_ratings': sorted(season_ratings, key=lambda x: x['season'] if isinstance(x['season'], int) else 0),
            'latest_review': latest_review_text,
        }
        
        return to_stremio_meta(tvshow, 'series', aggregated_review)
    
    return to_stremio_meta(tvshow, 'series', None)
