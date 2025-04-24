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

# Add these imports if they're not already at the top
from django.db.models import Count, Avg, F, Q, Case, When, IntegerField
from django.db.models.functions import ExtractMonth, ExtractYear
from collections import defaultdict
import json
from datetime import timedelta, datetime
from django.contrib.contenttypes.models import ContentType
from movies.models import Movie, Genre
from custom_auth.models import Review

def login_page(request):
    if request.user.is_authenticated:
        return redirect('home_page')
    return render(request, 'login_page.html')

def login_request(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('home_page')
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
def browse_by_genre(request):
    anime_only = request.GET.get('anime_only') == 'on'
    genres = Genre.objects.all()
    return render(request, 'browse_by_genre.html', {
        'genres': genres,
        'anime_only': anime_only,
    })

@login_required
def genre_detail(request, genre_id):
    genre = get_object_or_404(Genre, pk=genre_id)
    anime_filter = request.GET.get('anime_filter', 'all')
    view_type = request.GET.get('view_type', 'grid')
    
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
    
    return render(request, 'genre_detail.html', {
        'genre': genre,
        'movies': movies,
        'tv_shows': tv_shows,
        'anime_filter': anime_filter,
        'view_type': view_type,
    })

@login_required
def profile_page(request, username=None):
    """
    View for displaying a user's profile page.
    If username is provided, show that user's profile.
    Otherwise, show the logged-in user's profile.
    """
    if username:
        user = get_object_or_404(User, username=username)
    else:
        user = request.user
    
    # Get user's favorite movies by finding movies they've reviewed
    # and sorting by rating (highest first)
    movie_content_type = ContentType.objects.get_for_model(Movie)
    movie_reviews = Review.objects.filter(
        user=user,
        content_type=movie_content_type
    ).select_related('content_type').order_by('-rating')
    
    # Create a list of movies with their review scores
    favorite_movies = []
    for review in movie_reviews[:11]:  # Limit to top 5 rated movies
        movie = Movie.objects.filter(id=review.object_id).first()
        if movie:
            movie.user_rating = review.rating  # Add the user's rating to the movie object
            favorite_movies.append(movie)
    
    # Get user's favorite TV shows
    favorite_shows = []  # Replace with: TVShow.objects.filter(user=user)
    
    # Get user's watchlist
    watchlist_items = []  # Replace with: WatchlistItem.objects.filter(user=user)
    
    context = {
        'user': user,
        'favorite_movies': favorite_movies,
        'favorite_shows': favorite_shows,
        'watchlist_items': watchlist_items,
    }
    
    return render(request, 'profile_page.html', context)

@login_required
def watchlist_page(request):
    """Display the user's watchlist with filtering options."""
    
    # Get watchlist items
    watchlist_items = request.user.get_watchlist()
    
    # Handle filters
    media_type = request.GET.get('media_type', '')
    genre_id = request.GET.get('genre')
    sort_by = request.GET.get('sort_by', '-date_added')  # Default sort by date added
    filter_title = request.GET.get('title', '')  # Get title filter parameter
    
    # Apply media type filter if provided
    if media_type == 'movie':
        watchlist_items = watchlist_items.filter(content_type=ContentType.objects.get_for_model(Movie))
    elif media_type == 'tvshow':
        watchlist_items = watchlist_items.filter(content_type=ContentType.objects.get_for_model(TVShow))
    # If no media_type filter, show both movies and TV shows
    
    # Apply filters if provided
    if genre_id:
        # Filter items by genre (need to handle GenericForeignKey relationship)
        filtered_items = []
        for item in watchlist_items:
            if hasattr(item.media, 'genres') and item.media.genres.filter(id=genre_id).exists():
                filtered_items.append(item.id)
        watchlist_items = watchlist_items.filter(id__in=filtered_items)
    
    # Filter by title if provided
    if filter_title:
        filtered_items = []
        for item in watchlist_items:
            if hasattr(item.media, 'title') and filter_title.lower() in item.media.title.lower():
                filtered_items.append(item.id)
        watchlist_items = watchlist_items.filter(id__in=filtered_items)
    
    # Apply sorting
    if sort_by == 'title':
        # Sort by title (need custom sorting for GenericForeignKey)
        watchlist_items = sorted(watchlist_items, key=lambda x: x.media.title)
    elif sort_by == '-title':
        watchlist_items = sorted(watchlist_items, key=lambda x: x.media.title, reverse=True)
    elif sort_by == 'release_date':
        from datetime import date
        watchlist_items = sorted(watchlist_items, key=lambda x: getattr(x.media, 'release_date', date(1900, 1, 1)))
    elif sort_by == '-release_date':
        from datetime import date
        watchlist_items = sorted(watchlist_items, key=lambda x: getattr(x.media, 'release_date', date(1900, 1, 1)), reverse=True)
    # Default sorting by date_added is handled by the model's Meta ordering
    
    # Get only the genres that are in the watchlist items
    genre_ids = set()
    for item in watchlist_items:
        if hasattr(item.media, 'genres'):
            genre_ids.update(item.media.genres.values_list('id', flat=True))
    genres = Genre.objects.filter(id__in=genre_ids).distinct()
    
    # Add other users' reviews for each watchlist item
    current_user = request.user
    for item in watchlist_items:
        # Get the content type and object id for the media
        content_type = item.content_type
        object_id = item.object_id
        
        # Get reviews for this media from other users
        other_reviews = Review.objects.filter(
            content_type=content_type,
            object_id=object_id
        ).exclude(user=current_user).select_related('user')
        
        # Create a list of reviews with username and rating
        item.other_reviews = [
            {'username': review.user.username, 'rating': review.rating}
            for review in other_reviews
        ]
        
        # Add average rating and count
        if other_reviews:
            item.avg_rating = round(sum(review.rating for review in other_reviews) / len(other_reviews), 1)
            item.rating_count = len(other_reviews)
    
    context = {
        'watchlist_items': watchlist_items,
        'genres': genres,
        'current_media_type': media_type,
        'current_genre': genre_id,
        'current_sort': sort_by,
        'filter_title': filter_title,  # Add title filter to context
        'view_type': request.GET.get('view_type', 'grid')  # Default to grid view
    }
    
    return render(request, 'watchlist_page.html', context)

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
def browse_by_country(request):
    """
    View for displaying all available countries for browsing
    """
    countries = Country.objects.all().order_by('name')
    return render(request, 'browse_by_country.html', {
        'countries': countries,
    })

@login_required
def country_detail(request, country_id):
    """
    View for displaying all movies and TV shows from a specific country
    with filtering options
    """
    country = get_object_or_404(Country, pk=country_id)
    anime_filter = request.GET.get('anime_filter', 'all')
    view_type = request.GET.get('view_type', 'grid')
    
    # Get content types for Movie and TVShow models
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
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
    
    # Get user's reviews for movies in this country
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
    
    
    return render(request, 'country_detail.html', {
        'country': country,
        'movies': movies,
        'tv_shows': tv_shows,
        'anime_filter': anime_filter,
        'view_type': view_type,
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
    View for displaying people categorized by their roles (directors, writers, actors, etc.)
    with optimized query performance
    """
    # Cache key prefix for storing sorted people lists
    cache_key_prefix = "people_browser_"
    cache_timeout = 20  # 1 hour cache timeout
    
    # Function to efficiently get and sort people by role with caching
    def get_people_by_role(role_filter, cache_key):
        from django.core.cache import cache
        
        # Limit to first 100 people per category to prevent slowdowns
        # with huge datasets - add pagination if needed
        people = Person.objects.filter(role_filter)[:100]
        
        # Prefetch all media relations in a single query 
        # to avoid N+1 database hits
        person_ids = [p.id for p in people]
        
        # Get all media relations for all people in one query
        media_relations = MediaPerson.objects.filter(
            person_id__in=person_ids
        ).values('person_id', 'content_type_id', 'object_id')
        
        # Group media relations by person
        person_media = {}
        for relation in media_relations:
            person_id = relation['person_id']
            if person_id not in person_media:
                person_media[person_id] = set()  # Use a set instead of a list
            person_media[person_id].add(
                (relation['content_type_id'], relation['object_id'])
            )
        
        # Batch collect all content types and object IDs for reviews
        all_media_identifiers = set()  # Use a set here too
        for relations in person_media.values():
            all_media_identifiers.update(relations)  # Use update for sets
        
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
                person.avg_rating = None
                person.rating_count = 0
                people_with_ratings.append(person)
                continue
                
            # Calculate average rating across all media
            total_rating = 0
            total_count = 0
            
            for media_key in person_media_list:
                if media_key in media_ratings:
                    rating_data = media_ratings[media_key]
                    # Weighted average: Rating * Count
                    total_rating += rating_data['avg_rating'] * rating_data['count']
                    total_count += rating_data['count']
            
            if total_count > 0:
                person.avg_rating = round(total_rating / total_count, 1)
                person.rating_count = total_count
            else:
                person.avg_rating = None
                person.rating_count = 0
                
            people_with_ratings.append(person)
            
        # Sort by average rating
        result = sorted(
            people_with_ratings,
            key=lambda p: (p.avg_rating is not None, p.avg_rating or 0),
            reverse=True
        )
        
        # Cache the result
        cache.set(cache_key, result, cache_timeout)
        
        return result
    
    # Get people by category with optimized queries
    directors = get_people_by_role(
        Q(is_director=True), 
        f"{cache_key_prefix}directors"
    )
    
    writers = get_people_by_role(
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
        Q(is_original_story=True) | Q(is_novelist=True) | 
        Q(is_comic_artist=True) | Q(is_graphic_novelist=True),
        f"{cache_key_prefix}other_creators"
    )
    
    return render(request, 'browse_by_people.html', {
        'directors': directors,
        'writers': writers,
        'actors': actors,
        'tv_creators': tv_creators,
        'musicians': musicians,
        'other_creators': other_creators,
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

def settings_page(request):
    """View to display and update user settings"""
    try:
        user_settings = request.user.settings
    except:
        user_settings = UserSettings.objects.create(user=request.user)
        
    if request.method == 'POST':
        # Process form data
        user_settings.show_keywords = 'show_keywords' in request.POST
        user_settings.show_review_text = 'show_review_text' in request.POST
        user_settings.show_plot = 'show_plot' in request.POST
        user_settings.save()
        
        # Add a success message if django messages is configured
        from django.contrib import messages
        messages.success(request, "Settings updated successfully!")
        return redirect('settings_page')
        
    return render(request, 'setttings_page.html', {
        'user_settings': user_settings
    })
