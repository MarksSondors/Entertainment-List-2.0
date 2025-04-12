from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout  # Import the logout function
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.db.models import Q, Avg
from django.contrib.contenttypes.models import ContentType
# Create your views here.
# import services

from .models import *
from movies.models import Movie
from tvshows.models import TVShow
from datetime import date # Import date

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
def person_detail(request, person_id):
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
    
    context = {
        'person': person,
        'combined_credits': combined_credits_list,
        'average_rating': average_rating, # Add average rating to context
        'user_average_rating': user_average_rating, # Add user-specific average rating
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
            title = content_object.name
            if review.season:
                title += f" - {review.season}"
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
    Returns recently added reviews, watchlist items and movies added to the database, combines them in the order of most recent first.
    Groups activities that happened at the same minute.
    """
    # Fetch recent data
    reviews = Review.objects.all().order_by('-date_added')[:15]
    watchlist_items = Watchlist.objects.all().order_by('-date_added')[:15]
    movies = Movie.objects.all().order_by('-date_added')[:15]
    
    # Format individual activities
    activities = []
    
    # Format reviews
    for review in reviews:
        content_object = review.media
        poster_path = None
        tmdb_id = None
        
        if isinstance(content_object, Movie):
            content_type = "Movie"
            title = content_object.title
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
        elif isinstance(content_object, TVShow):
            content_type = "TV Show"
            title = content_object.title
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
            if review.season:
                title += f" - {review.season}"
        else:
            content_type = review.content_type.model.capitalize()
            title = getattr(content_object, 'title', 'Unknown')
        
        # Use the minute as the key for grouping
        timestamp_key = review.date_added.strftime('%Y-%m-%d %H:%M')
        
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
        content_object = item.media
        poster_path = None
        tmdb_id = None
        
        if isinstance(content_object, Movie):
            content_type = "Movie"
            title = content_object.title
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
        elif isinstance(content_object, TVShow):
            content_type = "TV Show"
            title = content_object.title
            poster_path = content_object.poster
            tmdb_id = content_object.tmdb_id
        else:
            content_type = item.content_type.model.capitalize()
            title = getattr(content_object, 'title', 'Unknown')
        
        timestamp_key = item.date_added.strftime('%Y-%m-%d %H:%M')
            
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
        timestamp_key = movie.date_added.strftime('%Y-%m-%d %H:%M')
        
        activities.append({
            'type': 'new_content',
            'username': movie.added_by.username if movie.added_by else 'System',
            'title': movie.title,
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
        
        # Add action based on type
        if activity['type'] == 'review':
            grouped_activities[key]['actions'].append('reviewed')
        elif activity['type'] == 'watchlist':
            grouped_activities[key]['actions'].append('added to watchlist')
        elif activity['type'] == 'new_content':
            grouped_activities[key]['actions'].append('added to database')
    
    # Convert grouped activities to list and format actions
    result = []
    for _, activity in grouped_activities.items():
        # Format the actions into a readable string
        if len(activity['actions']) == 1:
            activity['action'] = activity['actions'][0]
        else:
            activity['action'] = ' and '.join([', '.join(activity['actions'][:-1]), activity['actions'][-1]])
        
        # Remove actions list from final output
        del activity['actions']
        result.append(activity)
    
    # Sort by timestamp again to ensure newest first
    result.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Take only the first 15 grouped activities
    result = result[:15]
    
    # Return as JSON
    return JsonResponse(result, safe=False)



