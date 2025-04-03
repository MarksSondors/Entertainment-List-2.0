from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout  # Import the logout function
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
# Create your views here.
# import services

from .models import *


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

def home_page(request):
    if request.user.is_authenticated:
        context = {
            'user': request.user,
        }
        return render(request, 'home_page.html', context)
    else:
        return redirect('login_page')

def browse_by_genre(request):
    anime_only = request.GET.get('anime_only') == 'on'
    genres = Genre.objects.all()
    return render(request, 'browse_by_genre.html', {
        'genres': genres,
        'anime_only': anime_only,
    })

def genre_detail(request, genre_id):
    genre = get_object_or_404(Genre, pk=genre_id)
    anime_filter = request.GET.get('anime_filter', 'all')
    view_type = request.GET.get('view_type', 'grid')
    
    # Filter movies and TV shows based on anime_filter
    if anime_filter == 'anime_only':
        movies = Movie.objects.filter(genres=genre, is_anime=True)
        tv_shows = TVShow.objects.filter(genres=genre, is_anime=True)
    elif anime_filter == 'no_anime':
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
    
    # Get user's favorite movies
    # In a real implementation, you would sort by review score
    # For now, just get all movies associated with the user
    favorite_movies = Movie.objects.all()[:5]  # Replace with: Movie.objects.filter(user=user)
    
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
        watchlist_items = sorted(watchlist_items, key=lambda x: getattr(x.media, 'release_date', '1900-01-01'))
    elif sort_by == '-release_date':
        watchlist_items = sorted(watchlist_items, key=lambda x: getattr(x.media, 'release_date', '1900-01-01'), reverse=True)
    # Default sorting by date_added is handled by the model's Meta ordering
    
    # Get only the genres that are in the watchlist items
    genre_ids = set()
    for item in watchlist_items:
        if hasattr(item.media, 'genres'):
            genre_ids.update(item.media.genres.values_list('id', flat=True))
    genres = Genre.objects.filter(id__in=genre_ids).distinct()
    
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