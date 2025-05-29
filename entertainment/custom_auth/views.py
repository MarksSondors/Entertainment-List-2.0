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
from datetime import date # Import date
import random

from django.views.decorators.http import require_POST

# Add these imports if they're not already at the top
from django.db.models import Count, Avg, F, Q, Case, When, IntegerField
from django.db.models.functions import ExtractMonth, ExtractYear
from collections import defaultdict
import json
from datetime import timedelta, datetime
from django.contrib.contenttypes.models import ContentType
from movies.models import Movie, Genre
from tvshows.models import TVShow
from custom_auth.models import Review, Person

from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank, TrigramSimilarity


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
    
    # Get the current community movie pick data
    community_pick_data = get_current_community_pick_data()
    
    context = {
        'community_pick': community_pick_data
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
            ).filter(similarity__gt=0.3).order_by('-similarity')[:10]
        
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
            ).filter(similarity__gt=0.3).order_by('-similarity')[:10]
        
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
        user = get_object_or_404(CustomUser, username=username)
    else:
        user = request.user
    
    # Get all users for the sidebar (limit to a reasonable number)
    all_users = CustomUser.objects.all().order_by('username')[:50]
    
    # Get user's favorite movies by finding movies they've reviewed
    # and sorting by rating (highest first)
    movie_content_type = ContentType.objects.get_for_model(Movie)
    movie_reviews = Review.objects.filter(
        user=user,
        content_type=movie_content_type
    ).select_related('content_type').order_by('-rating')
    
    # Create a list of movies with their review scores
    favorite_movies = []
    movie_ids = [review.object_id for review in movie_reviews[:16]]
    movies_dict = {movie.id: movie for movie in Movie.objects.filter(id__in=movie_ids)}
    
    for review in movie_reviews[:16]:  # Limit to top 11 rated movies
        movie = movies_dict.get(review.object_id)
        if movie:
            movie.user_rating = review.rating  # Add the user's rating to the movie object
            favorite_movies.append(movie)
    
    # Get user's favorite TV shows
    tv_show_content_type = ContentType.objects.get_for_model(TVShow)
    tv_show_reviews = Review.objects.filter(
        user=user,
        content_type=tv_show_content_type
    ).select_related('content_type', 'season', 'episode_subgroup').order_by('-rating')
    
    # Prefetch all TV shows in a single query
    tv_show_ids = set(review.object_id for review in tv_show_reviews)
    tv_shows_dict = {tv.id: tv for tv in TVShow.objects.filter(id__in=tv_show_ids)}
    
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
                           reverse=True)[:16]:
        show = show_data['tv_show']
        show.user_rating = round(show_data['total_rating'] / show_data['count'], 1) if show_data['count'] > 0 else 0
        show.review_count = show_data['count']  # Add the count of reviews for this show
        favorite_shows.append(show)
    
    # Get user's watchlist
    watchlist_items = user.get_watchlist()[:16]  # Limit to top 10 items
    
    # Prepare watchlist items for template display
    watchlist_for_template = []
    if watchlist_items:
        # Add reviews data to items
        add_review_data_to_items(watchlist_items, user)
        
        # Process items for template
        for item in watchlist_items:
            media = item.media  # This will use the GenericForeignKey
            if media:
                watchlist_for_template.append({
                    'id': item.id,
                    'title': media.title,
                    'poster_url': media.poster,
                    'media_type': item.content_type.model,
                    'avg_rating': getattr(item, 'avg_rating', None),
                    'rating_count': getattr(item, 'rating_count', None),
                    'tmdb_id': media.tmdb_id,  # Add tmdb_id for URL construction
                    'object_id': media.id      # Add the actual media object ID
                })
    
    context = {
        'user': user,
        'favorite_movies': favorite_movies,
        'favorite_shows': favorite_shows,
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
    
    # Get all watchlist items for the user (for initial page load)
    watchlist_items = user.get_watchlist()
    
    if not watchlist_items:
        # Handle empty watchlist case
        return render(request, 'watchlist_page.html', {'watchlist_empty': True})
    
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
    
    # First, separate TV shows and movies
    tv_shows_items = []
    
    for item in watchlist_items:
        media = get_safe_media(item)
        if item.content_type_id == movie_ct.id:
            movies.append(item)
        elif item.content_type_id == tv_ct.id and media:
            tv_shows_items.append(item)
    
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
    
    # Update your context dictionary to include finished_shows
    context = {
        'continue_watching': continue_watching_for_template,
        'havent_started': havent_started_for_template,
        'finished_shows': finished_shows_for_template,  # Add this line
        'movies': movies_for_template,
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
    
    # Prefetch content types in one query
    content_type_ids = {item.content_type_id for item in items}
    content_types = {ct.id: ct for ct in ContentType.objects.filter(id__in=content_type_ids)}
    
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
    
    # Fetch all direct reviews in a SINGLE query
    all_reviews = Review.objects.filter(review_query).exclude(user=current_user).select_related('user')
    
    # For TV shows, also fetch season and episode subgroup reviews
    if tv_show_ids:
        # Get season and subgroup reviews for these TV shows
        from django.db.models import Q
        tv_show_season_reviews = Review.objects.filter(
            content_type_id=tv_show_content_type_id,
            object_id__in=tv_show_ids
        ).exclude(
            user=current_user
        ).exclude(
            season=None,
            episode_subgroup=None
        ).select_related('user', 'season', 'episode_subgroup')
        
        # Add these reviews to our collection
        all_reviews = list(all_reviews) + list(tv_show_season_reviews)
    
    # Group reviews by content type and object ID for fast lookup
    grouped_reviews = {}
    for review in all_reviews:
        key = (review.content_type_id, review.object_id)
        if key not in grouped_reviews:
            grouped_reviews[key] = []
        grouped_reviews[key].append(review)
    
    # Process each item group
    for (ct_id, obj_id), item_group in grouped_items.items():
        # Get reviews for this specific media item from our cache
        other_reviews = grouped_reviews.get((ct_id, obj_id), [])
        
        # Create review data
        review_data = [
            {'username': review.user.username, 'rating': review.rating}
            for review in other_reviews
        ]
        
        # Calculate statistics
        avg_rating = None
        rating_count = len(other_reviews)
        if other_reviews:
            avg_rating = round(sum(review.rating for review in other_reviews) / rating_count, 1)
        
        # Apply to all related items
        for item in item_group:
            item.other_reviews = review_data
            item.avg_rating = avg_rating
            item.rating_count = rating_count

@login_required
def api_watchlist(request):
    """API endpoint for filtering watchlist items."""
    
    user = request.user
    search_query = request.GET.get('search', '')
    genre_id = request.GET.get('genre', '')
    country_id = request.GET.get('country', '')
    sort_by = request.GET.get('sort_by', 'date_added')
    
    # Get user's watchlist items
    watchlist_items = user.get_watchlist()
    
    # Apply search filter if provided
    if search_query:
        # Use PostgreSQL's full-text search capabilities
        from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
        
        # Get content types for efficient filtering
        movie_ct = ContentType.objects.get_for_model(Movie)
        tv_ct = ContentType.objects.get_for_model(TVShow)
        
        # Group watchlist items by content type
        movie_items = [item for item in watchlist_items if item.content_type_id == movie_ct.id]
        tv_items = [item for item in watchlist_items if item.content_type_id == tv_ct.id]
        other_items = [item for item in watchlist_items if item.content_type_id not in (movie_ct.id, tv_ct.id)]
        
        # Extract IDs by content type
        movie_ids = [item.object_id for item in movie_items]
        tv_ids = [item.object_id for item in tv_items]
        
        # Create search query object with proper config
        search_query_obj = SearchQuery(search_query, config='english')
        
        # Search movies with PostgreSQL full-text search
        movie_matches = set(Movie.objects.filter(id__in=movie_ids).annotate(
            search=SearchVector('title', 'original_title', config='english'),
            rank=SearchRank(SearchVector('title', 'original_title', config='english'), search_query_obj)
        ).filter(search=search_query_obj).order_by('-rank').values_list('id', flat=True))
        
        # Search TV shows with PostgreSQL full-text search
        tv_matches = set(TVShow.objects.filter(id__in=tv_ids).annotate(
            search=SearchVector('title', 'original_title', config='english'),
            rank=SearchRank(SearchVector('title', 'original_title', config='english'), search_query_obj)
        ).filter(search=search_query_obj).order_by('-rank').values_list('id', flat=True))
        
        # Add fallback search for partial text matches if full-text search found nothing
        if not movie_matches:
            movie_matches = set(Movie.objects.filter(
                id__in=movie_ids
            ).filter(
                Q(title__icontains=search_query) | 
                Q(original_title__icontains=search_query)
            ).values_list('id', flat=True))
        
        if not tv_matches:
            tv_matches = set(TVShow.objects.filter(
                id__in=tv_ids
            ).filter(
                Q(title__icontains=search_query) | 
                Q(original_title__icontains=search_query)
            ).values_list('id', flat=True))
        
        # Filter watchlist items based on database matches
        filtered_items = []
        for item in movie_items:
            if item.object_id in movie_matches:
                filtered_items.append(item)
        
        for item in tv_items:
            if item.object_id in tv_matches:
                filtered_items.append(item)
        
        # Add other content types (if any)
        filtered_items.extend(other_items)
        
        watchlist_items = filtered_items
    
    # Apply genre filter if provided
    if genre_id:
        filtered_items = []
        for item in watchlist_items:
            if hasattr(item.media, 'genres') and item.media.genres.filter(id=genre_id).exists():
                filtered_items.append(item)
        watchlist_items = filtered_items
    
    # Apply country filter if provided
    if country_id:
        filtered_items = []
        for item in watchlist_items:
            if hasattr(item.media, 'countries') and item.media.countries.filter(id=country_id).exists():
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
            watchlist_items = sorted(watchlist_items, key=lambda x: x.media.title.lower())
        elif sort_by == '-title':
            watchlist_items = sorted(watchlist_items, key=lambda x: x.media.title.lower(), reverse=True)
        elif sort_by == 'release_date':
            # Handle both movie release_date and TV show first_air_date
            def get_release_date(item):
                if hasattr(item.media, 'release_date') and item.media.release_date:
                    return item.media.release_date
                elif hasattr(item.media, 'first_air_date') and item.media.first_air_date:
                    return item.media.first_air_date
                # Return a far past date for items without release dates
                return datetime.min
            watchlist_items = sorted(watchlist_items, key=get_release_date)
        elif sort_by == '-release_date':
            # Same function but reversed sort
            def get_release_date(item):
                if hasattr(item.media, 'release_date') and item.media.release_date:
                    return item.media.release_date
                elif hasattr(item.media, 'first_air_date') and item.media.first_air_date:
                    return item.media.first_air_date
                # Return a far past date for items without release dates
                return datetime.min
            watchlist_items = sorted(watchlist_items, key=get_release_date, reverse=True)
    
    # Categorize items
    continue_watching = []
    havent_started = []
    finished_shows = []
    movies = []
    
    movie_ct = ContentType.objects.get_for_model(Movie)
    tv_ct = ContentType.objects.get_for_model(TVShow)
    
    # First, separate TV shows and movies
    tv_shows_items = []
    
    for item in watchlist_items:
        if item.content_type_id == movie_ct.id:
            movies.append(serialize_watchlist_item(item))
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
            show_id = item.object_id
            total = total_episodes_map.get(show_id, 0)
            watched = watched_episodes_map.get(show_id, 0)
            
            # Calculate progress percentage
            progress = (watched / total * 100) if total > 0 else 0
            
            # Categorize based on progress
            if progress >= 100:
                # Completed shows
                finished_shows.append(serialize_watchlist_item(item))
            elif progress > 0:
                # In-progress shows
                continue_watching.append({'item': serialize_watchlist_item(item), 'progress': progress})
            else:
                # Not started shows
                havent_started.append(serialize_watchlist_item(item))
    
    return JsonResponse({
        'continue_watching': continue_watching,
        'havent_started': havent_started,
        'finished_shows': finished_shows,
        'movies': movies,
    })

def serialize_watchlist_item(item):
    """Helper function to serialize a watchlist item for JSON response."""
    result = {
        'id': item.id,
        'content_type_model': item.content_type.model,  # Flattened
        'media_id': item.media.id,                      # Flattened
        'media_title': item.media.title,                # Flattened
        'media_poster': item.media.poster,              # Flattened
        'media_tmdb_id': item.media.tmdb_id,            # Flattened
        'date_added': item.date_added.isoformat(),
    }
    
    # Add release date if available
    if hasattr(item.media, 'release_date') and item.media.release_date:
        result['media_release_date'] = item.media.release_date.isoformat()  # Flattened
    elif hasattr(item.media, 'first_air_date') and item.media.first_air_date:
        result['media_first_air_date'] = item.media.first_air_date.isoformat()  # Flattened
    
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
    Returns recently added reviews, watchlist items, episodes watched, and movies added to the database,
    combines them in the order of most recent first. Supports pagination for "Show More" functionality.
    Groups activities that happened at the same minute.
    """
    # Get pagination parameters
    page = int(request.GET.get('page', '1'))
    limit = int(request.GET.get('limit', '15'))
    
    # Calculate offset for pagination
    offset = (page - 1) * limit
    fetch_limit = limit * 3  # Fetch more items to ensure we have enough after grouping
    
    # Fetch recent data with increased limits for pagination
    reviews = Review.objects.select_related('user', 'season', 'episode_subgroup').order_by('-date_added')[:fetch_limit]
    watchlist_items = Watchlist.objects.select_related('user').order_by('-date_added')[:fetch_limit]
    movies = Movie.objects.select_related('added_by').order_by('-date_added')[:fetch_limit]
    
    # Prefetch media objects for reviews to avoid N+1 queries
    from django.contrib.contenttypes.models import ContentType
    movie_content_type = ContentType.objects.get_for_model(Movie)
    tvshow_content_type = ContentType.objects.get_for_model(TVShow)
    
    # Get lists of reviews by content type
    movie_reviews = [r for r in reviews if r.content_type_id == movie_content_type.id]
    tvshow_reviews = [r for r in reviews if r.content_type_id == tvshow_content_type.id]
    
    # Get all IDs to fetch in bulk
    movie_ids = [r.object_id for r in movie_reviews]
    tvshow_ids = [r.object_id for r in tvshow_reviews]
    
    # Also handle watchlist items - group by content type
    watchlist_movie_items = [w for w in watchlist_items if w.content_type_id == movie_content_type.id]
    watchlist_tvshow_items = [w for w in watchlist_items if w.content_type_id == tvshow_content_type.id]
    
    # Add watchlist item IDs to the fetch lists
    movie_ids.extend([w.object_id for w in watchlist_movie_items])
    tvshow_ids.extend([w.object_id for w in watchlist_tvshow_items])
    
    # Fetch all media objects in bulk
    movies_by_id = {m.id: m for m in Movie.objects.filter(id__in=movie_ids)}
    tvshows_by_id = {t.id: t for t in TVShow.objects.filter(id__in=tvshow_ids)}
    
    # Fetch recently watched episodes
    from collections import defaultdict
    from tvshows.models import WatchedEpisode  # Adjust import path if needed
    
    # Get recently watched episodes with proper select_related to follow the relationship chain
    watched_episodes = WatchedEpisode.objects.select_related('episode', 'episode__season', 'episode__season__show', 'user').order_by('-watched_date')[:fetch_limit * 6]
    # Group episodes by day, TV show, and user
    episode_groups = defaultdict(list)
    for episode in watched_episodes:
        # Use date as the key for grouping (without time)
        day_key = episode.watched_date.strftime('%Y-%m-%d')
        # Access tv_show through the proper relationship chain
        tv_show = episode.episode.season.show
        show_key = tv_show.id
        user_key = episode.user.id  # Add user to the grouping key
        group_key = (day_key, show_key, user_key)
        episode_groups[group_key].append(episode)
    
    # Format individual activities
    activities = []
    
    # Format episode watching activities
    for (day_key, show_id, user_id), episodes in episode_groups.items():
        # Sort episodes by watched_date to ensure we get the most recent timestamp
        episodes.sort(key=lambda e: e.watched_date, reverse=True)
        most_recent = episodes[0]
        # Access tv_show through the proper relationship chain
        tv_show = most_recent.episode.season.show
        
        # Use the timestamp of the most recent episode
        local_timestamp = timezone.localtime(most_recent.watched_date)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        
        # Create episode count text
        episode_count = len(episodes)
        
        activities.append({
            'type': 'watched_episodes',
            'username': most_recent.user.username,
            'title': tv_show.title if tv_show.title == tv_show.original_title else f"{tv_show.title} ({tv_show.original_title})",
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
    
    # Format reviews
    for review in reviews:
        # Use prefetched objects instead of accessing .media property
        if review.content_type_id == movie_content_type.id:
            content_object = movies_by_id.get(review.object_id)
        elif review.content_type_id == tvshow_content_type.id:
            content_object = tvshows_by_id.get(review.object_id)
        else:
            content_object = review.media  # Fallback for other content types
        
        poster_path = None
        tmdb_id = None
        
        # Rest of the processing remains the same
        if isinstance(content_object, Movie):
            content_type = "Movie"
            if content_object.title == content_object.original_title:
                title = content_object.title
            else:
                title = f"{content_object.title} ({content_object.original_title})"
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
        elif isinstance(content_object, TVShow):
            content_type = "TV Show"
            if content_object.title == content_object.original_title:
                title = content_object.title
            else:
                title = f"{content_object.title} ({content_object.original_title})"
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
            if review.season:
                title += f" - {review.season.title}"
            elif review.episode_subgroup:
                title += f" - {review.episode_subgroup.name}"
        else:
            content_type = review.content_type.model.capitalize()
            title = getattr(content_object, 'title', 'Unknown')
        
        # Use the minute as the key for grouping
        local_timestamp = timezone.localtime(review.date_added)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        
        activities.append({
            'type': 'review',
            'username': review.user.username,
            'content': review.review_text[:100] + '...' if review.review_text and len(review.review_text) > 100 else review.review_text,
            'title': title,
            'rating': review.rating,
            'content_type': content_type,
            'date': review.date_added,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': content_object.id,
            'poster_path': poster_path,
            'tmdb_id': tmdb_id
        })
    
    # Format watchlist items
    for item in watchlist_items:
        if item.content_type_id == movie_content_type.id:
            content_object = movies_by_id.get(item.object_id)
        elif item.content_type_id == tvshow_content_type.id:
            content_object = tvshows_by_id.get(item.object_id)
        else:
            content_object = item.media
        poster_path = None
        tmdb_id = None
        
        if isinstance(content_object, Movie):
            content_type = "Movie"
            if content_object.title == content_object.original_title:
                title = content_object.title
            else:
                title = f"{content_object.title} ({content_object.original_title})"
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
        elif isinstance(content_object, TVShow):
            content_type = "TV Show"
            if content_object.title == content_object.original_title:
                title = content_object.title
            else:
                title = f"{content_object.title} ({content_object.original_title})"
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
        else:
            content_type = item.content_type.model.capitalize()
            title = getattr(content_object, 'title', 'Unknown')
        
        # Convert UTC time to local time
        local_timestamp = timezone.localtime(item.date_added)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
            
        activities.append({
            'type': 'watchlist',
            'username': item.user.username,
            'title': title,
            'content_type': content_type,
            'date': item.date_added,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': content_object.id,
            'action': 'added to watchlist',
            'poster_path': poster_path,
            'tmdb_id': tmdb_id
        })
    
    # Format new movies
    for movie in movies:
        # Convert UTC time to local time
        local_timestamp = timezone.localtime(movie.date_added)
        timestamp_key = local_timestamp.strftime('%Y-%m-%d %H:%M')
        
        activities.append({
            'type': 'new_content',
            'username': movie.added_by.username if movie.added_by else 'System',
            'title': movie.title if movie.title == movie.original_title else f"{movie.title} ({movie.original_title})",
            'content_type': 'Movie',
            'date': movie.date_added,
            'timestamp': timestamp_key,
            'timestamp_key': timestamp_key,
            'media_id': movie.id,
            'action': 'added to database',
            'poster_path': movie.poster,
            'tmdb_id': movie.tmdb_id
        })
    
    # Sort all activities by date (most recent first)
    activities.sort(key=lambda x: x['date'], reverse=True)
    
    # Group activities by timestamp and media_id
    grouped_activities = {}
    for activity in activities:
        key = (activity['timestamp_key'], activity['title'], activity['media_id'])
        if key not in grouped_activities:
            grouped_activities[key] = {
                'title': activity['title'],
                'content_type': activity['content_type'],
                'timestamp': activity['timestamp'],
                'username': activity['username'],
                'poster_path': activity['poster_path'],
                'tmdb_id': activity['tmdb_id'],
                'actions': []
            }
            if 'content' in activity:
                grouped_activities[key]['content'] = activity['content']
            if 'rating' in activity:
                grouped_activities[key]['rating'] = activity['rating']
            if 'episode_count' in activity:
                grouped_activities[key]['episode_count'] = activity['episode_count']
        
        # Add action based on type
        if activity['type'] == 'review':
            grouped_activities[key]['actions'].append('reviewed')
        elif activity['type'] == 'watchlist':
            grouped_activities[key]['actions'].append('added to watchlist')
        elif activity['type'] == 'new_content':
            grouped_activities[key]['actions'].append('added to database')
        elif activity['type'] == 'watched_episodes':
            episode_count = activity['episode_count']
            grouped_activities[key]['actions'].append(f'watched {episode_count} episode{"s" if episode_count > 1 else ""}')
    
    # Convert grouped activities to list and format actions
    result_list = []
    for _, activity in grouped_activities.items():
        # Format the actions into a readable string
        if len(activity['actions']) == 1:
            activity['action'] = activity['actions'][0]
        else:
            activity['action'] = ' and '.join([', '.join(activity['actions'][:-1]), activity['actions'][-1]])
        
        # Remove actions list from final output
        del activity['actions']
        result_list.append(activity)
    
    # Sort by timestamp again to ensure newest first
    result_list.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Get total count for pagination info
    total_count = len(result_list)
    
    # Apply pagination
    paginated_results = result_list[offset:offset + limit]
    
    # Create response with pagination metadata
    response = {
        'results': paginated_results,
        'pagination': {
            'page': page,
            'limit': limit,
            'total': total_count,
            'has_more': (offset + limit) < total_count
        }
    }
    
    # Return as JSON
    return JsonResponse(response)

@login_required
def browse_by_people(request):
    """
    View for displaying people categorized by their roles with improved role identification
    and optimized query performance. Only shows people involved in works that have reviews.
    Includes additional data for enhanced display features.
    """
    # Cache key prefix for storing sorted people lists
    cache_key_prefix = "people_browser_reviewed_"
    cache_timeout = 3600  # 1 hour cache timeout
    
    # Function to efficiently get and sort people by role with caching
    def get_people_by_role(role_filter, cache_key):
        from django.core.cache import cache
        
        # Try to get cached result first
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result
        
        # First, identify media that has reviews
        reviewed_media = Review.objects.values('content_type_id', 'object_id').distinct()
        
        # Then get all MediaPerson entries that match those reviewed media
        reviewed_media_persons = MediaPerson.objects.filter(
            content_type_id__in=[item['content_type_id'] for item in reviewed_media],
            object_id__in=[item['object_id'] for item in reviewed_media]
        ).values('person_id').distinct()
        
        # Get people matching the role filter AND who have been in reviewed works
        # Include birth_date for the enhanced hover information
        people = Person.objects.filter(
            role_filter,
            profile_picture__isnull=False,
            id__in=reviewed_media_persons
        ).distinct()[:150]
        
        # If no people match the criteria, return empty list early
        if not people:
            cache.set(cache_key, [], cache_timeout)
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
        
        # Assign ratings to people
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
            
            if total_count > 0:
                person.avg_rating = round(total_rating / total_count, 1)
                person.rating_count = total_count
                person.num_works = num_works  # Add number of works for hover display
                # Convert date_of_birth to birth_date for template consistency
                person.birth_date = person.date_of_birth
                people_with_ratings.append(person)
        
        # Sort by average rating (highest first) and then alphabetically by name
        result = sorted(
            people_with_ratings,
            key=lambda p: (p.avg_rating is not None, p.avg_rating or 0, p.name),
            reverse=True
        )
        
        # Cache the result
        cache.set(cache_key, result, cache_timeout)
        
        return result
    
    # Get people by category with improved role identification
    directors = get_people_by_role(
        Q(is_director=True), 
        f"{cache_key_prefix}directors"
    )
    
    writers = get_people_by_role(
        # Include all writing roles
        Q(is_screenwriter=True) | Q(is_writer=True) | Q(is_story=True),
        f"{cache_key_prefix}writers"
    )
    
    actors = get_people_by_role(
        Q(is_actor=True),
        f"{cache_key_prefix}actors"
    )
    
    tv_creators = get_people_by_role(
        Q(is_tv_creator=True),
        f"{cache_key_prefix}tv_creators"
    )
    
    musicians = get_people_by_role(
        Q(is_musician=True) | Q(is_original_music_composer=True),
        f"{cache_key_prefix}musicians"
    )
    
    other_creators = get_people_by_role(
        # Visual artists and other creators not covered in other categories
        Q(is_comic_artist=True) | Q(is_graphic_novelist=True) | Q(is_book=True) | 
        Q(is_novelist=True) | Q(is_original_story=True),
        f"{cache_key_prefix}other_creators"
    )
      # Get category counts for the status bar
    category_counts = {
        'directors': len(directors),
        'writers': len(writers),
        'actors': len(actors),
        'tv_creators': len(tv_creators),
        'musicians': len(musicians),
        'other_creators': len(other_creators),
    }
    
    # Total person count
    total_people = sum(category_counts.values())
    
    return render(request, 'browse_by_people.html', {
        'directors': directors,
        'writers': writers,
        'actors': actors,
        'tv_creators': tv_creators,
        'musicians': musicians,
        'other_creators': other_creators,
        'category_counts': category_counts,
        'total_people': total_people,
    })

# Add this view function
@login_required
def movie_statistics(request):
    """
    Display personalized movie statistics for the current user.
    """
    # Get content type for Movie model
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Get filter parameters
    selected_year = request.GET.get('year', '')
    selected_genre = request.GET.get('genre', '')
    
    # Base query for user's movie reviews
    reviews = Review.objects.filter(
        user=request.user,
        content_type=movie_content_type
    ).select_related('user')
    
    # Apply filters
    if selected_year:
        reviews = reviews.filter(date_added__year=selected_year)
    
    if selected_genre:
        # This is more complex as we need to filter on the GenericForeignKey
        genre = get_object_or_404(Genre, id=selected_genre)
        movie_ids_with_genre = Movie.objects.filter(genres=genre).values_list('id', flat=True)
        reviews = reviews.filter(object_id__in=movie_ids_with_genre)
    
    # Get available years for the filter dropdown
    available_years = reviews.dates('date_added', 'year')
    available_years = [date.year for date in available_years]
    
    # Calculate totals
    total_movies = reviews.count()
    avg_rating = reviews.aggregate(avg=Avg('rating'))['avg'] or 0
    
    # Get movies for these reviews
    movie_ids = reviews.values_list('object_id', flat=True)
    movies = Movie.objects.filter(id__in=movie_ids)
    
    # Calculate total hours watched (based on movie runtime)
    total_minutes = sum(movie.runtime or 0 for movie in movies)
    total_hours = round(total_minutes / 60, 1)
    
    # Monthly activity data
    monthly_data = [0] * 12
    for review in reviews:
        month = review.date_added.month
        monthly_data[month-1] += 1
    
    # Genre distribution
    genre_counts = defaultdict(int)
    genre_labels = []
    genre_data = []
    
    for movie in movies:
        for genre in movie.genres.all():
            genre_counts[genre.name] += 1
    
    # Sort genres by count and get top 10
    top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    for genre_name, count in top_genres:
        genre_labels.append(genre_name)
        genre_data.append(count)
    
    # Rating distribution
    rating_distribution = [0] * 10
    for review in reviews:
        if 1 <= review.rating <= 10:
            # Convert float rating to integer index
            index = int(review.rating) - 1
            rating_distribution[index] += 1
    
    # Yearly comparison
    yearly_data = []
    yearly_labels = []
    
    year_counts = defaultdict(int)
    for review in Review.objects.filter(
        user=request.user,
        content_type=movie_content_type
    ):
        year = review.date_added.year
        year_counts[year] += 1
    
    # Sort years chronologically
    years_sorted = sorted(year_counts.keys())
    for year in years_sorted:
        yearly_labels.append(str(year))
        yearly_data.append(year_counts[year])
    
    # Top rated movies
    top_movies = []
    for review in reviews.order_by('-rating', '-date_added')[:10]:
        try:
            movie = Movie.objects.get(id=review.object_id)
            top_movies.append({
                'movie': movie,
                'rating': review.rating,
                'date_added': review.date_added
            })
        except Movie.DoesNotExist:
            continue
    
    # Determine top director
    director_counts = defaultdict(list)
    for movie in movies:
        # Get director(s) from the crew
        for person, roles in movie.get_crew().items():  # Add parentheses to call the method
            if 'Director' in roles:
                director_counts[person].append(movie)
    
    top_director = {"name": "None", "count": 0, "movies": []}
    if director_counts:
        top_director_name = max(director_counts.items(), key=lambda x: len(x[1]))[0]
        top_director = {
            "name": top_director_name,
            "count": len(director_counts[top_director_name]),
            "movies": director_counts[top_director_name][:5]  # Limit to 5 movies
        }
    
    # Calculate best streak
    if reviews:
        # Sort reviews by date
        sorted_reviews = sorted(reviews, key=lambda x: x.date_added)
        dates = [review.date_added.date() for review in sorted_reviews]
        
        current_streak = 1
        best_streak = 1
        for i in range(1, len(dates)):
            if (dates[i] - dates[i-1]) == timedelta(days=1):
                current_streak += 1
                best_streak = max(best_streak, current_streak)
            else:
                current_streak = 1
    else:
        best_streak = 0
    
    # Get all genres for the filter dropdown
    genres = Genre.objects.all().order_by('name')
    
    # Find the shortest and longest movies
    shortest_movie = None
    longest_movie = None
    if movies:
        valid_movies = [m for m in movies if m.runtime is not None]
        if valid_movies:
            shortest_movie = min(valid_movies, key=lambda x: x.runtime)
            longest_movie = max(valid_movies, key=lambda x: x.runtime)
    
    context = {
        'total_movies': total_movies,
        'avg_rating': avg_rating,
        'total_hours': total_hours,
        'monthly_data': json.dumps(monthly_data),
        'genre_labels': json.dumps(genre_labels),
        'genre_data': json.dumps(genre_data),
        'rating_distribution': json.dumps(rating_distribution),
        'yearly_labels': json.dumps(yearly_labels),
        'yearly_data': json.dumps(yearly_data),
        'top_movies': top_movies,
        'top_director': top_director,
        'best_streak': best_streak,
        'genres': genres,
        'available_years': available_years,
        'selected_year': int(selected_year) if selected_year else None,
        'selected_genre': int(selected_genre) if selected_genre else None,
        'shortest_movie': shortest_movie,
        'longest_movie': longest_movie,
    }
    
    return render(request, 'statistics_movies.html', context)

@login_required
def settings_page(request):
    """View to display and update user settings"""
    try:
        user_settings = request.user.settings
    except:
        user_settings = UserSettings.objects.create(user=request.user)
    
    if request.user.api_key is None:
        request.user.generate_api_key()  # Use the method on the user model
        request.user.save()  # Make sure to save the user
        
    if request.method == 'POST':
        # Check if user wants to regenerate API key
        if 'regenerate_api_key' in request.POST:
            request.user.generate_api_key()  # Generate a new key
            request.user.save()  # Save the user with the new key
            from django.contrib import messages
            messages.success(request, "New API key generated successfully!")
            return redirect('settings_page')
        
        # Process other settings form data
        user_settings.show_keywords = 'show_keywords' in request.POST
        user_settings.show_review_text = 'show_review_text' in request.POST
        user_settings.show_plot = 'show_plot' in request.POST
        user_settings.save()
        
        # Add a success message if django messages is configured
        from django.contrib import messages
        messages.success(request, "Settings updated successfully!")
        return redirect('settings_page')
        
    return render(request, 'settings_page.html', {
        'user_settings': user_settings
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
            'backdrop': movie.backdrop
        })
    
    # Add TV shows to the list
    for show in tv_shows:
        productions.append({
            'title': show.title,
            'year': show.first_air_date.year if show.first_air_date else None,
            'type': 'tvshow',
            'type_display': 'TV Show',
            'tmdb_id': show.tmdb_id,
            'backdrop': show.backdrop
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