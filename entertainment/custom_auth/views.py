from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout  # Import the logout function
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.db.models import Q, Avg
from django.contrib.contenttypes.models import ContentType

from django.utils import timezone
# Create your views here.
# import services

from .models import *
from movies.models import Movie
from tvshows.models import TVShow
from games.models import Game
from datetime import date # Import date
import random

from django.views.decorators.http import require_POST

# Add these imports if they're not already at the top  
from django.db import models
from django.db.models import Count, Avg, F, Q, Case, When, IntegerField
from django.db.models.functions import ExtractMonth, ExtractYear
from collections import defaultdict
import json
import logging
from datetime import timedelta, datetime
from django.contrib.contenttypes.models import ContentType
from movies.models import Movie, Genre
from tvshows.models import TVShow
from custom_auth.models import Review, Person
import re

from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank, TrigramSimilarity
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services.cross_media_recommendation import CrossMediaRecommender

logger = logging.getLogger(__name__)


def get_low_res_poster_url(poster_url):
    """
    Convert TMDB poster URL to lower resolution.
    Replaces w500 or original with w185 for better performance.
    """
    if not poster_url:
        return poster_url
    
    # TMDB image URLs typically look like: https://image.tmdb.org/t/p/w500/poster.jpg
    # We want to replace w500 or original with w185
    if 'image.tmdb.org' in poster_url:
        # Replace common high-res sizes with w185
        poster_url = poster_url.replace('/w500/', '/w185/')
        poster_url = poster_url.replace('/original/', '/w185/')
        poster_url = poster_url.replace('/w780/', '/w185/')
    
    return poster_url


# new and polished code
@login_required
def discover_page(request):
    """
    search_bar
    movie_of_the_week
    popular_movies
    popular_tv_shows
    """
    from movies.views import get_current_community_pick_data
    from notifications.models import PushSubscription
    
    # Get the current community movie pick data
    community_pick_data = get_current_community_pick_data()
    
    # Check if user has enabled push notifications
    has_push_subscription = False
    if request.user.is_authenticated:
        has_push_subscription = PushSubscription.objects.filter(
            user=request.user,
            is_active=True
        ).exists()
    
    context = {
        'community_pick': community_pick_data,
        'has_push_subscription': has_push_subscription,
    }
    
    return render(request, 'discover_page.html', context)

@login_required
def search_bar_discover(request):
    #  i wil get type of search - currently only movies, tvshows, people or users
    search_query = request.GET.get('search', '').strip()
    search_type = request.GET.get('type', 'movies')  # Default to movies if not specified
    if not search_query:
        return JsonResponse({'error': 'Search query cannot be empty'}, status=400)
    
    if search_type == 'movies':
        search_query_obj = SearchQuery(search_query, config='english')
        
        # Primary search using full-text search
        movies = Movie.objects.annotate(
            search=SearchVector('title', 'original_title', config='english'),
            rank=SearchRank(SearchVector('title', 'original_title', config='english'), search_query_obj)
        ).filter(search=search_query_obj).order_by('-rank')[:10]
        
        # If no results, try fuzzy matching with trigrams
        if not movies:
            movies = Movie.objects.annotate(
                similarity=TrigramSimilarity('title', search_query) + TrigramSimilarity('original_title', search_query)
            ).filter(similarity__gt=0.5).order_by('-similarity')[:10]
        
        # If still no results, try partial case-insensitive matching
        if not movies:
            movies = Movie.objects.filter(
                Q(title__icontains=search_query) | Q(original_title__icontains=search_query)
            )[:10]
        
        # Get content type for movies
        movie_content_type = ContentType.objects.get_for_model(Movie)
        movie_ids = [movie.id for movie in movies]
        
        # Get user's watchlist items
        user_watchlist = set(
            Watchlist.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movie_ids
            ).values_list('object_id', flat=True)
        )
        
        # Get user's reviews
        user_reviews = {
            review.object_id: review.rating
            for review in Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movie_ids
            )
        }
        
        # Get average ratings
        avg_ratings = {}
        rating_counts = {}
        for item in Review.objects.filter(
            content_type=movie_content_type,
            object_id__in=movie_ids
        ).values('object_id').annotate(
            avg_rating=Avg('rating'),
            count=Count('id')
        ):
            avg_ratings[item['object_id']] = round(item['avg_rating'], 1)
            rating_counts[item['object_id']] = item['count']
        
        # Get watchlist usernames
        watchlist_users = {}
        for item in Watchlist.objects.filter(
            content_type=movie_content_type,
            object_id__in=movie_ids
        ).select_related('user').values('object_id', 'user__username'):
            if item['object_id'] not in watchlist_users:
                watchlist_users[item['object_id']] = []
            watchlist_users[item['object_id']].append(item['user__username'])
        
        results = []
        for movie in movies:
            result = {
                'id': movie.id,
                'title': movie.title,
                'poster': movie.poster,
                'tmdb_id': movie.tmdb_id,
                'year': movie.release_date.year if movie.release_date else None,
                'url': f'/movies/{movie.tmdb_id}/',
                'in_watchlist': movie.id in user_watchlist,
                'user_rating': user_reviews.get(movie.id),
                'avg_rating': avg_ratings.get(movie.id),
                'rating_count': rating_counts.get(movie.id, 0),
                'watchlist_users': watchlist_users.get(movie.id, [])
            }
            results.append(result)
            
    elif search_type == 'tvshows':
        search_query_obj = SearchQuery(search_query, config='english')
        
        # Primary search using full-text search
        tv_shows = TVShow.objects.annotate(
            search=SearchVector('title', 'original_title', config='english'),
            rank=SearchRank(SearchVector('title', 'original_title', config='english'), search_query_obj)
        ).filter(search=search_query_obj).order_by('-rank')[:10]
        
        # If no results, try fuzzy matching with trigrams
        if not tv_shows:
            tv_shows = TVShow.objects.annotate(
                similarity=TrigramSimilarity('title', search_query) + TrigramSimilarity('original_title', search_query)
            ).filter(similarity__gt=0.5).order_by('-similarity')[:10]
        
        # If still no results, try partial case-insensitive matching
        if not tv_shows:
            tv_shows = TVShow.objects.filter(
                Q(title__icontains=search_query) | Q(original_title__icontains=search_query)
            )[:10]
        
        # Get content type for TV shows
        tv_content_type = ContentType.objects.get_for_model(TVShow)
        tv_ids = [tv.id for tv in tv_shows]
        
        # Get user's watchlist items
        user_watchlist = set(
            Watchlist.objects.filter(
                user=request.user,
                content_type=tv_content_type,
                object_id__in=tv_ids
            ).values_list('object_id', flat=True)
        )
        
        # Get user's reviews
        user_reviews = {
            review.object_id: review.rating
            for review in Review.objects.filter(
                user=request.user,
                content_type=tv_content_type,
                object_id__in=tv_ids
            )
        }
        
        # Get average ratings
        avg_ratings = {}
        rating_counts = {}
        for item in Review.objects.filter(
            content_type=tv_content_type,
            object_id__in=tv_ids
        ).values('object_id').annotate(
            avg_rating=Avg('rating'),
            count=Count('id')
        ):
            avg_ratings[item['object_id']] = round(item['avg_rating'], 1)
            rating_counts[item['object_id']] = item['count']
        
        # Get watchlist usernames
        watchlist_users = {}
        for item in Watchlist.objects.filter(
            content_type=tv_content_type,
            object_id__in=tv_ids
        ).select_related('user').values('object_id', 'user__username'):
            if item['object_id'] not in watchlist_users:
                watchlist_users[item['object_id']] = []
            watchlist_users[item['object_id']].append(item['user__username'])
        
        results = []
        for tv in tv_shows:
            result = {
                'id': tv.id, 
                'title': tv.title, 
                'poster': tv.poster, 
                'tmdb_id': tv.tmdb_id, 
                'year': tv.first_air_date.year if tv.first_air_date else None,
                'url': f'/tvshows/{tv.tmdb_id}/',
                'in_watchlist': tv.id in user_watchlist,
                'user_rating': user_reviews.get(tv.id),
                'avg_rating': avg_ratings.get(tv.id),
                'rating_count': rating_counts.get(tv.id, 0),
                'watchlist_users': watchlist_users.get(tv.id, [])
            }
            # Add end year if the show has ended
            if tv.last_air_date and tv.status in ['Ended', 'Canceled']:
                result['end_year'] = tv.last_air_date.year
            results.append(result)
            
    elif search_type == 'people':
        search_query_obj = SearchQuery(search_query, config='english')
        
        # Primary search using full-text search
        people = Person.objects.annotate(
            search=SearchVector('name', config='english'),
            rank=SearchRank(SearchVector('name', config='english'), search_query_obj)
        ).filter(search=search_query_obj).order_by('-rank')[:10]
        
        # If no results, try fuzzy matching with trigrams
        if not people:
            people = Person.objects.annotate(
                similarity=TrigramSimilarity('name', search_query)
            ).filter(similarity__gt=0.3).order_by('-similarity')[:10]
        
        # If still no results, try partial case-insensitive matching
        if not people:
            people = Person.objects.filter(name__icontains=search_query)[:10]
        
        # Get ratings for people's work
        results = []
        for person in people:
            # Get all media this person has worked on
            person_credits = MediaPerson.objects.filter(person=person)
            
            # Collect all media identifiers
            media_identifiers = set()
            for credit in person_credits:
                media_identifiers.add((credit.content_type_id, credit.object_id))
            
            # Calculate average rating for this person's work
            avg_rating = None
            rating_count = 0
            if media_identifiers:
                q_objects = Q()
                for ct_id, obj_id in media_identifiers:
                    q_objects |= Q(content_type_id=ct_id, object_id=obj_id)
                
                # Get all reviews for this person's work
                reviews = Review.objects.filter(q_objects)
                if reviews.exists():
                    rating_data = reviews.aggregate(
                        avg_rating=Avg('rating'),
                        count=Count('id')
                    )
                    if rating_data['avg_rating'] is not None:
                        avg_rating = round(rating_data['avg_rating'], 1)
                        rating_count = rating_data['count']
            
            results.append({
                'id': person.id,
                'name': person.name,
                'profile_picture': person.profile_picture,
                'url': f'/people/{person.id}/',
                'avg_rating': avg_rating,
                'rating_count': rating_count
            })
    elif search_type == 'users':
        search_query_obj = SearchQuery(search_query, config='english')
        
        # Primary search using full-text search
        users = CustomUser.objects.annotate(
            search=SearchVector('username', config='english'),
            rank=SearchRank(SearchVector('username', config='english'), search_query_obj)
        ).filter(search=search_query_obj).order_by('-rank')[:10]
        
        # If no results, try fuzzy matching with trigrams
        if not users:
            users = CustomUser.objects.annotate(
                similarity=TrigramSimilarity('username', search_query)
            ).filter(similarity__gt=0.3).order_by('-similarity')[:10]
        
        # If still no results, try partial case-insensitive matching
        if not users:
            users = CustomUser.objects.filter(username__icontains=search_query)[:10]
        
        results = [{'id': user.id, 'username': user.username, 'url': f'/profile/{user.username}/'} for user in users]
    elif search_type == 'games':
        search_query_obj = SearchQuery(search_query, config='english')
        
        # Primary search using full-text search
        games = Game.objects.annotate(
            search=SearchVector('title', config='english'),
            rank=SearchRank(SearchVector('title', config='english'), search_query_obj)
        ).filter(search=search_query_obj).order_by('-rank')[:10]
        
        # If no results, try fuzzy matching with trigrams
        if not games:
            games = Game.objects.annotate(
                similarity=TrigramSimilarity('title', search_query)
            ).filter(similarity__gt=0.3).order_by('-similarity')[:10]
        
        # If still no results, try partial case-insensitive matching
        if not games:
            games = Game.objects.filter(title__icontains=search_query)[:10]
        
        # Get content type for games
        game_content_type = ContentType.objects.get_for_model(Game)
        game_ids = [game.id for game in games]
        
        # Get user's watchlist items
        user_watchlist = set(
            Watchlist.objects.filter(
                user=request.user,
                content_type=game_content_type,
                object_id__in=game_ids
            ).values_list('object_id', flat=True)
        )
        
        # Get user's reviews
        user_reviews = {
            review.object_id: review.rating
            for review in Review.objects.filter(
                user=request.user,
                content_type=game_content_type,
                object_id__in=game_ids
            )
        }
        
        # Get average ratings
        avg_ratings = {}
        rating_counts = {}
        for item in Review.objects.filter(
            content_type=game_content_type,
            object_id__in=game_ids
        ).values('object_id').annotate(
            avg_rating=Avg('rating'),
            count=Count('id')
        ):
            avg_ratings[item['object_id']] = round(item['avg_rating'], 1)
            rating_counts[item['object_id']] = item['count']
        
        # Get watchlist usernames
        watchlist_users = {}
        for item in Watchlist.objects.filter(
            content_type=game_content_type,
            object_id__in=game_ids
        ).select_related('user').values('object_id', 'user__username'):
            if item['object_id'] not in watchlist_users:
                watchlist_users[item['object_id']] = []
            watchlist_users[item['object_id']].append(item['user__username'])
        
        results = []
        for game in games:
            result = {
                'id': game.id,
                'title': game.title,
                'poster': game.poster,
                'rawg_id': game.rawg_id,
                'year': game.release_date.year if game.release_date else None,
                'url': f'/games/{game.id}/',
                'in_watchlist': game.id in user_watchlist,
                'user_rating': user_reviews.get(game.id),
                'avg_rating': avg_ratings.get(game.id),
                'rating_count': rating_counts.get(game.id, 0),
                'metacritic': game.metacritic,
                'watchlist_users': watchlist_users.get(game.id, [])
            }
            results.append(result)
    else:
        return JsonResponse({'error': 'Invalid search type'}, status=400)
    
    return JsonResponse({
        'success': True,
        'results': results,
        'search_type': search_type,
        'query': search_query,
        'count': len(results)
    })

@login_required
def discover_genres(request):
    """
    API endpoint for discover page genres - returns 10 random genres
    in randomized order for horizontal scrolling component
    """
    # Get all genres and randomly select 10
    all_genres = list(Genre.objects.all())
    random.shuffle(all_genres)  # Randomize the order
    genres = all_genres[:10]  # Take first 10 after shuffle
    
    genres_data = []
    for genre in genres:
        genre_data = {
            'id': genre.id,
            'name': genre.name,
            'background_image': genre.background_image.url if genre.background_image else None,
        }
        genres_data.append(genre_data)
    
    return JsonResponse({'genres': genres_data})

