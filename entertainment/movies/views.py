from django.shortcuts import render, get_object_or_404
from api.services.movies import MoviesService
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

import json

from custom_auth.models import Movie
from .serializers import MovieSerializer
from .parsers import create_movie
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.decorators import login_required
from custom_auth.models import Watchlist, Genre
from django.http import JsonResponse

# Create your views here.

def create_movie_page(request):
    return render(request, 'create_movie.html')

def movie_page(request, movie_id):
    movie_db = Movie.objects.filter(tmdb_id=movie_id).first()
    if not movie_db:
        create_movie(movie_id)
        movie_db = Movie.objects.filter(tmdb_id=movie_id).first()
    context = {
        'movie': movie_db
    }
    return render(request, 'movie_page.html', context)

@login_required
def watchlist_page(request):
    """Display the user's watchlist with filtering options."""
    
    # Get watchlist items
    watchlist_items = request.user.get_watchlist()
    
    # Handle filters
    media_type = request.GET.get('media_type')
    genre_id = request.GET.get('genre')
    sort_by = request.GET.get('sort_by', '-date_added')  # Default sort by date added
    filter_title = request.GET.get('title', '')  # Get title filter parameter
    
    # Apply filters if provided
    if media_type:
        content_type = ContentType.objects.get(model=media_type.lower())
        watchlist_items = watchlist_items.filter(content_type=content_type)
    
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


class TMDBSearchView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search Movies",
        description="Search for movies using the TMDB API by providing a query string and optional filters.",
        parameters=[
            OpenApiParameter(name="query", description="Search term for movies", required=True, type=str),
            OpenApiParameter(name="page", description="Page number for pagination (default is 1)", required=False, type=int),
            OpenApiParameter(name="include_adult", description="Include adult content in results (default is False)", required=False, type=bool),
            OpenApiParameter(name="language", description="Language of the results (e.g., 'en-US')", required=False, type=str),
            OpenApiParameter(name="primary_release_year", description="Filter results by primary release year", required=False, type=int),
            OpenApiParameter(name="region", description="Filter results by region (e.g., 'US')", required=False, type=str),
            OpenApiParameter(name="year", description="Filter results by year", required=False, type=int),
        ]
    )
    def get(self, request):
        query = request.GET.get('query')
        page = request.GET.get('page', 1)
        include_adult = request.GET.get('include_adult', False)
        language = request.GET.get('language')
        primary_release_year = request.GET.get('primary_release_year')
        region = request.GET.get('region')
        year = request.GET.get('year')

        if not query:
            return Response({"error": "Query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        params = {
            'query': query,
            'include_adult': include_adult,
            'language': language,
            'page': page,
            'primary_release_year': primary_release_year,
            'region': region,
            'year': year
        }

        movies = MoviesService().search_movies(**params)
        sorted_movies = sorted(movies['results'], key=lambda x: x['popularity'], reverse=True)
        
        # Add a field to show if movie is already in database
        existing_movie_ids = Movie.objects.filter(
            tmdb_id__in=[movie['id'] for movie in sorted_movies]
        ).values_list('tmdb_id', flat=True)
        
        # Get the user's watchlist movie IDs
        user_watchlist = Watchlist.objects.filter(
            user=request.user,
            content_type=ContentType.objects.get_for_model(Movie)
        ).values_list('object_id', flat=True)
        
        user_watchlist_tmdb_ids = Movie.objects.filter(
            id__in=user_watchlist
        ).values_list('tmdb_id', flat=True)
        
        for movie in sorted_movies:
            movie['in_database'] = movie['id'] in existing_movie_ids
            movie['in_watchlist'] = movie['id'] in user_watchlist_tmdb_ids
            
        movies['results'] = sorted_movies
        return Response(movies, status=status.HTTP_200_OK)

class MovieViewSet(viewsets.ViewSet):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]
    queryset = Movie.objects.all()
    serializer_class = MovieSerializer

    @extend_schema(
        summary="Get Movies",
        description="Get a list of movies.",
        responses={200: MovieSerializer(many=True)}
    )
    def list(self, request):
        queryset = Movie.objects.all()
        serializer = MovieSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Create Movie",
        description="Create a new movie.",
        request=MovieSerializer,
        responses={201: MovieSerializer},
    )
    def create(self, request):
        movie_id = request.data.get('id')
        if not movie_id:
            return Response({"error": "Movie ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if Movie.objects.filter(tmdb_id=movie_id).exists():
            return Response({"error": "Movie already exists"}, status=status.HTTP_400_BAD_REQUEST)
        
        movie_poster = request.data.get('poster')
        movie_backdrop = request.data.get('backdrop')

        is_anime = request.data.get('is_anime', False)
        add_to_watchlist = request.data.get('add_to_watchlist', False)

        # if movie_poster or movie_backdrop is added add the url https://image.tmdb.org/t/p/original to the front
        if movie_poster:
            movie_poster = f"https://image.tmdb.org/t/p/original{movie_poster}"
        if movie_backdrop:
            movie_backdrop = f"https://image.tmdb.org/t/p/original{movie_backdrop}"

        movie = create_movie(movie_id, movie_poster, movie_backdrop, is_anime, add_to_watchlist)
        if not movie:
            return Response({"error": "Movie not found"}, status=status.HTTP_404_NOT_FOUND)
        
            
        return Response(MovieSerializer(movie).data, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        summary="Get Movie",
        description="Get a movie by ID.",
        responses={200: MovieSerializer}
    )
    def retrieve(self, request, pk=None):
        queryset = Movie.objects.all()
        movie = get_object_or_404(queryset, pk=pk)
        serializer = MovieSerializer(movie)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Update Movie",
        description="Update a movie by ID.",
        request=MovieSerializer,
        responses={200: MovieSerializer}
    )
    def update(self, request, pk=None):
        movie = Movie.objects.get(pk=pk)
        serializer = MovieSerializer(movie, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PopularMoviesView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Popular Movies",
        description="Retrieve the top 5 popular movies with poster URLs.",
        responses={200: OpenApiExample("Popular Movies", value=[
            {"title": "Movie 1", "poster_path": "https://image.tmdb.org/t/p/w500/path_to_poster1"},
            {"title": "Movie 2", "poster_path": "https://image.tmdb.org/t/p/w500/path_to_poster2"},
        ])}
    )
    def get(self, request):
        popular_movies = MoviesService().get_popular_movies()
        popular_movies = sorted(popular_movies['results'], key=lambda x: x['popularity'], reverse=True)[:5]
        for movie in popular_movies:
            movie['poster_path'] = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}"
        return Response(popular_movies, status=status.HTTP_200_OK)
    
class MovieImagesView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Movie Pictures",
        description="gives the movie posters and backdrops in the create movie page.",
        parameters=[
            OpenApiParameter(name="movie_id", description="ID of the movie to retrieve pictures for", required=True, type=int),
        ]
    )
    def get(self, request):
        movie_id = request.GET.get('movie_id')
        if not movie_id:
            return Response({"error": "Movie ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        movie = MoviesService().get_movie_images(movie_id)
        if not movie:
            return Response({"error": "Movie not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(json.dumps(movie), status=status.HTTP_200_OK)