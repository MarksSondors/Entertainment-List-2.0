from django.shortcuts import render
from api.services.movies import MoviesService
from django.http import JsonResponse
# Create your views here.

def create_movie_page(request):
    return render(request, 'create_movie.html')


def tmdb_search(request):
    query = request.GET.get('query')
    page = request.GET.get('page', 1)
    movies = MoviesService().search_movies(query=query, page=page)
    return JsonResponse(movies)