@login_required
def browse_by_genre(request):
    genres = Genre.objects.all().order_by('name')
    return render(request, 'browse_by_genre.html', {
        'genres': genres,
    })

@login_required
def browse_by_country(request):
    """
    View for displaying all available countries for browsing
    """
    countries = Country.objects.all().order_by('name')
    return render(request, 'browse_by_country.html', {
        'countries': countries,
    })


# old and vibe coded code

def login_page(request):
    if request.user.is_authenticated:
        return redirect('discover_page')
    return render(request, 'login_page.html')

def login_request(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('discover_page')
        else:
            return redirect('login_page')
    return render(request, 'login_page.html')

def logout_request(request):
    if request.user.is_authenticated:
        logout(request)  # Use the logout function
    return redirect('login_page')

@login_required
def home_page(request):
    if request.user.is_authenticated:
        context = {
            'user': request.user,
        }
        return render(request, 'home_page.html', context)
    else:
        return redirect('login_page')


@login_required
def genre_detail(request, genre_id):
    genre = get_object_or_404(Genre, pk=genre_id)
    anime_filter = request.GET.get('anime_filter', 'all')
    view_type = request.GET.get('view_type', 'grid')
    sort_by = request.GET.get('sort_by', 'title')
    sort_order = request.GET.get('sort_order', 'asc')
    watched_status = request.GET.get('watched_status', 'all')
    
    # Get content types for Movie and TVShow models
    movie_content_type = ContentType.objects.get_for_model(Movie)
    tv_show_content_type = ContentType.objects.get_for_model(TVShow)
      # Filter movies and TV shows based on anime_filter
    if (anime_filter == 'anime_only'):
        movies = Movie.objects.filter(genres=genre, is_anime=True)
        tv_shows = TVShow.objects.filter(genres=genre, is_anime=True)
    elif (anime_filter == 'no_anime'):
        movies = Movie.objects.filter(genres=genre, is_anime=False)
        tv_shows = TVShow.objects.filter(genres=genre, is_anime=False)
    else:  # 'all' is the default
        movies = Movie.objects.filter(genres=genre)
        tv_shows = TVShow.objects.filter(genres=genre)
    
    # Apply sorting to movies and TV shows
    # Define sort fields based on sort_by parameter
    if sort_by == 'release_date':
        movie_sort_field = 'release_date'
        tv_sort_field = 'first_air_date'
    elif sort_by == 'rating':
        # For rating, we'll sort by the tmdb rating first, then handle user rating later
        movie_sort_field = 'rating'
        tv_sort_field = 'rating'
    elif sort_by == 'user_rating':
        # We'll handle user rating sorting after getting the reviews
        movie_sort_field = 'title'  # Default fallback for initial query
        tv_sort_field = 'title'
    else:  # 'title' is the default
        movie_sort_field = 'title'
        tv_sort_field = 'title'
    
    # Apply sort direction
    if sort_order == 'desc':
        movie_sort_field = f'-{movie_sort_field}'
        tv_sort_field = f'-{tv_sort_field}'
    
    # Sort the querysets (except for user_rating which we'll handle later)
    if sort_by != 'user_rating':
        movies = movies.order_by(movie_sort_field)
        tv_shows = tv_shows.order_by(tv_sort_field)
    else:
        # For user rating, we'll sort after getting the reviews
        movies = movies.order_by('title')  # Default order first
        tv_shows = tv_shows.order_by('title')
    
    # Get user's reviews for movies in this genre
    user_movie_reviews = {
        review.object_id: review.rating 
        for review in Review.objects.filter(
            user=request.user,
            content_type=movie_content_type,
            object_id__in=movies.values_list('id', flat=True)
        )
    }
    
    
    # Get average movie ratings across the platform
    avg_movie_ratings = {}
    movie_rating_counts = {}
    for item in Review.objects.filter(
        content_type=movie_content_type,
        object_id__in=movies.values_list('id', flat=True)
    ).values('object_id').annotate(
        avg_rating=models.Avg('rating'),
        count=models.Count('id')
    ):
        avg_movie_ratings[item['object_id']] = round(item['avg_rating'], 1)
        movie_rating_counts[item['object_id']] = item['count']
      # Annotate movies with user ratings and average ratings
    for movie in movies:
        movie.user_rating = user_movie_reviews.get(movie.id)
        if movie.id in avg_movie_ratings:
            movie.avg_rating = avg_movie_ratings[movie.id]
            movie.rating_count = movie_rating_counts[movie.id]
      # Handle user rating sorting if selected
    if sort_by == 'user_rating':
        # Convert querysets to lists for custom sorting
        movies_list = list(movies)
        tv_shows_list = list(tv_shows)
        
        # Sort movies by user rating (None values go to the end)
        movies_list.sort(
            key=lambda x: (x.user_rating is None, x.user_rating or 0),
            reverse=(sort_order == 'desc')
        )
        
        # Get user's reviews for TV shows as well
        user_tv_reviews = {
            review.object_id: review.rating 
            for review in Review.objects.filter(
                user=request.user,
                content_type=tv_show_content_type,
                object_id__in=tv_shows.values_list('id', flat=True)
            )
        }
        
        # Annotate TV shows with user ratings
        for tv_show in tv_shows_list:
            tv_show.user_rating = user_tv_reviews.get(tv_show.id)
        
        # Sort TV shows by user rating (None values go to the end)
        tv_shows_list.sort(
            key=lambda x: (x.user_rating is None, x.user_rating or 0),
            reverse=(sort_order == 'desc')
        )
        
        # Replace the querysets with sorted lists
        movies = movies_list
        tv_shows = tv_shows_list
    
    # Apply watched status filter
    if watched_status != 'all':
        if watched_status == 'watched':
            # For movies: filter to only movies the user has reviewed
            user_reviewed_movies = set(Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movies.values_list('id', flat=True) if hasattr(movies, 'values_list') else [m.id for m in movies]
            ).values_list('object_id', flat=True))
            
            if hasattr(movies, 'filter'):  # QuerySet
                movies = movies.filter(id__in=user_reviewed_movies)
            else:  # List (from user_rating sorting)
                movies = [m for m in movies if m.id in user_reviewed_movies]
            
            # For TV shows: filter to only shows where user has watched at least one episode
            from tvshows.models import WatchedEpisode
            user_watched_shows = set(WatchedEpisode.objects.filter(
                user=request.user,
                episode__season__show_id__in=tv_shows.values_list('id', flat=True) if hasattr(tv_shows, 'values_list') else [s.id for s in tv_shows]
            ).values_list('episode__season__show_id', flat=True))
            
            if hasattr(tv_shows, 'filter'):  # QuerySet
                tv_shows = tv_shows.filter(id__in=user_watched_shows)
            else:  # List (from user_rating sorting)
                tv_shows = [s for s in tv_shows if s.id in user_watched_shows]
                
        elif watched_status == 'not_watched':
            # For movies: filter to only movies the user has NOT reviewed
            user_reviewed_movies = set(Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movies.values_list('id', flat=True) if hasattr(movies, 'values_list') else [m.id for m in movies]
            ).values_list('object_id', flat=True))
            
            if hasattr(movies, 'exclude'):  # QuerySet
                movies = movies.exclude(id__in=user_reviewed_movies)
            else:  # List (from user_rating sorting)
                movies = [m for m in movies if m.id not in user_reviewed_movies]
            
            # For TV shows: filter to only shows where user has NOT watched any episodes
            from tvshows.models import WatchedEpisode
            user_watched_shows = set(WatchedEpisode.objects.filter(
                user=request.user,
                episode__season__show_id__in=tv_shows.values_list('id', flat=True) if hasattr(tv_shows, 'values_list') else [s.id for s in tv_shows]
            ).values_list('episode__season__show_id', flat=True))
            
            if hasattr(tv_shows, 'exclude'):  # QuerySet
                tv_shows = tv_shows.exclude(id__in=user_watched_shows)
            else:  # List (from user_rating sorting)
                tv_shows = [s for s in tv_shows if s.id not in user_watched_shows]
    return render(request, 'genre_detail.html', {
        'genre': genre,
        'movies': movies,
        'tv_shows': tv_shows,
        'anime_filter': anime_filter,
        'view_type': view_type,
        'sort_by': sort_by,
        'sort_order': sort_order,
        'watched_status': watched_status,
    })

@login_required
def profile_page(request, username=None):
    """
    View for displaying a user's profile page.
    If username is provided, show that user's profile.
    Otherwise, show the logged-in user's profile.
    """
    if username:
        # Only fetch needed fields for the profile user
        user = get_object_or_404(
            CustomUser.objects.only('id', 'username', 'profile_picture'),
            username=username
        )
    else:
        user = request.user
    
    # Get all users for the sidebar (only fetch needed fields)
    all_users = CustomUser.objects.only('id', 'username', 'profile_picture').order_by('username')[:50]
    
    # Cache ContentType lookups to avoid repeated queries
    movie_content_type = ContentType.objects.get_for_model(Movie)
    tv_show_content_type = ContentType.objects.get_for_model(TVShow)
    game_content_type = ContentType.objects.get_for_model(Game)
    
    # Get user's favorite movies - fetch only top 20 reviews
    movie_reviews = Review.objects.filter(
        user=user,
        content_type=movie_content_type
    ).only('object_id', 'rating').order_by('-rating')[:20]
    
    # Create a list of movies with their review scores
    favorite_movies = []
    movie_ids = [review.object_id for review in movie_reviews]
    # Fetch only necessary fields for movies
    movies_dict = {movie.id: movie for movie in Movie.objects.filter(
        id__in=movie_ids
    ).only('id', 'title', 'poster', 'tmdb_id', 'release_date')}
    
    for review in movie_reviews:
        movie = movies_dict.get(review.object_id)
        if movie:
            movie.user_rating = review.rating
            movie.poster = get_low_res_poster_url(movie.poster)
            favorite_movies.append(movie)
            
    # Get user's favorite games - fetch only top 20 reviews
    game_reviews = Review.objects.filter(
        user=user,
        content_type=game_content_type
    ).only('object_id', 'rating').order_by('-rating')[:20]
    
    # Create a list of games with their review scores
    favorite_games = []
    game_ids = [review.object_id for review in game_reviews]
    # Fetch only necessary fields for games
    games_dict = {game.id: game for game in Game.objects.filter(
        id__in=game_ids
    ).only('id', 'title', 'poster', 'rawg_id', 'release_date')}
    
    for review in game_reviews:
        game = games_dict.get(review.object_id)
        if game:
            game.user_rating = review.rating
            game.poster = get_low_res_poster_url(game.poster)
            favorite_games.append(game)
    
    # Get user's favorite TV shows - only fetch what we need
    tv_show_reviews = Review.objects.filter(
        user=user,
        content_type=tv_show_content_type
    ).only('object_id', 'rating').order_by('-rating')
    
    # Prefetch all TV shows in a single query with only necessary fields
    tv_show_ids = set(review.object_id for review in tv_show_reviews)
    tv_shows_dict = {tv.id: tv for tv in TVShow.objects.filter(
        id__in=tv_show_ids
    ).only('id', 'title', 'poster', 'tmdb_id', 'first_air_date')}
    
    # Group reviews by TV show to handle multiple reviews per show
    tv_show_ratings = {}
    for review in tv_show_reviews:
        tv_show = tv_shows_dict.get(review.object_id)
        if not tv_show:
            continue
            
        if tv_show.id not in tv_show_ratings:
            tv_show_ratings[tv_show.id] = {
                'tv_show': tv_show,
                'reviews': [],
                'total_rating': 0,
                'count': 0
            }
        
        tv_show_ratings[tv_show.id]['reviews'].append(review)
        tv_show_ratings[tv_show.id]['total_rating'] += review.rating
        tv_show_ratings[tv_show.id]['count'] += 1
    
    # Create a list of favorite TV shows with average ratings
    favorite_shows = []
    for show_data in sorted(tv_show_ratings.values(), 
                           key=lambda x: x['total_rating']/x['count'] if x['count'] > 0 else 0, 
                           reverse=True)[:20]:
        show = show_data['tv_show']
        show.user_rating = round(show_data['total_rating'] / show_data['count'], 1) if show_data['count'] > 0 else 0
        show.review_count = show_data['count']  # Add the count of reviews for this show
        show.poster = get_low_res_poster_url(show.poster)  # Use lower resolution poster
        favorite_shows.append(show)
    
    # Get user's watchlist with prefetched content_type
    watchlist_items = list(user.get_watchlist().select_related('content_type')[:20])
    
    # Prepare watchlist items for template display
    watchlist_for_template = []
    if watchlist_items:
        # Group watchlist items by content type for bulk fetching
        items_by_ct = {}
        for item in watchlist_items:
            ct_id = item.content_type_id
            if ct_id not in items_by_ct:
                items_by_ct[ct_id] = []
            items_by_ct[ct_id].append(item)
        
        # Bulk fetch media objects
        media_cache = {}
        for ct_id, items in items_by_ct.items():
            if ct_id == movie_content_type.id:
                movie_ids = [item.object_id for item in items]
                movies = Movie.objects.filter(id__in=movie_ids).only(
                    'id', 'title', 'poster', 'tmdb_id'
                )
                for movie in movies:
                    media_cache[(ct_id, movie.id)] = movie
            elif ct_id == tv_show_content_type.id:
                tv_ids = [item.object_id for item in items]
                tvshows = TVShow.objects.filter(id__in=tv_ids).only(
                    'id', 'title', 'poster', 'tmdb_id'
                )
                for tv in tvshows:
                    media_cache[(ct_id, tv.id)] = tv
            elif ct_id == game_content_type.id:
                game_ids = [item.object_id for item in items]
                games = Game.objects.filter(id__in=game_ids).only(
                    'id', 'title', 'poster', 'rawg_id'
                )
                for game in games:
                    media_cache[(ct_id, game.id)] = game
        
        # Add reviews data to items (optimized version)
        add_review_data_to_items(watchlist_items, user)
        
        # Process items for template using cached media objects
        for item in watchlist_items:
            media = media_cache.get((item.content_type_id, item.object_id))
            if media:
                item_data = {
                    'id': item.id,
                    'title': media.title,
                    'poster_url': get_low_res_poster_url(media.poster),
                    'media_type': item.content_type.model,
                    'avg_rating': getattr(item, 'avg_rating', None),
                    'rating_count': getattr(item, 'rating_count', None),
                    'object_id': media.id
                }
                
                if item.content_type_id == movie_content_type.id:
                    item_data['tmdb_id'] = media.tmdb_id
                elif item.content_type_id == tv_show_content_type.id:
                    item_data['tmdb_id'] = media.tmdb_id
                elif item.content_type_id == game_content_type.id:
                    item_data['rawg_id'] = media.rawg_id
                    item_data['game_id'] = media.id
                    
                watchlist_for_template.append(item_data)
    
    context = {
        'user': user,
        'favorite_movies': favorite_movies,
        'favorite_shows': favorite_shows,
        'favorite_games': favorite_games,
        'watchlist_items': watchlist_for_template,
        'all_users': all_users,  # Add all users to context
        'current_user': request.user,  # Add current user for highlighting
    }
    
    return render(request, 'profile_page.html', context)

