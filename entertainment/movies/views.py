from django.shortcuts import render
from api.services.movies import MoviesService
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
# Create your views here.


# rest framework 

def create_movie_page(request):
    return render(request, 'create_movie.html')


class TMDBSearchView(APIView):
    """
    A Django Rest Framework API view that handles GET requests to search for movies using the TMDB API.

    Methods:
        - get(request): Accepts a 'query' parameter for the search term and an optional 'page' parameter for pagination.
          Returns a JSON response with the search results or an error message if the query parameter is missing.
    """
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
        return Response(movies, status=status.HTTP_200_OK)

