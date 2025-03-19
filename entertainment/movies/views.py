from django.shortcuts import render, get_object_or_404
from api.services.movies import MoviesService
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

from .models import Movie
from .models import Country, Genre, Person, Keyword
from .serializers import MovieSerializer
# Create your views here.

def create_movie_page(request):
    return render(request, 'create_movie.html')


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

        # Fetch movie details from TMDB API using the provided ID
        movie_details = MoviesService().get_movie_details(movie_id)
        if not movie_details:
            return Response({"error": "Movie not found in TMDB"}, status=status.HTTP_404_NOT_FOUND)

        # Extract poster and backdrop URLs
        poster_url = request.data.get('poster')
        backdrop_url = request.data.get('backdrop')

        # Create or get related countries, genres, and people
        countries = movie_details.get('production_countries', [])
        genres = movie_details.get('genres', [])
        people = MoviesService().get_movie_credits(movie_id).get('cast', [])[:5]
        print(genres)

        country_instances = []
        for country in countries:
            country_instance, _ = Country.objects.get_or_create(
            iso_3166_1=country.get('iso_3166_1'),
            defaults={'name': country.get('name')}
            )
            country_instances.append(country_instance)

        genre_instances = []
        for genre in genres:
            genre_instance, _ = Genre.objects.get_or_create(
                tmdb_id=genre.get('id'),
                defaults={'name': genre.get('name')}
            )
            genre_instances.append(genre_instance)
        
        keywords = MoviesService().get_movie_keywords(movie_id).get('keywords', [])
        keyword_instances = []
        for keyword in keywords:
            keyword_instance, _ = Keyword.objects.get_or_create(
                tmdb_id=keyword.get('id'),
                defaults={'name': keyword.get('name')}
            )
            keyword_instances.append(keyword_instance)

        # Create a new movie instance with the fetched details
        movie_data = {
            'title': movie_details.get('title'),
            'original_title': movie_details.get('original_title'),
            'poster': poster_url,
            'backdrops': {'backdrop': backdrop_url},
            'release_date': movie_details.get('release_date'),
            'tmdb_id': movie_details.get('id'),
            'runtime': movie_details.get('runtime'),
            'plot': movie_details.get('overview'),
            'rating': movie_details.get('vote_average'),
            'trailer': movie_details.get('videos', {}).get('results', [{}])[0].get('key') if movie_details.get('videos', {}).get('results') else None,
            'genres': [genre.pk for genre in genre_instances],
            'countries': [country.pk for country in country_instances],
            'keywords': [keyword.pk for keyword in keyword_instances]
        }
        serializer = MovieSerializer(data=movie_data)
        if serializer.is_valid():
            movie = serializer.save()
            movie.countries.set(country_instances)
            movie.genres.set(genre_instances)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
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