@login_required
def watchlist_page(request):
    """Display the user's watchlist with categorized sections."""
    
    user = request.user
    # Get content types
    movie_ct = ContentType.objects.get_for_model(Movie)
    tv_ct = ContentType.objects.get_for_model(TVShow)
    game_ct = ContentType.objects.get_for_model(Game)
    
    # Get all watchlist items for the user (for initial page load)
    watchlist_items = user.get_watchlist()
    
    if not watchlist_items:
        # Handle empty watchlist case - return empty lists for all sections
        context = {
            'continue_watching': [],
            'havent_started': [],
            'finished_shows': [],
            'movies': [],
            'games': [],
            'genres': [],
            'countries': [],
            'watchlist_empty': True,
        }
        return render(request, 'watchlist_page_old.html', context)
    
    # Group items by content type for efficient media fetching
    items_by_content_type = {}
    for item in watchlist_items:
        if item.content_type_id not in items_by_content_type:
            items_by_content_type[item.content_type_id] = []
        items_by_content_type[item.content_type_id].append(item)
    
    # Prefetch all media objects in batches by content type
    media_objects = {}
    all_media_by_type = {}  # Store media objects grouped by type
    
    for ct_id, items in items_by_content_type.items():
        # Get the model class for this content type
        content_type = ContentType.objects.get_for_id(ct_id)
        model_class = content_type.model_class()
        
        # Fetch all media objects of this type in one query
        object_ids = [item.object_id for item in items]
        objects = model_class.objects.filter(id__in=object_ids)
        
        # Store all media objects by type for later use
        all_media_by_type[ct_id] = objects
        
        # Create a lookup dictionary
        for obj in objects:
            media_objects[(ct_id, obj.id)] = obj
    
    # Attach prefetched media objects to items
    for item in watchlist_items:
        # Get the prefetched object or None if not found
        media_obj = media_objects.get((item.content_type_id, item.object_id))
        if media_obj:
            # Set the prefetched object directly
            item._prefetched_media_object = media_obj
            # Replace the descriptor with a simple property
            item.__dict__['media'] = media_obj
        else:
            # Mark missing media items to avoid queries
            item.__dict__['media'] = None
    
    # Create a function to safely get media without triggering database queries
    def get_safe_media(item):
        """Get media object without triggering a database query."""
        return item.__dict__.get('media')
    
    # Categorize items
    continue_watching = []
    havent_started = []
    finished_shows = []
    movies = []
    games = []
    
    # First, separate TV shows and movies
    tv_shows_items = []
    
    for item in watchlist_items:
        media = get_safe_media(item)
        if item.content_type_id == movie_ct.id:
            movies.append(item)
        elif item.content_type_id == tv_ct.id and media:
            tv_shows_items.append(item)
        elif item.content_type_id == game_ct.id:
            games.append(item)
    
    # If there are TV shows, batch-fetch their watch progress
    if tv_shows_items:
        # Get all TV show IDs
        tv_show_ids = [item.object_id for item in tv_shows_items]
        
        # Get all episodes for these shows
        from tvshows.models import Episode
        total_episodes = Episode.objects.filter(
            season__show_id__in=tv_show_ids,
            season__season_number__gt=0,
            air_date__isnull=False,
            air_date__lte=timezone.now()
        ).values('season__show_id').annotate(
            count=models.Count('id')
        )
        
        # Get all watched episodes for these shows by this user
        from tvshows.models import WatchedEpisode
        watched_episodes = WatchedEpisode.objects.filter(
            user=user,
            episode__season__show_id__in=tv_show_ids,
            episode__season__season_number__gt=0 
        ).values('episode__season__show_id').annotate(
            count=models.Count('id')
        )
        
        # Create mappings for fast lookup
        total_episodes_map = {item['season__show_id']: item['count'] for item in total_episodes}
        watched_episodes_map = {item['episode__season__show_id']: item['count'] for item in watched_episodes}
        
        # Now categorize TV shows with the pre-fetched data
        for item in tv_shows_items:
            show_id = item.object_id
            total = total_episodes_map.get(show_id, 0)
            watched = watched_episodes_map.get(show_id, 0)
            
            # Calculate progress percentage
            progress = (watched / total * 100) if total > 0 else 0
            
            # Categorize based on progress
            if progress >= 100:
                # Completed shows
                finished_shows.append(item)
            elif progress > 0:
                # In-progress shows
                continue_watching.append((item, progress))
            else:
                # Not started shows
                havent_started.append(item)
    
    # Add reviews data to items
    add_review_data_to_items(watchlist_items, user)
    
    # Get all genres for filter
    genre_ids = set()
    country_ids = set()
    
    # Get genres and countries from movies more efficiently
    movie_objects = all_media_by_type.get(movie_ct.id, [])
    if movie_objects:
        # Get all genre IDs in a single query
        movie_ids = [movie.id for movie in movie_objects]
        movie_genres = Movie.objects.filter(id__in=movie_ids).values('genres').distinct()
        genre_ids.update(genre.get('genres') for genre in movie_genres if genre.get('genres'))
        
        # Get all country IDs in a single query
        movie_countries = Movie.objects.filter(id__in=movie_ids).values('countries').distinct()
        country_ids.update(country.get('countries') for country in movie_countries if country.get('countries'))
    
    # Similarly for TV shows
    tv_objects = all_media_by_type.get(tv_ct.id, [])
    if tv_objects:
        tv_ids = [tv.id for tv in tv_objects]
        tv_genres = TVShow.objects.filter(id__in=tv_ids).values('genres').distinct()
        genre_ids.update(genre.get('genres') for genre in tv_genres if genre.get('genres'))
        
        tv_countries = TVShow.objects.filter(id__in=tv_ids).values('countries').distinct()
        country_ids.update(country.get('countries') for country in tv_countries if country.get('countries'))
    
    genres = Genre.objects.filter(id__in=genre_ids).distinct().order_by('name')
    countries = Country.objects.filter(id__in=country_ids).distinct().order_by('name')
    
    # Prepare flattened data for templates to avoid GenericForeignKey lookups
    movies_for_template = []
    for item in movies:
        media = get_safe_media(item)
        if not media:
            continue
            
        movies_for_template.append({
            'id': item.id,
            'date_added': item.date_added,
            'content_type_model': item.content_type.model,
            'media_id': media.id,
            'media_title': media.title,
            'media_poster': media.poster,
            'media_tmdb_id': media.tmdb_id,
            'media_release_date': getattr(media, 'release_date', None),
            'avg_rating': getattr(item, 'avg_rating', None),
            'rating_count': getattr(item, 'rating_count', None),
        })
    
    # Do the same for continue_watching
    continue_watching_for_template = []
    for item, progress in continue_watching:
        media = get_safe_media(item)
        if not media:
            continue
            
        continue_watching_for_template.append({
            'id': item.id,
            'progress': progress,
            'date_added': item.date_added,
            'content_type_model': item.content_type.model,
            'media_id': media.id,
            'media_title': media.title,
            'media_poster': media.poster,
            'media_tmdb_id': media.tmdb_id,
            'media_first_air_date': getattr(media, 'first_air_date', None),
            'avg_rating': getattr(item, 'avg_rating', None),
            'rating_count': getattr(item, 'rating_count', None),
        })
    
    # Similarly for havent_started
    havent_started_for_template = []
    for item in havent_started:
        media = get_safe_media(item)
        if not media:
            continue
            
        havent_started_for_template.append({
            'id': item.id,
            'date_added': item.date_added,
            'content_type_model': item.content_type.model,
            'media_id': media.id,
            'media_title': media.title,
            'media_poster': media.poster,
            'media_tmdb_id': media.tmdb_id,
            'media_release_date': getattr(media, 'release_date', None),
            'avg_rating': getattr(item, 'avg_rating', None),
            'rating_count': getattr(item, 'rating_count', None),
        })
    
    # Flatten finished shows data
    finished_shows_for_template = []
    for item in finished_shows:
        media = get_safe_media(item)
        if not media:
            continue
            
        finished_shows_for_template.append({
            'id': item.id,
            'date_added': item.date_added,
            'content_type_model': item.content_type.model,
            'media_id': media.id,
            'media_title': media.title,
            'media_poster': media.poster,
            'media_tmdb_id': media.tmdb_id,
            'media_first_air_date': getattr(media, 'first_air_date', None),
            'avg_rating': getattr(item, 'avg_rating', None),
            'rating_count': getattr(item, 'rating_count', None),
        })
        
    # Flatten games data
    games_for_template = []
    for item in games:
        media = get_safe_media(item)
        if not media:
            continue
            
        games_for_template.append({
            'id': item.id,
            'date_added': item.date_added,
            'content_type_model': item.content_type.model,
            'media_id': media.id,
            'media_title': media.title,
            'media_poster': media.poster,
            'media_rawg_id': media.rawg_id,
            'media_release_date': getattr(media, 'release_date', None),
            'avg_rating': getattr(item, 'avg_rating', None),
            'rating_count': getattr(item, 'rating_count', None),
        })
    
    # Update your context dictionary to include finished_shows
    context = {
        'continue_watching': continue_watching_for_template,
        'havent_started': havent_started_for_template,
        'finished_shows': finished_shows_for_template,  # Add this line
        'movies': movies_for_template,
        'games': games_for_template,
        'genres': genres,
        'countries': countries,
    }
    
    return render(request, 'watchlist_page_old.html', context)

def add_review_data_to_items(items, current_user):
    """Helper function to add review data to watchlist items."""
    if not items:
        return
        
    # Group items by content type and object ID
    grouped_items = {}
    for item in items:
        key = (item.content_type_id, item.object_id)
        if key not in grouped_items:
            grouped_items[key] = []
        grouped_items[key].append(item)
    
    # Identify TV show content type
    from tvshows.models import TVShow
    tv_show_content_type_id = ContentType.objects.get_for_model(TVShow).id
    
    # Build a combined query for all reviews at once
    from django.db.models import Q
    review_query = Q()
    tv_show_ids = []  # Track TV show IDs for season/subgroup review fetch
    
    for ct_id, obj_id in grouped_items.keys():
        review_query |= Q(content_type_id=ct_id, object_id=obj_id)
        # If this is a TV show, track it for later
        if ct_id == tv_show_content_type_id:
            tv_show_ids.append(obj_id)
    
    # Fetch all direct reviews in a SINGLE query - only fetch needed fields
    all_reviews = list(Review.objects.filter(review_query).exclude(
        Q(season__isnull=False) | Q(episode_subgroup__isnull=False)
    ).values('content_type_id', 'object_id', 'rating', 'user__username'))
    
    # For TV shows, also fetch season and episode subgroup reviews
    if tv_show_ids:
        # Get season and subgroup reviews for these TV shows - only needed fields
        from django.db.models import Q
        tv_show_season_reviews = list(Review.objects.filter(
            content_type_id=tv_show_content_type_id,
            object_id__in=tv_show_ids
        ).exclude(
            season=None,
            episode_subgroup=None
        ).values('content_type_id', 'object_id', 'rating', 'user__username'))
        
        # Add these reviews to our collection
        all_reviews = all_reviews + tv_show_season_reviews
    
    # Group reviews by content type and object ID for fast lookup
    grouped_reviews = {}
    for review in all_reviews:
        key = (review['content_type_id'], review['object_id'])
        if key not in grouped_reviews:
            grouped_reviews[key] = []
        grouped_reviews[key].append(review)
    
    # Process each item group
    for (ct_id, obj_id), item_group in grouped_items.items():
        # Get reviews for this specific media item from our cache
        other_reviews = grouped_reviews.get((ct_id, obj_id), [])
        
        # Create review data
        review_data = [
            {'username': review['user__username'], 'rating': review['rating']}
            for review in other_reviews
        ]
        
        # Calculate statistics
        avg_rating = None
        rating_count = len(other_reviews)
        if other_reviews:
            avg_rating = round(sum(review['rating'] for review in other_reviews) / rating_count, 1)
        
        
        # Apply to all related items
        for item in item_group:
            item.other_reviews = review_data
            item.avg_rating = avg_rating
            item.rating_count = rating_count

