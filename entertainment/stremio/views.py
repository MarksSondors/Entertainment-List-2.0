import base64
import json

from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Q

from .authentication import require_stremio_auth
from .formatters import to_stremio_meta, to_stremio_catalog_item

from movies.models import Movie, MovieOfWeekPick
from tvshows.models import TVShow, Season
from custom_auth.models import Watchlist, Review
from movies.services.recommendation import MovieRecommender


# Constants
PAGE_SIZE = 100
RECOMMENDATIONS_SIZE = 10


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


def configure(request):
    """
    Stremio configure page.
    Shows API key input and generates install URL.
    """
    base_url = request.build_absolute_uri('/stremio/')
    
    return render(request, 'stremio/configure.html', {
        'base_url': base_url,
    })


@csrf_exempt
def manifest(request, config: str = None):
    """
    Stremio manifest endpoint.
    Can be called with or without config for initial addon installation.
    """
    if request.method == 'OPTIONS':
        return cors_preflight_response()
    
    manifest_data = {
        'id': 'com.entertainment-list.addon',
        'version': '1.0.0',
        'name': 'Entertainment List',
        'description': 'Your personal entertainment tracking addon - watchlists, recommendations, and community picks',
        'logo': request.build_absolute_uri('/static/images/logo.png'),
        'resources': ['catalog', 'meta'],
        'types': ['movie', 'series'],
        'idPrefixes': ['tt'],
        'catalogs': [
            {
                'id': 'watchlist-movies',
                'name': 'My Watchlist',
                'type': 'movie',
                'extra': [{'name': 'skip', 'isRequired': False}]
            },
            {
                'id': 'watchlist-series',
                'name': 'My Watchlist',
                'type': 'series',
                'extra': [{'name': 'skip', 'isRequired': False}]
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
                'id': 'top-rated',
                'name': 'Top Rated (Unseen)',
                'type': 'movie',
                'extra': [{'name': 'skip', 'isRequired': False}]
            },
        ],
        'behaviorHints': {
            'configurable': True,
            'configurationRequired': True,
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
    
    # Parse skip from extra (format: "skip=100")
    skip = 0
    if extra:
        for part in extra.split('&'):
            if part.startswith('skip='):
                try:
                    skip = int(part.split('=')[1])
                except (ValueError, IndexError):
                    pass
    
    # Route to appropriate catalog handler
    catalog_handlers = {
        ('movie', 'watchlist-movies'): lambda: get_watchlist_movies(user, skip),
        ('series', 'watchlist-series'): lambda: get_watchlist_series(user, skip),
        ('movie', 'community-picks'): lambda: get_community_picks(user, skip),
        ('movie', 'recommendations'): lambda: get_recommendations(user),
        ('movie', 'top-rated'): lambda: get_top_rated(user, skip),
    }
    
    handler = catalog_handlers.get((media_type, catalog_id))
    if not handler:
        return cors_response({'metas': []})
    
    metas = handler()
    return cors_response({'metas': metas})


def get_watchlist_movies(user, skip: int = 0) -> list[dict]:
    """Get movies from user's watchlist."""
    movie_ct = ContentType.objects.get_for_model(Movie)
    
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
            item = to_stremio_catalog_item(movie_dict[movie_id], 'movie')
            if item:
                metas.append(item)
    
    return metas


def get_watchlist_series(user, skip: int = 0) -> list[dict]:
    """Get TV shows from user's watchlist."""
    tvshow_ct = ContentType.objects.get_for_model(TVShow)
    
    watchlist_items = Watchlist.objects.filter(
        user=user,
        content_type=tvshow_ct
    ).order_by('-date_added')[skip:skip + PAGE_SIZE]
    
    tvshow_ids = [item.object_id for item in watchlist_items]
    tvshows = TVShow.objects.filter(
        id__in=tvshow_ids
    ).exclude(
        Q(imdb_id__isnull=True) | Q(imdb_id='')
    ).prefetch_related('genres')
    
    # Preserve watchlist order
    tvshow_dict = {t.id: t for t in tvshows}
    metas = []
    for tvshow_id in tvshow_ids:
        if tvshow_id in tvshow_dict:
            item = to_stremio_catalog_item(tvshow_dict[tvshow_id], 'series')
            if item:
                metas.append(item)
    
    return metas


def get_community_picks(user, skip: int = 0) -> list[dict]:
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
        
        item = to_stremio_catalog_item(movie, 'movie')
        if item:
            metas.append(item)
            count += 1
            if count >= PAGE_SIZE:
                break
    
    return metas


def get_recommendations(user) -> list[dict]:
    """Get personalized movie recommendations (fixed 10 items, no pagination)."""
    recommender = MovieRecommender()
    recommendations = recommender.get_recommendations_for_user(
        user.id, 
        max_recommendations=RECOMMENDATIONS_SIZE
    )
    
    # Recommendations returns Movie instances or tuples
    metas = []
    for rec in recommendations:
        movie = rec if isinstance(rec, Movie) else rec[0] if isinstance(rec, tuple) else None
        if movie and movie.imdb_id:
            item = to_stremio_catalog_item(movie, 'movie')
            if item:
                metas.append(item)
    
    return metas


def get_top_rated(user, skip: int = 0) -> list[dict]:
    """Get highest rated movies that the user hasn't reviewed."""
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    # Get user's reviewed movie IDs
    user_reviewed_ids = set(
        Review.objects.filter(
            user=user,
            content_type=movie_ct
        ).values_list('object_id', flat=True)
    )
    
    # Get movies with average ratings, excluding user's reviewed
    rated_movies = Review.objects.filter(
        content_type=movie_ct
    ).exclude(
        object_id__in=user_reviewed_ids
    ).values('object_id').annotate(
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
            item = to_stremio_catalog_item(movie_dict[movie_id], 'movie')
            if item:
                metas.append(item)
    
    return metas


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
