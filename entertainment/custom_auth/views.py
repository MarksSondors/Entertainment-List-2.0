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