@login_required
def api_watchlist(request):
    """API endpoint for filtering watchlist items - optimized for performance."""
    
    user = request.user
    search_query = request.GET.get('search', '')
    genre_id = request.GET.get('genre', '')
    country_id = request.GET.get('country', '')
    sort_by = request.GET.get('sort_by', 'date_added')
    
    # Get user's watchlist items
    watchlist_items = user.get_watchlist()
    
    if not watchlist_items:
        return JsonResponse({
            'continue_watching': [],
            'havent_started': [],
            'finished_shows': [],
            'movies': [],
        })
    
    # Get content types
    movie_ct = ContentType.objects.get_for_model(Movie)
    tv_ct = ContentType.objects.get_for_model(TVShow)
    
    # Group items by content type for bulk fetching
    items_by_content_type = {}
    for item in watchlist_items:
        if item.content_type_id not in items_by_content_type:
            items_by_content_type[item.content_type_id] = []
        items_by_content_type[item.content_type_id].append(item)
    
    # Bulk fetch all media objects
    media_objects = {}
    
    # Fetch movies
    if movie_ct.id in items_by_content_type:
        movie_items = items_by_content_type[movie_ct.id]
        movie_ids = [item.object_id for item in movie_items]
        movies_by_id = {m.id: m for m in Movie.objects.filter(id__in=movie_ids)}
        for item in movie_items:
            media_objects[(item.content_type_id, item.object_id)] = movies_by_id.get(item.object_id)
    
    # Fetch TV shows
    if tv_ct.id in items_by_content_type:
        tv_items = items_by_content_type[tv_ct.id]
        tv_ids = [item.object_id for item in tv_items]
        tvshows_by_id = {t.id: t for t in TVShow.objects.filter(id__in=tv_ids)}
        for item in tv_items:
            media_objects[(item.content_type_id, item.object_id)] = tvshows_by_id.get(item.object_id)
    
    # Apply search filter if provided
    if search_query:
        from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
        
        # Get media objects for search
        movie_items = items_by_content_type.get(movie_ct.id, [])
        tv_items = items_by_content_type.get(tv_ct.id, [])
        
        movie_ids = [item.object_id for item in movie_items]
        tv_ids = [item.object_id for item in tv_items]
        
        search_query_obj = SearchQuery(search_query, config='english')
        
        # Search movies with PostgreSQL full-text search
        movie_matches = set()
        if movie_ids:
            movie_matches = set(Movie.objects.filter(id__in=movie_ids).annotate(
                search=SearchVector('title', 'original_title', config='english'),
                rank=SearchRank(SearchVector('title', 'original_title', config='english'), search_query_obj)
            ).filter(search=search_query_obj).values_list('id', flat=True))
            
            # Fallback search if no results
            if not movie_matches:
                movie_matches = set(Movie.objects.filter(
                    id__in=movie_ids
                ).filter(
                    Q(title__icontains=search_query) | 
                    Q(original_title__icontains=search_query)
                ).values_list('id', flat=True))
        
        # Search TV shows with PostgreSQL full-text search
        tv_matches = set()
        if tv_ids:
            tv_matches = set(TVShow.objects.filter(id__in=tv_ids).annotate(
                search=SearchVector('title', 'original_title', config='english'),
                rank=SearchRank(SearchVector('title', 'original_title', config='english'), search_query_obj)
            ).filter(search=search_query_obj).values_list('id', flat=True))
            
            # Fallback search if no results
            if not tv_matches:
                tv_matches = set(TVShow.objects.filter(
                    id__in=tv_ids
                ).filter(
                    Q(title__icontains=search_query) | 
                    Q(original_title__icontains=search_query)
                ).values_list('id', flat=True))
        
        # Filter watchlist items based on search matches
        filtered_items = []
        for item in watchlist_items:
            if item.content_type_id == movie_ct.id and item.object_id in movie_matches:
                filtered_items.append(item)
            elif item.content_type_id == tv_ct.id and item.object_id in tv_matches:
                filtered_items.append(item)
            elif item.content_type_id not in (movie_ct.id, tv_ct.id):
                # Keep other content types
                filtered_items.append(item)
        
        watchlist_items = filtered_items
    
    # Apply genre filter if provided
    if genre_id:
        filtered_items = []
        for item in watchlist_items:
            media = media_objects.get((item.content_type_id, item.object_id))
            if media and hasattr(media, 'genres') and media.genres.filter(id=genre_id).exists():
                filtered_items.append(item)
        watchlist_items = filtered_items
    
    # Apply country filter if provided
    if country_id:
        filtered_items = []
        for item in watchlist_items:
            media = media_objects.get((item.content_type_id, item.object_id))
            if media and hasattr(media, 'countries') and media.countries.filter(id=country_id).exists():
                filtered_items.append(item)
        watchlist_items = filtered_items

    # Add review data to items
    add_review_data_to_items(watchlist_items, user)
    
    # Apply sorting before categorizing
    if sort_by:
        if sort_by == 'date_added':
            watchlist_items = sorted(watchlist_items, key=lambda x: x.date_added, reverse=True)
        elif sort_by == '-date_added':
            watchlist_items = sorted(watchlist_items, key=lambda x: x.date_added)
        elif sort_by == 'title':
            def get_title(item):
                media = media_objects.get((item.content_type_id, item.object_id))
                return media.title.lower() if media else ''
            watchlist_items = sorted(watchlist_items, key=get_title)
        elif sort_by == '-title':
            def get_title(item):
                media = media_objects.get((item.content_type_id, item.object_id))
                return media.title.lower() if media else ''
            watchlist_items = sorted(watchlist_items, key=get_title, reverse=True)
        elif sort_by == 'release_date':
            def get_release_date(item):
                media = media_objects.get((item.content_type_id, item.object_id))
                if not media:
                    return datetime.min
                if hasattr(media, 'release_date') and media.release_date:
                    return media.release_date
                elif hasattr(media, 'first_air_date') and media.first_air_date:
                    return media.first_air_date
                return datetime.min
            watchlist_items = sorted(watchlist_items, key=get_release_date)
        elif sort_by == '-release_date':
            def get_release_date(item):
                media = media_objects.get((item.content_type_id, item.object_id))
                if not media:
                    return datetime.min
                if hasattr(media, 'release_date') and media.release_date:
                    return media.release_date
                elif hasattr(media, 'first_air_date') and media.first_air_date:
                    return media.first_air_date
                return datetime.min
            watchlist_items = sorted(watchlist_items, key=get_release_date, reverse=True)
    
    # Categorize items with bulk-fetched media data
    continue_watching = []
    havent_started = []
    finished_shows = []
    movies = []
    
    # Separate TV shows and movies
    tv_shows_items = []
    
    for item in watchlist_items:
        media = media_objects.get((item.content_type_id, item.object_id))
        if not media:
            continue  # Skip items with missing media
            
        if item.content_type_id == movie_ct.id:
            serialized = serialize_watchlist_item(item, media)
            if serialized:
                movies.append(serialized)
        elif item.content_type_id == tv_ct.id:
            tv_shows_items.append(item)
    
    # If there are TV shows, batch-fetch their watch progress
    if tv_shows_items:
        # Get all TV show IDs
        tv_show_ids = [item.object_id for item in tv_shows_items]
        
        # Get all episodes for these shows that have aired
        from tvshows.models import Episode
        total_episodes = Episode.objects.filter(
            season__show_id__in=tv_show_ids,
            season__season_number__gt=0,
            air_date__isnull=False,
            air_date__lte=timezone.now()
        ).values('season__show_id').annotate(
            count=models.Count('id')
        )
        
        # Get all watched episodes for these shows by this user
        from tvshows.models import WatchedEpisode
        watched_episodes = WatchedEpisode.objects.filter(
            user=user,
            episode__season__show_id__in=tv_show_ids,
            episode__season__season_number__gt=0
        ).values('episode__season__show_id').annotate(
            count=models.Count('id')
        )
        
        # Create mappings for fast lookup
        total_episodes_map = {item['season__show_id']: item['count'] for item in total_episodes}
        watched_episodes_map = {item['episode__season__show_id']: item['count'] for item in watched_episodes}
        
        # Now categorize TV shows with the pre-fetched data
        for item in tv_shows_items:
            media = media_objects.get((item.content_type_id, item.object_id))
            if not media:
                continue
                
            show_id = item.object_id
            total = total_episodes_map.get(show_id, 0)
            watched = watched_episodes_map.get(show_id, 0)
            
            # Calculate progress percentage
            progress = (watched / total * 100) if total > 0 else 0
            
            # Serialize with bulk-fetched media
            serialized = serialize_watchlist_item(item, media)
            if not serialized:
                continue
            
            # Categorize based on progress
            if progress >= 100:
                finished_shows.append(serialized)
            elif progress > 0:
                continue_watching.append({'item': serialized, 'progress': progress})
            else:
                havent_started.append(serialized)
    
    return JsonResponse({
        'continue_watching': continue_watching,
        'havent_started': havent_started,
        'finished_shows': finished_shows,
        'movies': movies,
    })

def serialize_watchlist_item(item, media_obj=None):
    """Helper function to serialize a watchlist item for JSON response."""
    # Use prefetched media object if provided, otherwise fall back to GenericForeignKey
    media = media_obj if media_obj else item.media
    
    if not media:
        return None  # Skip items with no media
    
    result = {
        'id': item.id,
        'content_type_model': item.content_type.model,  # Flattened
        'media_id': media.id,                          # Flattened
        'media_title': media.title,                    # Flattened
        'media_poster': media.poster,                  # Flattened
        'media_tmdb_id': media.tmdb_id,               # Flattened
        'date_added': item.date_added.isoformat(),
    }
    
    # Add release date if available
    if hasattr(media, 'release_date') and media.release_date:
        result['media_release_date'] = media.release_date.isoformat()  # Flattened
    elif hasattr(media, 'first_air_date') and media.first_air_date:
        result['media_first_air_date'] = media.first_air_date.isoformat()  # Flattened
    
    # Add rating if available
    if hasattr(item, 'avg_rating'):
        result['avg_rating'] = item.avg_rating
        result['rating_count'] = item.rating_count
    
    return result

@require_POST
@login_required
def remove_from_watchlist(request):
    """API endpoint to remove an item from watchlist."""
    try:
        data = json.loads(request.body)
        item_id = data.get('item_id')
        
        if not item_id:
            return JsonResponse({
                'success': False,
                'error': 'Item ID is required'
            }, status=400)
        
        # Find and delete the watchlist item
        try:
            watchlist_item = Watchlist.objects.get(id=item_id, user=request.user)
            watchlist_item.delete()
            return JsonResponse({
                'success': True,
                'message': 'Item removed from watchlist'
            })
            
        except Watchlist.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Item not found in your watchlist'
            }, status=404)
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON in request body'
        }, status=400)

@login_required
def people_detail(request, person_id):
    """
    View for displaying details about a single person and their filmography
    """
    # Get the person object or return 404
    person = get_object_or_404(Person, id=person_id)
    
    # Get all media credits for this person
    person_credits_qs = MediaPerson.objects.filter(person=person).select_related(
        'content_type'
    )
    
    # Group credits by media and collect media identifiers
    grouped_credits = {}
    media_identifiers = set()
    
    for credit in person_credits_qs:
        try:
            # Get the actual movie or TV show object via the generic relation
            media = credit.content_type.get_object_for_this_type(id=credit.object_id)
            key = (credit.content_type_id, credit.object_id)
            media_identifiers.add(key)
            
            if key not in grouped_credits:
                grouped_credits[key] = {
                    'media': media,
                    'roles': set(), # Use set to avoid duplicate roles
                    'characters': set() # Use set for unique characters
                }
            
            grouped_credits[key]['roles'].add(credit.role)
            if credit.character_name:
                grouped_credits[key]['characters'].add(credit.character_name)
                
        except (Movie.DoesNotExist, TVShow.DoesNotExist):
            continue # Skip if the related media object doesn't exist
            
    # Calculate average rating for the person's works (all users)
    average_rating = None
    if media_identifiers:
        # Build Q objects for filtering reviews
        q_objects = Q()
        for ct_id, obj_id in media_identifiers:
            q_objects |= Q(content_type_id=ct_id, object_id=obj_id)
            
        # Calculate average rating across all users
        rating_data = Review.objects.filter(q_objects).aggregate(Avg('rating'))
        if rating_data['rating__avg'] is not None:
            average_rating = round(rating_data['rating__avg'], 1)

        # Calculate average rating for the current user's reviews
        user_average_rating = None
        user_rating_data = Review.objects.filter(q_objects, user=request.user).aggregate(Avg('rating'))
        if user_rating_data['rating__avg'] is not None:
            user_average_rating = round(user_rating_data['rating__avg'], 1)

    # Convert grouped credits to a list for sorting and template iteration
    combined_credits_list = list(grouped_credits.values())
    
    # Add rating data for each media item
    if combined_credits_list:
        movie_content_type = ContentType.objects.get_for_model(Movie)
        tv_show_content_type = ContentType.objects.get_for_model(TVShow)
        
        # Get user reviews for all media items
        user_reviews = {}
        for review in Review.objects.filter(q_objects, user=request.user):
            key = (review.content_type_id, review.object_id)
            user_reviews[key] = review.rating
            
        # Get average ratings for all media items
        avg_ratings = {}
        rating_counts = {}
        for item in Review.objects.filter(q_objects).values(
            'content_type_id', 'object_id'
        ).annotate(
            avg_rating=models.Avg('rating'),
            count=models.Count('id')
        ):
            key = (item['content_type_id'], item['object_id'])
            avg_ratings[key] = round(item['avg_rating'], 1)
            rating_counts[key] = item['count']
            
        # Add rating data to each credit
        for credit in combined_credits_list:
            media = credit['media']
            if hasattr(media, 'release_date'):  # Movie
                content_type_id = movie_content_type.id
            else:  # TV Show
                content_type_id = tv_show_content_type.id
                
            key = (content_type_id, media.id)
            
            # Add user rating
            credit['user_rating'] = user_reviews.get(key)
            
            # Add average rating
            if key in avg_ratings:
                credit['avg_rating'] = avg_ratings[key]
                credit['rating_count'] = rating_counts[key]
    
    # Sort combined credits by release date (newest first)
    combined_credits_list = sorted(
        combined_credits_list,
        key=lambda x: (
            getattr(x['media'], 'release_date', None) or 
            getattr(x['media'], 'first_air_date', None) or 
            date.min # Fallback for items without a date
        ),
        reverse=True
    )
    
    # Select a random backdrop from the person's filmography
    random_backdrop = None
    backdrop_candidates = []
    
    for credit in combined_credits_list:
        media = credit['media']
        # Check if media has a valid backdrop property
        backdrop = getattr(media, 'backdrop', None)
        if backdrop:
            backdrop_candidates.append(backdrop)
    
    # Choose a random backdrop if available
    if backdrop_candidates:
        random_backdrop = random.choice(backdrop_candidates)
    
    context = {
        'person': person,
        'combined_credits': combined_credits_list,
        'average_rating': average_rating, # Add average rating to context
        'user_average_rating': user_average_rating, # Add user-specific average rating
        'random_backdrop': random_backdrop, # Add random backdrop to context
    }
    
    return render(request, 'people_page.html', context)

