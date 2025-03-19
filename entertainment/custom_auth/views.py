from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout  # Import the logout function
# Create your views here.
# import services
from api.services.movies import MoviesService

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
        popular_movies = MoviesService().get_popular_movies()
        # edit down the data to only include the first 5 movies and add url to the image
        popular_movies = sorted(popular_movies['results'], key=lambda x: x['popularity'], reverse=True)[:4]
        for movie in popular_movies:
            movie['poster_path'] = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}"
        context = {
            'user': request.user,
            'popular_movies': popular_movies
        }
        return render(request, 'home_page.html', context)
    else:
        return redirect('login_page')