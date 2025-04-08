from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout  # Import the logout function
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType
# Create your views here.
# import services

from .models import *
from movies.models import Movie

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
    for review in movie_reviews[:5]:  # Limit to top 5 rated movies
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
    person_credits = MediaPerson.objects.filter(person=person).select_related(
        'content_type'
    )
    
    # For each credit, ensure we have the actual media object
    valid_credits = []
    for credit in person_credits:
        try:
            # Get the actual movie or TV show object via the generic relation
            credit.media = credit.content_type.get_object_for_this_type(id=credit.object_id)
            valid_credits.append(credit)
        except (Movie.DoesNotExist, TVShow.DoesNotExist):
            continue
    
    # Replace person_credits with only valid credits
    person_credits = valid_credits
    
    # Sort credits by release date (newest first)
    person_credits = sorted(
        person_credits,
        key=lambda x: (
            x.media.release_date.year if hasattr(x.media, 'release_date') and x.media.release_date else 
            x.media.first_air_date.year if hasattr(x.media, 'first_air_date') and x.media.first_air_date else 
            0
        ),
        reverse=True
    )
    
    context = {
        'person': person,
        'person_credits': person_credits,
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
    reviews = Review.objects.all().order_by('-date_reviewed')[:10]
    
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
            'created_at': review.date_reviewed.strftime('%Y-%m-%d %H:%M')
        })
    return JsonResponse(review_data, safe=False)