@login_required
def get_person_tmdb_filmography(request, person_id):
    """
    API endpoint to fetch a person's filmography from TMDB
    Returns both movie and TV show credits
    """
    person = get_object_or_404(Person, pk=person_id)
    
    if not person.tmdb_id:
        return JsonResponse({'error': 'This person does not have a TMDB ID'}, status=400)
    
    try:
        from api.services.movies import MoviesService
        from api.services.tvshows import TVShowsService
        
        movies_service = MoviesService()
        tvshows_service = TVShowsService()
        
        # Fetch movie and TV credits from TMDB
        movie_credits_data = movies_service.get_person_movie_credits(person.tmdb_id)
        tv_credits_data = tvshows_service.get_person_tv_credits(person.tmdb_id)
        
        # Process movie credits
        movies = []
        if movie_credits_data and 'cast' in movie_credits_data:
            for movie in movie_credits_data['cast']:
                movies.append({
                    'id': movie.get('id'),
                    'title': movie.get('title', 'Unknown'),
                    'original_title': movie.get('original_title', ''),
                    'release_date': movie.get('release_date', ''),
                    'character': movie.get('character', ''),
                    'poster_path': f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get('poster_path') else None,
                    'vote_average': movie.get('vote_average', 0),
                    'role_type': 'Acting',
                    'in_database': Movie.objects.filter(tmdb_id=movie.get('id')).exists()
                })
        
        if movie_credits_data and 'crew' in movie_credits_data:
            for movie in movie_credits_data['crew']:
                movies.append({
                    'id': movie.get('id'),
                    'title': movie.get('title', 'Unknown'),
                    'original_title': movie.get('original_title', ''),
                    'release_date': movie.get('release_date', ''),
                    'job': movie.get('job', ''),
                    'department': movie.get('department', ''),
                    'poster_path': f"https://image.tmdb.org/t/p/w500{movie['poster_path']}" if movie.get('poster_path') else None,
                    'vote_average': movie.get('vote_average', 0),
                    'role_type': 'Crew',
                    'in_database': Movie.objects.filter(tmdb_id=movie.get('id')).exists()
                })
        
        # Process TV credits
        tv_shows = []
        if tv_credits_data and 'cast' in tv_credits_data:
            for show in tv_credits_data['cast']:
                tv_shows.append({
                    'id': show.get('id'),
                    'title': show.get('name', 'Unknown'),
                    'original_title': show.get('original_name', ''),
                    'first_air_date': show.get('first_air_date', ''),
                    'character': show.get('character', ''),
                    'episode_count': show.get('episode_count', 0),
                    'poster_path': f"https://image.tmdb.org/t/p/w500{show['poster_path']}" if show.get('poster_path') else None,
                    'vote_average': show.get('vote_average', 0),
                    'role_type': 'Acting',
                    'in_database': TVShow.objects.filter(tmdb_id=show.get('id')).exists()
                })
        
        if tv_credits_data and 'crew' in tv_credits_data:
            for show in tv_credits_data['crew']:
                tv_shows.append({
                    'id': show.get('id'),
                    'title': show.get('name', 'Unknown'),
                    'original_title': show.get('original_name', ''),
                    'first_air_date': show.get('first_air_date', ''),
                    'job': show.get('job', ''),
                    'department': show.get('department', ''),
                    'episode_count': show.get('episode_count', 0),
                    'poster_path': f"https://image.tmdb.org/t/p/w500{show['poster_path']}" if show.get('poster_path') else None,
                    'vote_average': show.get('vote_average', 0),
                    'role_type': 'Crew',
                    'in_database': TVShow.objects.filter(tmdb_id=show.get('id')).exists()
                })
        
        # Sort by date (most recent first)
        movies.sort(key=lambda x: x.get('release_date', ''), reverse=True)
        tv_shows.sort(key=lambda x: x.get('first_air_date', ''), reverse=True)
        
        return JsonResponse({
            'movies': movies,
            'tv_shows': tv_shows
        })
        
    except Exception as e:
        logger.error(f"Error fetching TMDB filmography for person {person_id}: {str(e)}")
        return JsonResponse({'error': f'Failed to fetch filmography: {str(e)}'}, status=500)

@login_required
def country_detail(request, country_id):
    """
    View for displaying all movies and TV shows from a specific country
    with filtering, sorting, and watched status options
    """
    country = get_object_or_404(Country, pk=country_id)
    anime_filter = request.GET.get('anime_filter', 'all')
    view_type = request.GET.get('view_type', 'grid')
    sort_by = request.GET.get('sort_by', 'title')
    sort_order = request.GET.get('sort_order', 'asc')
    watched_status = request.GET.get('watched_status', 'all')
    
    # Get content types for Movie and TVShow models
    movie_content_type = ContentType.objects.get_for_model(Movie)
    tv_show_content_type = ContentType.objects.get_for_model(TVShow)
    
    # Filter movies and TV shows based on anime_filter
    if (anime_filter == 'anime_only'):
        movies = Movie.objects.filter(countries=country, is_anime=True)
        tv_shows = TVShow.objects.filter(countries=country, is_anime=True)
    elif (anime_filter == 'no_anime'):
        movies = Movie.objects.filter(countries=country, is_anime=False)
        tv_shows = TVShow.objects.filter(countries=country, is_anime=False)
    else:  # 'all' is the default
        movies = Movie.objects.filter(countries=country)
        tv_shows = TVShow.objects.filter(countries=country)
    
    # Apply sorting to movies and TV shows
    # Define sort fields based on sort_by parameter
    if sort_by == 'release_date':
        movie_sort_field = 'release_date'
        tv_sort_field = 'first_air_date'
    elif sort_by == 'rating':
        # For rating, we'll sort by the tmdb rating first, then handle user rating later
        movie_sort_field = 'rating'
        tv_sort_field = 'rating'
    elif sort_by == 'user_rating':
        # We'll handle user rating sorting after getting the reviews
        movie_sort_field = 'title'  # Default fallback for initial query
        tv_sort_field = 'title'
    else:  # 'title' is the default
        movie_sort_field = 'title'
        tv_sort_field = 'title'
    
    # Apply sort direction
    if sort_order == 'desc':
        movie_sort_field = f'-{movie_sort_field}'
        tv_sort_field = f'-{tv_sort_field}'
    
    # Sort the querysets (except for user_rating which we'll handle later)
    if sort_by != 'user_rating':
        movies = movies.order_by(movie_sort_field)
        tv_shows = tv_shows.order_by(tv_sort_field)
    else:
        # For user rating, we'll sort after getting the reviews
        movies = movies.order_by('title')  # Default order first
        tv_shows = tv_shows.order_by('title')
      # Handle user rating sorting if selected (before filtering to preserve QuerySet)
    if sort_by == 'user_rating':
        # Get user's reviews first
        user_movie_reviews = {
            review.object_id: review.rating 
            for review in Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movies.values_list('id', flat=True)
            )
        }
        
        user_tv_reviews = {
            review.object_id: review.rating 
            for review in Review.objects.filter(
                user=request.user,
                content_type=tv_show_content_type,
                object_id__in=tv_shows.values_list('id', flat=True)
            )
        }
        
        # Convert querysets to lists for custom sorting
        movies_list = list(movies)
        tv_shows_list = list(tv_shows)
        
        # Annotate with user ratings first
        for movie in movies_list:
            movie.user_rating = user_movie_reviews.get(movie.id)
        for tv_show in tv_shows_list:
            tv_show.user_rating = user_tv_reviews.get(tv_show.id)
        
        # Sort by user rating (None values go to the end)
        movies_list.sort(
            key=lambda x: (x.user_rating is None, x.user_rating or 0),
            reverse=(sort_order == 'desc')
        )
        tv_shows_list.sort(
            key=lambda x: (x.user_rating is None, x.user_rating or 0),
            reverse=(sort_order == 'desc')
        )
        
        # Replace the querysets with sorted lists
        movies = movies_list
        tv_shows = tv_shows_list
      # Handle user rating sorting if selected (before filtering to preserve QuerySet)
    if sort_by == 'user_rating':
        # Get user's reviews first
        user_movie_reviews = {
            review.object_id: review.rating 
            for review in Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movies.values_list('id', flat=True)
            )
        }
        
        user_tv_reviews = {
            review.object_id: review.rating 
            for review in Review.objects.filter(
                user=request.user,
                content_type=tv_show_content_type,
                object_id__in=tv_shows.values_list('id', flat=True)
            )
        }
        
        # Convert querysets to lists for custom sorting
        movies_list = list(movies)
        tv_shows_list = list(tv_shows)
        
        # Annotate with user ratings first
        for movie in movies_list:
            movie.user_rating = user_movie_reviews.get(movie.id)
        for tv_show in tv_shows_list:
            tv_show.user_rating = user_tv_reviews.get(tv_show.id)
        
        # Sort by user rating (None values go to the end)
        movies_list.sort(
            key=lambda x: (x.user_rating is None, x.user_rating or 0),
            reverse=(sort_order == 'desc')
        )
        tv_shows_list.sort(
            key=lambda x: (x.user_rating is None, x.user_rating or 0),
            reverse=(sort_order == 'desc')
        )
        
        # Replace the querysets with sorted lists
        movies = movies_list
        tv_shows = tv_shows_list
    
    # Apply watched status filter
    if watched_status != 'all':
        if watched_status == 'watched':
            # For movies: filter to only movies the user has reviewed
            user_reviewed_movies = set(Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movies.values_list('id', flat=True) if hasattr(movies, 'values_list') else [m.id for m in movies]
            ).values_list('object_id', flat=True))
            
            if hasattr(movies, 'filter'):  # QuerySet
                movies = movies.filter(id__in=user_reviewed_movies)
            else:  # List (from user_rating sorting)
                movies = [m for m in movies if m.id in user_reviewed_movies]
            
            # For TV shows: filter to only shows where user has watched at least one episode
            from tvshows.models import WatchedEpisode
            user_watched_shows = set(WatchedEpisode.objects.filter(
                user=request.user,
                episode__season__show_id__in=tv_shows.values_list('id', flat=True) if hasattr(tv_shows, 'values_list') else [s.id for s in tv_shows]
            ).values_list('episode__season__show_id', flat=True))
            
            if hasattr(tv_shows, 'filter'):  # QuerySet
                tv_shows = tv_shows.filter(id__in=user_watched_shows)
            else:  # List (from user_rating sorting)
                tv_shows = [s for s in tv_shows if s.id in user_watched_shows]
                
        elif watched_status == 'not_watched':
            # For movies: filter to only movies the user has NOT reviewed
            user_reviewed_movies = set(Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movies.values_list('id', flat=True) if hasattr(movies, 'values_list') else [m.id for m in movies]
            ).values_list('object_id', flat=True))
            
            if hasattr(movies, 'exclude'):  # QuerySet
                movies = movies.exclude(id__in=user_reviewed_movies)
            else:  # List (from user_rating sorting)
                movies = [m for m in movies if m.id not in user_reviewed_movies]
            
            # For TV shows: filter to only shows where user has NOT watched any episodes
            from tvshows.models import WatchedEpisode
            user_watched_shows = set(WatchedEpisode.objects.filter(
                user=request.user,
                episode__season__show_id__in=tv_shows.values_list('id', flat=True) if hasattr(tv_shows, 'values_list') else [s.id for s in tv_shows]
            ).values_list('episode__season__show_id', flat=True))
            
            if hasattr(tv_shows, 'exclude'):  # QuerySet
                tv_shows = tv_shows.exclude(id__in=user_watched_shows)
            else:  # List (from user_rating sorting)
                tv_shows = [s for s in tv_shows if s.id not in user_watched_shows]
    
    # Get user's reviews and average ratings AFTER filtering (to handle both QuerySets and lists)
    # Extract IDs from either QuerySet or list
    if hasattr(movies, 'values_list'):  # QuerySet
        movie_ids = list(movies.values_list('id', flat=True))
    else:  # List
        movie_ids = [m.id for m in movies]
        
    if hasattr(tv_shows, 'values_list'):  # QuerySet
        tv_show_ids = list(tv_shows.values_list('id', flat=True))
    else:  # List
        tv_show_ids = [s.id for s in tv_shows]
    
    # Get user's reviews for movies in this country
    user_movie_reviews = {}
    if movie_ids:
        user_movie_reviews = {
            review.object_id: review.rating 
            for review in Review.objects.filter(
                user=request.user,
                content_type=movie_content_type,
                object_id__in=movie_ids
            )
        }
    
    # Get average movie ratings across the platform
    avg_movie_ratings = {}
    movie_rating_counts = {}
    if movie_ids:
        for item in Review.objects.filter(
            content_type=movie_content_type,
            object_id__in=movie_ids
        ).values('object_id').annotate(
            avg_rating=models.Avg('rating'),
            count=models.Count('id')
        ):
            avg_movie_ratings[item['object_id']] = round(item['avg_rating'], 1)
            movie_rating_counts[item['object_id']] = item['count']
    
    # Get user's reviews for TV shows in this country
    user_tv_reviews = {}
    if tv_show_ids:
        user_tv_reviews = {
            review.object_id: review.rating 
            for review in Review.objects.filter(
                user=request.user,
                content_type=tv_show_content_type,
                object_id__in=tv_show_ids
            )
        }
    
    # Get average TV show ratings across the platform
    avg_tv_ratings = {}
    tv_rating_counts = {}
    if tv_show_ids:
        for item in Review.objects.filter(
            content_type=tv_show_content_type,
            object_id__in=tv_show_ids
        ).values('object_id').annotate(
            avg_rating=models.Avg('rating'),
            count=models.Count('id')
        ):
            avg_tv_ratings[item['object_id']] = round(item['avg_rating'], 1)
            tv_rating_counts[item['object_id']] = item['count']
    
    # Annotate movies with user ratings and average ratings
    for movie in movies:
        # Don't override if already set from user_rating sorting
        if not hasattr(movie, 'user_rating'):
            movie.user_rating = user_movie_reviews.get(movie.id)
        if movie.id in avg_movie_ratings:
            movie.avg_rating = avg_movie_ratings[movie.id]
            movie.rating_count = movie_rating_counts[movie.id]
    
    # Annotate TV shows with user ratings and average ratings
    for tv_show in tv_shows:
        # Don't override if already set from user_rating sorting
        if not hasattr(tv_show, 'user_rating'):
            tv_show.user_rating = user_tv_reviews.get(tv_show.id)
        if tv_show.id in avg_tv_ratings:
            tv_show.avg_rating = avg_tv_ratings[tv_show.id]
            tv_show.rating_count = tv_rating_counts[tv_show.id]
    
    return render(request, 'country_detail.html', {
        'country': country,
        'movies': movies,
        'tv_shows': tv_shows,
        'anime_filter': anime_filter,
        'view_type': view_type,
        'sort_by': sort_by,
        'sort_order': sort_order,
        'watched_status': watched_status,
    })

def recent_reviews(request):
    """
    Returns recent reviews across all content types as JSON
    """
    # Fetch 10 most recent reviews
    reviews = Review.objects.all().order_by('-date_added')[:10]
    
    # Format data for frontend
    review_data = []
    for review in reviews:
        # Get the content object that was reviewed
        content_object = review.media
        
        # Determine content type and title
        if isinstance(content_object, Movie):
            content_type = "Movie"
            title = content_object.title
        elif isinstance(content_object, TVShow):
            content_type = "TV Show"
            title = content_object.title
            if review.season:
                title += f" - {review.season}"
            elif review.episode_subgroup:
                title += f" - {review.episode_subgroup.name}"
        else:
            # Default fallback
            content_type = review.content_type.model.capitalize()
            title = getattr(content_object, 'title', 'Unknown')
        
        # Build review data object
        review_data.append({
            'username': review.user.username,
            'content': review.review_text[:100] + '...' if review.review_text and len(review.review_text) > 100 else review.review_text,
            'title': title,
            'rating': review.rating,
            'content_type': content_type,
            'created_at': review.date_added.strftime('%Y-%m-%d %H:%M')
        })
    return JsonResponse(review_data, safe=False)

def recent_activity(request):
    """
    Improved version of recent_activity with better performance and maintainability.
    """
    # Get pagination parameters
    page = int(request.GET.get('page', '1'))
    limit = int(request.GET.get('limit', '15'))
    offset = (page - 1) * limit
    
    # Use a more conservative fetch limit to improve performance
    fetch_limit = limit * 3 
    
    try:
        # Get content types once at the beginning
        content_types = _get_content_types()
        
        # Fetch all activities in parallel using a more efficient approach
        activities_data = _fetch_activities_efficiently(fetch_limit, content_types)
        
        # Process and group activities
        grouped_activities = _process_and_group_activities(activities_data, content_types)
        
        # Apply pagination
        total_count = len(grouped_activities)
        paginated_results = grouped_activities[offset:offset + limit]
        
        # Optimize poster URLs for better performance
        _optimize_poster_urls(paginated_results)
        
        return JsonResponse({
            'results': paginated_results,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total_count,
                'has_more': (offset + limit) < total_count
            }
        })
        
    except Exception as e:
        logger.error(f"Error in recent_activity: {str(e)}")
        return JsonResponse({'error': 'Internal server error'}, status=500)


def _get_content_types():
    """Cache content types to avoid repeated queries."""
    from movies.models import Movie
    from tvshows.models import TVShow
    from games.models import Game
    
    return {
        'movie': ContentType.objects.get_for_model(Movie),
        'tvshow': ContentType.objects.get_for_model(TVShow),
        'game': ContentType.objects.get_for_model(Game)
    }


def _fetch_activities_efficiently(fetch_limit, content_types):
    """
    Fetch all activities using optimized queries with proper prefetching.
    """
    from custom_auth.models import Review, Watchlist
    from movies.models import Movie
    from tvshows.models import WatchedEpisode
    from games.models import Game
    
    # Use select_related and prefetch_related more efficiently
    reviews = Review.objects.select_related(
        'user', 'content_type', 'season', 'episode_subgroup'
    ).order_by('-date_added')[:fetch_limit]
    
    watchlist_items = Watchlist.objects.select_related(
        'user', 'content_type'
    ).order_by('-date_added')[:fetch_limit]
    
    movies = Movie.objects.select_related('added_by').order_by('-date_added')[:fetch_limit]
    
    games = Game.objects.select_related('added_by').order_by('-date_added')[:fetch_limit]
    
    # More efficient episode fetching with optimized select_related
    watched_episodes = WatchedEpisode.objects.select_related(
        'user', 'episode__season__show'
    ).order_by('-watched_date')[:fetch_limit * 2]  # Reduced multiplier
    
    return {
        'reviews': reviews,
        'watchlist_items': watchlist_items,
        'movies': movies,
        'games': games,
        'watched_episodes': watched_episodes
    }


def _get_media_objects_bulk(activities_data, content_types):
    """
    Efficiently fetch all required media objects in bulk.
    """
    from movies.models import Movie
    from tvshows.models import TVShow
    from games.models import Game
    
    # Collect all required media IDs
    movie_ids = set()
    tvshow_ids = set()
    game_ids = set()
    
    # From reviews
    for review in activities_data['reviews']:
        if review.content_type_id == content_types['movie'].id:
            movie_ids.add(review.object_id)
        elif review.content_type_id == content_types['tvshow'].id:
            tvshow_ids.add(review.object_id)
        elif review.content_type_id == content_types['game'].id:
            game_ids.add(review.object_id)
    
    # From watchlist items
    for item in activities_data['watchlist_items']:
        if item.content_type_id == content_types['movie'].id:
            movie_ids.add(item.object_id)
        elif item.content_type_id == content_types['tvshow'].id:
            tvshow_ids.add(item.object_id)
        elif item.content_type_id == content_types['game'].id:
            game_ids.add(item.object_id)
    
    # From watched episodes
    for episode in activities_data['watched_episodes']:
        tvshow_ids.add(episode.episode.season.show.id)
    
    # Bulk fetch media objects
    movies_by_id = {}
    tvshows_by_id = {}
    games_by_id = {}
    
    if movie_ids:
        movies_by_id = {m.id: m for m in Movie.objects.filter(id__in=movie_ids)}
    
    if tvshow_ids:
        tvshows_by_id = {t.id: t for t in TVShow.objects.filter(id__in=tvshow_ids)}
    
    if game_ids:
        games_by_id = {g.id: g for g in Game.objects.filter(id__in=game_ids)}
    
    return movies_by_id, tvshows_by_id, games_by_id


def _process_and_group_activities(activities_data, content_types):
    """
    Process activities and group them efficiently.
    """
    # Get media objects in bulk
    movies_by_id, tvshows_by_id, games_by_id = _get_media_objects_bulk(activities_data, content_types)
    
    # Process each activity type
    all_activities = []
    
    # Process watched episodes (group by day/user/show first)
    episode_activities = _process_watched_episodes(
        activities_data['watched_episodes'], tvshows_by_id
    )
    all_activities.extend(episode_activities)
    
    # Process reviews
    review_activities = _process_reviews(
        activities_data['reviews'], movies_by_id, tvshows_by_id, games_by_id, content_types
    )
    all_activities.extend(review_activities)
    
    # Process watchlist items
    watchlist_activities = _process_watchlist_items(
        activities_data['watchlist_items'], movies_by_id, tvshows_by_id, games_by_id, content_types
    )
    all_activities.extend(watchlist_activities)
    
    # Process new movies
    movie_activities = _process_new_movies(activities_data['movies'])
    all_activities.extend(movie_activities)
    
    # Process new games
    game_activities = _process_new_games(activities_data['games'])
    all_activities.extend(game_activities)
    
    # Sort by date and group by timestamp/media
    all_activities.sort(key=lambda x: x['date'], reverse=True)
    
    return _group_activities_by_timestamp_and_media(all_activities)


def _process_watched_episodes(watched_episodes, tvshows_by_id):
    """Group and process watched episodes efficiently."""
    # Group episodes by day, TV show, and user
    episode_groups = defaultdict(list)
    
    for episode in watched_episodes:
        if not hasattr(episode.episode, 'season') or not hasattr(episode.episode.season, 'show'):
            continue
            
        day_key = episode.watched_date.strftime('%Y-%m-%d')
        show_id = episode.episode.season.show.id
        user_id = episode.user.id
        group_key = (day_key, show_id, user_id)
        episode_groups[group_key].append(episode)
    
    activities = []
    for (day_key, show_id, user_id), episodes in episode_groups.items():
        if not episodes:
            continue
            
        # Sort episodes by watched_date and get the most recent
        episodes.sort(key=lambda e: e.watched_date, reverse=True)
        most_recent = episodes[0]
        
        tv_show = tvshows_by_id.get(show_id)
        if not tv_show:
            continue
        
        # Create activity data
        local_timestamp = timezone.localtime(most_recent.watched_date)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        episode_count = len(episodes)
        
        activities.append({
            'type': 'watched_episodes',
            'username': most_recent.user.username,
            'title': _format_title(tv_show),
            'content_type': 'TV Show',
            'date': most_recent.watched_date,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': tv_show.id,
            'action': f'watched {episode_count} episode{"s" if episode_count > 1 else ""}',
            'poster_path': tv_show.poster,
            'tmdb_id': tv_show.tmdb_id,
            'episode_count': episode_count
        })
    
    return activities


def _process_reviews(reviews, movies_by_id, tvshows_by_id, games_by_id, content_types):
    """Process review activities efficiently."""
    activities = []
    
    for review in reviews:
        # Get content object efficiently
        content_object = None
        if review.content_type_id == content_types['movie'].id:
            content_object = movies_by_id.get(review.object_id)
            content_type = "Movie"
        elif review.content_type_id == content_types['tvshow'].id:
            content_object = tvshows_by_id.get(review.object_id)
            content_type = "TV Show"
        elif review.content_type_id == content_types['game'].id:
            content_object = games_by_id.get(review.object_id)
            content_type = "Game"
        else:
            # Fallback for other content types
            content_object = review.media
            content_type = review.content_type.model.capitalize()
        
        if not content_object:
            continue
        
        # Format title
        title = _format_title(content_object)
        if hasattr(content_object, 'first_air_date'):  # TV Show
            if review.season:
                title += f" - {review.season.title}"
            elif review.episode_subgroup:
                title += f" - {review.episode_subgroup.name}"
        
        # Create timestamp
        local_timestamp = timezone.localtime(review.date_added)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        
        activities.append({
            'type': 'review',
            'username': review.user.username,
            'content': _truncate_text(review.review_text, 100),
            'title': title,
            'rating': review.rating,
            'content_type': content_type,
            'date': review.date_added,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': content_object.id,
            'poster_path': getattr(content_object, 'poster', None),
            'tmdb_id': getattr(content_object, 'tmdb_id', None),
            'rawg_id': getattr(content_object, 'rawg_id', None)
        })
    
    return activities


def _process_watchlist_items(watchlist_items, movies_by_id, tvshows_by_id, games_by_id, content_types):
    """Process watchlist activities efficiently."""
    activities = []
    
    for item in watchlist_items:
        # Get content object efficiently
        content_object = None
        if item.content_type_id == content_types['movie'].id:
            content_object = movies_by_id.get(item.object_id)
            content_type = "Movie"
        elif item.content_type_id == content_types['tvshow'].id:
            content_object = tvshows_by_id.get(item.object_id)
            content_type = "TV Show"
        elif item.content_type_id == content_types['game'].id:
            content_object = games_by_id.get(item.object_id)
            content_type = "Game"
        else:
            content_object = item.media
            content_type = item.content_type.model.capitalize()
        
        if not content_object:
            continue
        
        # Create timestamp
        local_timestamp = timezone.localtime(item.date_added)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        
        activities.append({
            'type': 'watchlist',
            'username': item.user.username,
            'title': _format_title(content_object),
            'content_type': content_type,
            'date': item.date_added,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': content_object.id,
            'action': 'added to watchlist',
            'poster_path': getattr(content_object, 'poster', None),
            'tmdb_id': getattr(content_object, 'tmdb_id', None),
            'rawg_id': getattr(content_object, 'rawg_id', None)
        })
    
    return activities


def _process_new_movies(movies):
    """Process new movie activities efficiently."""
    activities = []
    
    for movie in movies:
        local_timestamp = timezone.localtime(movie.date_added)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        
        activities.append({
            'type': 'new_content',
            'username': movie.added_by.username if movie.added_by else 'System',
            'title': _format_title(movie),
            'content_type': 'Movie',
            'date': movie.date_added,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': movie.id,
            'action': 'added to database',
            'poster_path': movie.poster,
            'tmdb_id': movie.tmdb_id,
            'rawg_id': None
        })
    
    return activities


def _process_new_games(games):
    """Process new game activities efficiently."""
    activities = []
    
    for game in games:
        local_timestamp = timezone.localtime(game.date_added)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        
        activities.append({
            'type': 'new_content',
            'username': game.added_by.username if game.added_by else 'System',
            'title': _format_title(game),
            'content_type': 'Game',
            'date': game.date_added,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': game.id,
            'action': 'added to database',
            'poster_path': game.poster,
            'tmdb_id': None,
            'rawg_id': game.rawg_id
        })
    
    return activities


def _group_activities_by_timestamp_and_media(activities):
    """
    Group activities by timestamp and media for better presentation.
    Activities by the same user on the same media within 5 minutes are grouped together.
    """
    from datetime import timedelta
    
    # First, group activities by user and media
    user_media_groups = {}
    
    for activity in activities:
        user_media_key = (activity['username'], activity['media_id'])
        if user_media_key not in user_media_groups:
            user_media_groups[user_media_key] = []
        user_media_groups[user_media_key].append(activity)
    
    # Now group activities within each user-media group by time proximity
    grouped_activities = {}
    TIME_WINDOW = timedelta(minutes=5)  # Group activities within 5 minutes
    
    for (username, media_id), user_activities in user_media_groups.items():
        # Sort activities by date for this user-media combination
        user_activities.sort(key=lambda x: x['date'])
        
        activity_groups = []
        current_group = []
        
        for activity in user_activities:
            if not current_group:
                # First activity in the group
                current_group.append(activity)
            else:
                # Check if this activity is within the time window of the latest activity in current group
                latest_activity = max(current_group, key=lambda x: x['date'])
                time_diff = activity['date'] - latest_activity['date']
                
                if time_diff <= TIME_WINDOW:
                    # Add to current group
                    current_group.append(activity)
                else:
                    # Start a new group
                    activity_groups.append(current_group)
                    current_group = [activity]
        
        # Don't forget the last group
        if current_group:
            activity_groups.append(current_group)
        
        # Process each group
        for group in activity_groups:
            # Use the latest activity's timestamp for the group
            latest_activity = max(group, key=lambda x: x['date'])
            
            # Create unique key for this group
            group_key = (latest_activity['timestamp_key'], latest_activity['title'], latest_activity['media_id'], latest_activity['username'])
            
            grouped_activities[group_key] = {
                'title': latest_activity['title'],
                'content_type': latest_activity['content_type'],
                'timestamp': latest_activity['timestamp'],
                'username': latest_activity['username'],
                'poster_path': latest_activity['poster_path'],
                'tmdb_id': latest_activity.get('tmdb_id'),
                'rawg_id': latest_activity.get('rawg_id'),
                'media_id': latest_activity['media_id'],
                'actions': []
            }
            
            # Add optional fields from the latest activity (prefer review content if available)
            review_activity = next((a for a in group if a['type'] == 'review'), None)
            source_activity = review_activity or latest_activity
            
            for field in ['content', 'rating', 'episode_count']:
                if field in source_activity:
                    grouped_activities[group_key][field] = source_activity[field]
            
            # Add all actions from the group
            action_map = {
                'review': 'reviewed',
                'watchlist': 'added to watchlist',
                'new_content': 'added to database',
                'watched_episodes': lambda a: a.get('action', 'watched episodes')
            }
            
            for activity in group:
                action_func = action_map.get(activity['type'])
                if callable(action_func):
                    action = action_func(activity)
                else:
                    action = action_func or activity.get('action', 'unknown action')
                
                if action not in grouped_activities[group_key]['actions']:
                    grouped_activities[group_key]['actions'].append(action)
    
    # Convert to list and format actions
    result_list = []
    for activity_data in grouped_activities.values():
        # Format actions into readable string
        actions = activity_data['actions']
        if len(actions) == 1:
            activity_data['action'] = actions[0]
        elif len(actions) == 2:
            activity_data['action'] = f"{actions[0]} and {actions[1]}"
        else:
            activity_data['action'] = f"{', '.join(actions[:-1])}, and {actions[-1]}"
        
        # Remove actions list
        del activity_data['actions']
        result_list.append(activity_data)
    
    # Sort by timestamp (newest first)
    result_list.sort(key=lambda x: x['timestamp'], reverse=True)
    
    return result_list


def _format_title(media_object):
    """Format title consistently."""
    if not media_object:
        return 'Unknown'
    
    title = getattr(media_object, 'title', 'Unknown')
    original_title = getattr(media_object, 'original_title', None)
    
    if original_title and title != original_title:
        return f"{title} ({original_title})"
    return title


def _truncate_text(text, max_length):
    """Safely truncate text."""
    if not text:
        return text
    if len(text) <= max_length:
        return text
    return text[:max_length] + '...'


def _optimize_poster_urls(activities):
    """
    Optimize poster URLs to use lower quality images from TMDB for better performance.
    Modifies the activities list in-place.
    """
    for activity in activities:
        poster_path = activity.get('poster_path')
        if poster_path:
            activity['poster_path'] = _convert_to_low_quality_tmdb_url(poster_path)


def _convert_to_low_quality_tmdb_url(poster_path):
    """
    Convert poster URL to use lower quality TMDB image.
    TMDB image sizes: w92, w154, w185, w342, w500, w780, original
    We'll use w185 for good balance between quality and file size.
    
    Expected input format: https://image.tmdb.org/t/p/original/dfUCs5HNtGu4fofh83uiE2Qcy3v.jpg
    Output format: https://image.tmdb.org/t/p/w185/dfUCs5HNtGu4fofh83uiE2Qcy3v.jpg
    """
    if not poster_path or not isinstance(poster_path, str):
        return poster_path
    
    # Handle TMDB URLs (which is your standard format)
    if 'image.tmdb.org/t/p/' in poster_path:
        # Simply replace /original/ with /w342/ for maximum efficiency
        if '/original/' in poster_path:
            return poster_path.replace('/original/', '/w342/')

        # Handle other sizes (w500, w780, etc.) and replace with w342
        import re
        pattern = r'/t/p/(w\d+)/'
        if re.search(pattern, poster_path):
            return re.sub(pattern, '/t/p/w185/', poster_path)
        
        # If no size is specified after /t/p/, add w185
        if '/t/p//' in poster_path:
            return poster_path.replace('/t/p//', '/t/p/w185/')
    
    # Return original if we can't process it (fallback)
    return poster_path

@login_required
def browse_by_people(request):
    """
    View for displaying the people explorer page. Uses lazy loading for better performance.
    The actual people data is loaded via AJAX using the api_people_by_category endpoint.
    """
    return render(request, 'browse_by_people.html')


@login_required
def api_people_by_category(request):
    """
    API endpoint for fetching people by category with pagination and search.
    Supports lazy loading for better performance.
    """
    from django.core.cache import cache
    
    category = request.GET.get('category', 'directors')
    page = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 30))
    search_query = request.GET.get('search', '').strip().lower()
    sort_by = request.GET.get('sort', 'rating-desc')
    
    cache_key_prefix = "people_browser_v2_"
    cache_timeout = 3600  # 1 hour cache timeout
    
    # Map category to role filter
    category_filters = {
        'directors': Q(is_director=True),
        'writers': Q(is_screenwriter=True) | Q(is_writer=True) | Q(is_story=True),
        'actors': Q(is_actor=True),
        'tv_creators': Q(is_tv_creator=True),
        'musicians': Q(is_musician=True) | Q(is_original_music_composer=True),
        'other_creators': Q(is_comic_artist=True) | Q(is_graphic_novelist=True) | Q(is_book=True) | Q(is_novelist=True) | Q(is_original_story=True),
    }
    
    role_filter = category_filters.get(category, category_filters['directors'])
    cache_key = f"{cache_key_prefix}{category}"
    
    # Try to get cached result first
    people_with_ratings = cache.get(cache_key)
    
    if not people_with_ratings:
        people_with_ratings = _fetch_people_by_role(role_filter)
        cache.set(cache_key, people_with_ratings, cache_timeout)
    
    # Apply search filter
    if search_query:
        people_with_ratings = [
            p for p in people_with_ratings 
            if search_query in p['name'].lower()
        ]
    
    # Apply sorting
    if sort_by == 'name-asc':
        people_with_ratings = sorted(people_with_ratings, key=lambda x: x['name'].lower())
    elif sort_by == 'name-desc':
        people_with_ratings = sorted(people_with_ratings, key=lambda x: x['name'].lower(), reverse=True)
    elif sort_by == 'rating-asc':
        people_with_ratings = sorted(people_with_ratings, key=lambda x: (x['avg_rating'] or 0))
    else:  # rating-desc (default)
        people_with_ratings = sorted(people_with_ratings, key=lambda x: (x['avg_rating'] or 0), reverse=True)
    
    # Paginate
    total_count = len(people_with_ratings)
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_people = people_with_ratings[start_idx:end_idx]
    
    return JsonResponse({
        'success': True,
        'people': paginated_people,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total_count,
            'total_pages': (total_count + per_page - 1) // per_page,
            'has_more': end_idx < total_count
        },
        'category': category,
    })


def _fetch_people_by_role(role_filter):
    """
    Helper function to fetch and process people by role.
    Returns serialized list of people with their ratings.
    """
    # First, identify media that has reviews
    reviewed_media = Review.objects.values('content_type_id', 'object_id').distinct()
    
    # Then get all MediaPerson entries that match those reviewed media
    reviewed_media_persons = MediaPerson.objects.filter(
        content_type_id__in=[item['content_type_id'] for item in reviewed_media],
        object_id__in=[item['object_id'] for item in reviewed_media]
    ).values('person_id').distinct()
    
    # Get people matching the role filter AND who have been in reviewed works
    people = Person.objects.filter(
        role_filter,
        profile_picture__isnull=False,
        id__in=reviewed_media_persons
    ).distinct()[:200]
    
    if not people:
        return []
    
    # Prefetch all media relations in a single query
    person_ids = [p.id for p in people]
    
    # Get all media relations for these people in one query
    media_relations = MediaPerson.objects.filter(
        person_id__in=person_ids
    ).values('person_id', 'content_type_id', 'object_id')
    
    # Group media relations by person
    person_media = {}
    for relation in media_relations:
        person_id = relation['person_id']
        if person_id not in person_media:
            person_media[person_id] = set()
        person_media[person_id].add(
            (relation['content_type_id'], relation['object_id'])
        )
    # Batch collect all content types and object IDs for reviews
    all_media_identifiers = set()
    for relations in person_media.values():
        all_media_identifiers.update(relations)
    
    # Build a map of all media ratings at once
    media_ratings = {}
    if all_media_identifiers:
        q_objects = Q()
        for ct_id, obj_id in all_media_identifiers:
            q_objects |= Q(content_type_id=ct_id, object_id=obj_id)
            
        # Get aggregated review data in one query
        review_data = Review.objects.filter(q_objects).values(
            'content_type_id', 'object_id'
        ).annotate(
            avg_rating=Avg('rating'),
            count=models.Count('id')
        )
        
        # Create a map for fast lookups
        for data in review_data:
            key = (data['content_type_id'], data['object_id'])
            media_ratings[key] = {
                'avg_rating': data['avg_rating'],
                'count': data['count']
            }
    
    # Serialize people with ratings
    people_with_ratings = []
    for person in people:
        # Get this person's media
        person_media_list = person_media.get(person.id, [])
        
        if not person_media_list:
            continue  # Skip people with no media
                  
        # Calculate average rating across all media
        total_rating = 0
        total_count = 0
        num_works = 0
        
        for media_key in person_media_list:
            num_works += 1
            if media_key in media_ratings:
                rating_data = media_ratings[media_key]
                # Weighted average: Rating * Count
                total_rating += rating_data['avg_rating'] * rating_data['count']
                total_count += rating_data['count']
        
        avg_rating = None
        if total_count > 0:
            avg_rating = round(total_rating / total_count, 1)
        
        # Serialize person data for JSON response
        people_with_ratings.append({
            'id': person.id,
            'name': person.name,
            'profile_picture': person.profile_picture or None,
            'birth_year': person.date_of_birth.year if person.date_of_birth else None,
            'avg_rating': avg_rating,
            'rating_count': total_count,
            'num_works': num_works,
        })
    
    return people_with_ratings

# Add this view function
@login_required
def statistics_page(request):
    """
    Main statistics page with tabs for different content types.
    """
    from django.utils import timezone
    
    # Get content types
    movie_content_type = ContentType.objects.get_for_model(Movie)
    tvshow_content_type = ContentType.objects.get_for_model(TVShow)
    
    # Calculate last 7 days cutoff
    seven_days_ago = timezone.now() - timedelta(days=7)
    
    # Get user's movie reviews
    movie_reviews = Review.objects.filter(
        user=request.user,
        content_type=movie_content_type
    )
    
    # Get user's movie reviews from last 7 days
    movie_reviews_last_7_days = movie_reviews.filter(
        date_added__gte=seven_days_ago
    )
    
    # Get user's TV show reviews
    tvshow_reviews = Review.objects.filter(
        user=request.user,
        content_type=tvshow_content_type
    )
    
    # Calculate movie watching time
    movie_ids = movie_reviews.values_list('object_id', flat=True)
    movies = Movie.objects.filter(id__in=movie_ids)
    movie_total_minutes = sum(movie.runtime or 0 for movie in movies)
    
    # Calculate movie watching time for last 7 days
    movie_ids_last_7_days = movie_reviews_last_7_days.values_list('object_id', flat=True)
    movies_last_7_days = Movie.objects.filter(id__in=movie_ids_last_7_days)
    movie_last_7_days_minutes = sum(movie.runtime or 0 for movie in movies_last_7_days)
    
    # Find shortest and longest movies (only from watched movies)
    shortest_movie = None
    longest_movie = None
    if movies.exists():
        # Filter out movies with no runtime and find shortest
        movies_with_runtime = movies.filter(runtime__gt=0)
        if movies_with_runtime.exists():
            shortest_movie = movies_with_runtime.order_by('runtime').first()
            longest_movie = movies_with_runtime.order_by('-runtime').first()
    
    # Calculate TV show watching time (from actual watched episodes)
    tvshow_ids = tvshow_reviews.values_list('object_id', flat=True)
    tvshows = TVShow.objects.filter(id__in=tvshow_ids)
    
    # Get actual watched episodes for these shows
    from tvshows.models import WatchedEpisode
    watched_episodes = WatchedEpisode.objects.filter(
        user=request.user,
        episode__season__show_id__in=tvshow_ids
    ).select_related('episode', 'episode__season', 'episode__season__show')
    
    # Get watched episodes from last 7 days
    watched_episodes_last_7_days = watched_episodes.filter(
        watched_date__gte=seven_days_ago
    )
    
    # Calculate total time from watched episodes
    tvshow_total_minutes = 0
    for watched_episode in watched_episodes:
        # Use episode runtime if available, otherwise default to 45 minutes
        episode_runtime = getattr(watched_episode.episode, 'runtime', None) or 45
        tvshow_total_minutes += episode_runtime
    
    # Calculate total time from watched episodes in last 7 days
    tvshow_last_7_days_minutes = 0
    for watched_episode in watched_episodes_last_7_days:
        # Use episode runtime if available, otherwise default to 45 minutes
        episode_runtime = getattr(watched_episode.episode, 'runtime', None) or 45
        tvshow_last_7_days_minutes += episode_runtime
    
    def format_time(total_minutes):
        if total_minutes == 0:
            return "0 hours (0 hours)"
        
        total_hours = total_minutes // 60
        
        # Calculate months, days, hours
        months = total_hours // (24 * 30)  # Approximate 30 days per month
        remaining_hours = total_hours % (24 * 30)
        days = remaining_hours // 24
        hours = remaining_hours % 24
        
        # Build the formatted string
        parts = []
        if months > 0:
            parts.append(f"{months} month{'s' if months != 1 else ''}")
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        
        if not parts:
            parts.append("0 hours")
        
        formatted = ", ".join(parts)
        return f"{formatted} ({total_hours:,} hours)"
    
    def format_hours_only(total_minutes):
        """Format time as just hours for the 'last 7 days' stat"""
        total_hours = total_minutes // 60
        return f"{total_hours} hour{'s' if total_hours != 1 else ''}"
    
    # Calculate monthly activity breakdown
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    import calendar
    
    # Get all years that have movie reviews
    movie_years = movie_reviews.datetimes('date_added', 'year').values_list('date_added__year', flat=True)
    available_years = sorted(set(movie_years), reverse=True)
    
    # Get selected year from request, default to current year or latest year with data
    selected_year = request.GET.get('year')
    if selected_year:
        try:
            selected_year = int(selected_year)
        except ValueError:
            selected_year = None
    
    if not selected_year:
        if available_years:
            selected_year = available_years[0]  # Most recent year with data
        else:
            selected_year = timezone.now().year
    
    # Get monthly data for selected year
    monthly_data = movie_reviews.filter(
        date_added__year=selected_year
    ).annotate(
        month=TruncMonth('date_added')
    ).values('month').annotate(
        count=Count('id')
    ).order_by('month')
    
    # Create a complete 12-month array
    monthly_breakdown = []
    monthly_counts = {item['month'].month: item['count'] for item in monthly_data}
    
    for month_num in range(1, 13):
        month_name = calendar.month_abbr[month_num]
        count = monthly_counts.get(month_num, 0)
        monthly_breakdown.append({
            'month': month_name,
            'month_num': month_num,
            'count': count
        })
    
    context = {
        'page_title': 'Statistics',
        'movie_time_spent': format_time(movie_total_minutes),
        'tvshow_time_spent': format_time(tvshow_total_minutes),
        'movie_total_count': movie_reviews.count(),
        'tvshow_total_count': tvshow_reviews.count(),
        'tvshow_episodes_watched': watched_episodes.count(),
        'movie_last_7_days': format_hours_only(movie_last_7_days_minutes),
        'tvshow_last_7_days': format_hours_only(tvshow_last_7_days_minutes),
        'shortest_movie': shortest_movie,
        'longest_movie': longest_movie,
        'monthly_breakdown': monthly_breakdown,
        'available_years': available_years,
        'selected_year': selected_year,
    }
    return render(request, 'statistics_page.html', context)

@login_required
def settings_page(request):
    """
    View to display and update user settings.
    
    Handles:
    - Content display preferences (keywords, reviews, plot)
    - API key generation and management
    - Push notification preferences (types, quiet hours)
    """
    try:
        user_settings = request.user.settings
    except:
        user_settings = UserSettings.objects.create(user=request.user)
    
    # Get or create notification preferences
    from notifications.models import NotificationPreference
    notification_prefs, created = NotificationPreference.objects.get_or_create(
        user=request.user
    )
    
    if request.user.api_key is None:
        request.user.generate_api_key()  # Use the method on the user model
        request.user.save()  # Make sure to save the user
        
    if request.method == 'POST':
        # Check if admin is sending a broadcast notification
        if 'send_broadcast' in request.POST and request.user.is_staff:
            title = request.POST.get('broadcast_title', '').strip()
            message = request.POST.get('broadcast_message', '').strip()
            url = request.POST.get('broadcast_url', '').strip() or '/'
            
            if title and message:
                from notifications.utils import send_notification_to_all_users
                result = send_notification_to_all_users(
                    title=title,
                    body=message,
                    notification_type='system',
                    url=url
                )
                
                from django.contrib import messages
                success_count = result.get('success', 0)
                failed_count = result.get('failed', 0)
                queued_count = result.get('queued', 0)
                
                if success_count > 0 or queued_count > 0:
                    msg_parts = []
                    if success_count > 0:
                        msg_parts.append(f"✅ Sent to {success_count} user(s)")
                    if queued_count > 0:
                        msg_parts.append(f"⏰ Queued for {queued_count} user(s) in quiet hours")
                    if failed_count > 0:
                        msg_parts.append(f"❌ {failed_count} failed")
                    
                    messages.success(
                        request, 
                        " | ".join(msg_parts),
                        extra_tags='broadcast-success-message'
                    )
                else:
                    messages.warning(request, "⚠️ No users received the broadcast.")
                
                return redirect('settings_page')
        
        # Check if user wants to regenerate API key
        if 'regenerate_api_key' in request.POST:
            request.user.generate_api_key()  # Generate a new key
            request.user.save()  # Save the user with the new key
            from django.contrib import messages
            messages.success(request, "New API key generated successfully!")
            return redirect('settings_page')
        
        # Process content display settings
        user_settings.show_keywords = 'show_keywords' in request.POST
        user_settings.show_review_text = 'show_review_text' in request.POST
        user_settings.show_plot = 'show_plot' in request.POST
        user_settings.save()
        
        # Process notification preferences
        notification_prefs.new_releases = 'new_releases' in request.POST
        notification_prefs.watchlist_updates = 'watchlist_updates' in request.POST
        notification_prefs.recommendations = 'recommendations' in request.POST
        notification_prefs.new_reviews = 'new_reviews' in request.POST
        notification_prefs.movie_of_week = 'movie_of_week' in request.POST
        notification_prefs.system_notifications = 'system_notifications' in request.POST
        notification_prefs.quiet_hours_enabled = 'quiet_hours_enabled' in request.POST
        
        # Handle quiet hours times
        quiet_start = request.POST.get('quiet_hours_start')
        quiet_end = request.POST.get('quiet_hours_end')
        
        if quiet_start:
            from datetime import datetime
            try:
                notification_prefs.quiet_hours_start = datetime.strptime(quiet_start, '%H:%M').time()
            except ValueError:
                notification_prefs.quiet_hours_start = None
        else:
            notification_prefs.quiet_hours_start = None
            
        if quiet_end:
            from datetime import datetime
            try:
                notification_prefs.quiet_hours_end = datetime.strptime(quiet_end, '%H:%M').time()
            except ValueError:
                notification_prefs.quiet_hours_end = None
        else:
            notification_prefs.quiet_hours_end = None
        
        notification_prefs.save()
        
        # Add a success message if django messages is configured
        from django.contrib import messages
        messages.success(request, "Settings updated successfully!")
        return redirect('settings_page')
        
    return render(request, 'settings_page.html', {
        'user_settings': user_settings,
        'notification_prefs': notification_prefs,
    })


@login_required
def stremio_addon_page(request):
    """
    View to display Stremio addon installation page.
    Shows the addon URL with the user's API key encoded.
    """
    import base64
    import json
    
    # Generate API key if not exists
    if request.user.api_key is None:
        request.user.generate_api_key()
        request.user.save()
    
    # Build the install URL with encoded config
    if request.user.api_key:
        config = {'api_key': request.user.api_key}
        encoded_config = base64.urlsafe_b64encode(
            json.dumps(config).encode()
        ).decode().rstrip('=')
        
        # Build the full addon URL
        base_url = request.build_absolute_uri('/stremio/')
        install_url = f"stremio://{request.get_host()}/stremio/{encoded_config}/manifest.json"
    else:
        install_url = None
    
    return render(request, 'stremio_addon.html', {
        'install_url': install_url,
    })


@login_required
def release_calendar(request):
    """
    Display a calendar of upcoming movie and TV show releases
    """
    # Get the year and month from query params, default to current month
    today = date.today()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))
    
    # Create date objects for the first and last day of the month
    first_day = date(year, month, 1)
    # Get the last day of the month
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    
    # Calculate previous and next month for navigation
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    
    # Get all days in the month
    import calendar as cal_module
    cal = cal_module.monthcalendar(year, month)
    
    # Get movies releasing this month
    movies = Movie.objects.filter(
        release_date__range=(first_day, last_day)
    ).order_by('release_date')
    
    # Get episodes releasing this month
    from tvshows.models import Episode
    episodes = Episode.objects.filter(
        air_date__range=(first_day, last_day)
    ).select_related('season', 'season__show').order_by('air_date')
    
    # Create calendar weeks with day numbers
    calendar_weeks = []
    for week in cal:
        calendar_weeks.append(week)
    
    # Group the releases by date
    releases_by_date = {}
    
    # Add movies to releases
    for movie in movies:
        release_date = movie.release_date
        if release_date:
            date_str = release_date.strftime('%Y-%m-%d')
            if date_str not in releases_by_date:
                releases_by_date[date_str] = {'movies': [], 'tv_shows': [], 'episodes': []}
            releases_by_date[date_str]['movies'].append({
                'title': movie.title,
                'tmdb_id': movie.tmdb_id
            })
    
    # Group episodes by show for each date
    episode_groups = {}
    for episode in episodes:
        if not episode.air_date:
            continue
            
        date_str = episode.air_date.strftime('%Y-%m-%d')
        show_id = episode.season.show.id
        
        # Create key for grouping
        key = (date_str, show_id)
        if key not in episode_groups:
            episode_groups[key] = {
                'show': episode.season.show,
                'episodes': []
            }
        
        episode_groups[key]['episodes'].append(episode)
    
    # Add grouped episodes to releases_by_date
    for (date_str, _), group in episode_groups.items():
        show = group['show']
        episodes_count = len(group['episodes'])
        
        if date_str not in releases_by_date:
            releases_by_date[date_str] = {'movies': [], 'tv_shows': [], 'episodes': []}
        
        # If multiple episodes, create a batch release entry
        if episodes_count > 1:
            releases_by_date[date_str]['episodes'].append({
                'title': f"{show.title} ({episodes_count} episodes)",
                'show_tmdb_id': show.tmdb_id,
                'count': episodes_count
            })
        else:
            # Single episode release
            episode = group['episodes'][0]
            episode_title = f"{show.title} - S{episode.season.season_number}E{episode.episode_number}"
            releases_by_date[date_str]['episodes'].append({
                'title': episode_title,
                'show_tmdb_id': show.tmdb_id,
                'count': 1
            })
    
    import json
    releases_json = json.dumps(releases_by_date)
    
    context = {
        'year': year,
        'month': month,
       
        'month_name': cal_module.month_name[month],
        'calendar_weeks': calendar_weeks,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
        'releases_by_date': releases_by_date,
        'releases_json': releases_json,
        'today': today,
    }
    
    return render(request, 'release_calendar.html', context)

@login_required
def production_company_detail(request, company_id):
    """
    View for displaying details about a production company and its filmography
    """
    # Get the production company object or return 404
    company = get_object_or_404(ProductionCompany, id=company_id)
    
    # Get content types for Movie and TVShow models
    from django.contrib.contenttypes.models import ContentType
    from movies.models import Movie
    from tvshows.models import TVShow
    
    movie_content_type = ContentType.objects.get_for_model(Movie)
    tv_show_content_type = ContentType.objects.get_for_model(TVShow)
    
    # Find all movies and TV shows from this production company
    movies = Movie.objects.filter(production_companies=company)
    tv_shows = TVShow.objects.filter(production_companies=company)
    
    # Build a combined list of productions for the template
    productions = []
    
    # Add movies to the list
    for movie in movies:
        productions.append({
            'title': movie.title,
            'year': movie.release_date.year if movie.release_date else None,
            'type': 'movie',
            'type_display': 'Movie',
            'tmdb_id': movie.tmdb_id,
            'backdrop': movie.backdrop,
            'poster': movie.poster
        })
    
    # Add TV shows to the list
    for show in tv_shows:
        productions.append({
            'title': show.title,
            'year': show.first_air_date.year if show.first_air_date else None,
            'type': 'tvshow',
            'type_display': 'TV Show',
            'tmdb_id': show.tmdb_id,
            'backdrop': show.backdrop,
            'poster': show.poster
        })
    
    # Sort productions by year (most recent first)
    productions = sorted(productions, key=lambda x: x.get('year') or 0, reverse=True)
    
    # Calculate average rating for all productions
    average_rating = None
    user_average_rating = None
    
    # Collect all media identifiers
    media_identifiers = []
    for movie in movies:
        media_identifiers.append((movie_content_type.id, movie.id))
    for show in tv_shows:
        media_identifiers.append((tv_show_content_type.id, show.id))
    
    if media_identifiers:
        # Build query for reviews
        from django.db.models import Q
        q_objects = Q()
        for ct_id, obj_id in media_identifiers:
            q_objects |= Q(content_type_id=ct_id, object_id=obj_id)
        
        # Calculate average rating across all users
        from custom_auth.models import Review
        rating_data = Review.objects.filter(q_objects).aggregate(models.Avg('rating'))
        if rating_data['rating__avg'] is not None:
            average_rating = round(rating_data['rating__avg'], 1)
        
        # Calculate average rating for the current user
        user_rating_data = Review.objects.filter(q_objects, user=request.user).aggregate(models.Avg('rating'))
        if user_rating_data['rating__avg'] is not None:
            user_average_rating = round(user_rating_data['rating__avg'], 1)

    # Add rating data to productions
    for item in productions:
        if item['type'] == 'movie':
            movie = next((m for m in movies if m.tmdb_id == item['tmdb_id']), None)
            if movie:
                # Get the average rating for this movie
                from custom_auth.models import Review
                rating_data = Review.objects.filter(
                    content_type=movie_content_type, 
                    object_id=movie.id
                ).aggregate(models.Avg('rating'))
                if rating_data['rating__avg'] is not None:
                    item['rating'] = round(rating_data['rating__avg'], 1)
        elif item['type'] == 'tvshow':
            show = next((s for s in tv_shows if s.tmdb_id == item['tmdb_id']), None)
            if show:
                # Get the average rating for this show
                from custom_auth.models import Review
                rating_data = Review.objects.filter(
                    content_type=tv_show_content_type, 
                    object_id=show.id
                ).aggregate(models.Avg('rating'))
                if rating_data['rating__avg'] is not None:
                    item['rating'] = round(rating_data['rating__avg'], 1)
    
    # Choose a random backdrop for the page background
    random_backdrop = None
    backdrop_candidates = [p['backdrop'] for p in productions if p.get('backdrop')]
    if backdrop_candidates:
        import random
        random_backdrop = random.choice(backdrop_candidates)
    
    context = {
        'company': company,
        'productions': productions,
        'average_rating': average_rating,
        'user_average_rating': user_average_rating,
        'random_backdrop': random_backdrop,
    }
    
    return render(request, 'production_company_page.html', context)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def combined_recommendations(request):
    """Get combined recommendations across all media types"""
    limit = int(request.GET.get('limit', 20))
    recommender = CrossMediaRecommender()
    
    # Get discovery feed which mixes all content types
    feed_items = recommender.get_discovery_feed(
        request.user, 
        total_items=limit,
        media_types=['movies', 'tvshows', 'games']
    )
    
    # The feed items are already normalized by the service
    return Response(feed_items)

from .forms import MemeForm
from django.contrib import messages

@login_required
def add_meme(request, content_type_id, object_id):
    content_type = get_object_or_404(ContentType, id=content_type_id)
    model_class = content_type.model_class()
    obj = get_object_or_404(model_class, id=object_id)
    
    if request.method == 'POST':
        form = MemeForm(request.POST)
        if form.is_valid():
            meme = form.save(commit=False)
            meme.user = request.user
            meme.content_type = content_type
            meme.object_id = object_id
            meme.save()
            messages.success(request, "Meme added successfully!")
            
            # Redirect logic
            if content_type.model == 'movie':
                return redirect('movie_page', movie_id=obj.tmdb_id)
            elif content_type.model == 'tvshow':
                return redirect('tv_show_page', tv_show_id=obj.tmdb_id)
            # Add other models as needed
            
            return redirect('/')
    else:
        form = MemeForm()

    return render(request, 'custom_auth/add_meme.html', {'form': form, 'object': obj})
