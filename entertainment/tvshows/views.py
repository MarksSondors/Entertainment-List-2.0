from django.shortcuts import render, get_object_or_404
from api.services.tv_shows import TVShowsService
from django.http import JsonResponse, Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

import json

from custom_auth.models import TVShow
from .serializers import TVShowSerializer
from .parsers import create_tv_show
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.decorators import login_required
from custom_auth.models import Watchlist, Genre


# Create your views here.

def create_tv_show_page(request):
    return render(request, 'create_tv_show.html')

@login_required
def tv_show_page(request, show_id):
    try:
        tv_show_db = TVShow.objects.prefetch_related(
            'genres', 
            'countries', 
            'keywords'
        ).get(tmdb_id=show_id)
    except TVShow.DoesNotExist:
        tv_show_db = create_tv_show(show_id)
        if not tv_show_db:
            raise Http404(f"TV Show with ID {show_id} could not be created")
    context = {
        'tv_show': tv_show_db
    }
    return render(request, 'tv_show_page.html', context)


class TMDBTVSearchView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search TV Shows",
        description="Search for TV shows using the TMDB API by providing a query string and optional filters.",
        parameters=[
            OpenApiParameter(name="query", description="Search term for TV shows", required=True, type=str),
            OpenApiParameter(name="page", description="Page number for pagination (default is 1)", required=False, type=int),
            OpenApiParameter(name="include_adult", description="Include adult content in results (default is False)", required=False, type=bool),
            OpenApiParameter(name="language", description="Language of the results (e.g., 'en-US')", required=False, type=str),
            OpenApiParameter(name="first_air_date_year", description="Filter results by first air date year", required=False, type=int),
            OpenApiParameter(name="region", description="Filter results by region (e.g., 'US')", required=False, type=str),
        ]
    )
    def get(self, request):
        query = request.GET.get('query')
        page = request.GET.get('page', 1)
        include_adult = request.GET.get('include_adult', False)
        language = request.GET.get('language')
        first_air_date_year = request.GET.get('first_air_date_year')
        region = request.GET.get('region')

        if not query:
            return Response({"error": "Query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        params = {
            'query': query,
            'include_adult': include_adult,
            'language': language,
            'page': page,
            'first_air_date_year': first_air_date_year,
            'region': region
        }

        shows = TVShowsService().search_shows(**params)
        sorted_shows = sorted(shows['results'], key=lambda x: x['popularity'], reverse=True)
        
        # Add a field to show if TV show is already in database
        existing_show_ids = TVShow.objects.filter(
            tmdb_id__in=[show['id'] for show in sorted_shows]
        ).values_list('tmdb_id', flat=True)
        
        # Get the user's watchlist TV show IDs
        user_watchlist = Watchlist.objects.filter(
            user=request.user,
            content_type=ContentType.objects.get_for_model(TVShow)
        ).values_list('object_id', flat=True)
        
        user_watchlist_tmdb_ids = TVShow.objects.filter(
            id__in=user_watchlist
        ).values_list('tmdb_id', flat=True)
        
        for show in sorted_shows:
            show['in_database'] = show['id'] in existing_show_ids
            show['in_watchlist'] = show['id'] in user_watchlist_tmdb_ids
            
        shows['results'] = sorted_shows
        return Response(shows, status=status.HTTP_200_OK)

class TVShowViewSet(viewsets.ViewSet):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]
    queryset = TVShow.objects.all()
    serializer_class = TVShowSerializer

    @extend_schema(
        summary="Get TV Shows",
        description="Get a list of TV shows.",
        responses={200: TVShowSerializer(many=True)}
    )
    def list(self, request):
        queryset = TVShow.objects.all()
        serializer = TVShowSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Create TV Show",
        description="Create a new TV show.",
        request=TVShowSerializer,
        responses={201: TVShowSerializer},
    )
    def create(self, request):
        show_id = request.data.get('id')
        if not show_id:
            return Response({"error": "TV Show ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        add_to_watchlist = request.data.get('add_to_watchlist', False)

        if TVShow.objects.filter(tmdb_id=show_id).exists():
            if add_to_watchlist:
                # Add to watchlist if it already exists
                content_type = ContentType.objects.get_for_model(TVShow)
                watchlist_item, created = Watchlist.objects.get_or_create(
                    user=request.user,
                    content_type=content_type,
                    object_id=TVShow.objects.filter(tmdb_id=show_id).first().id,
                )
                if created:
                    return Response({"message": "TV Show added to watchlist"}, status=status.HTTP_201_CREATED)
                else:
                    return Response({"message": "TV Show already in watchlist"}, status=status.HTTP_200_OK)
            return Response({"error": "TV Show already exists"}, status=status.HTTP_400_BAD_REQUEST)
        
        show_poster = request.data.get('poster')
        show_backdrop = request.data.get('backdrop')

        is_anime = request.data.get('is_anime', False)
        # Add the URL base if poster or backdrop is provided
        if show_poster:
            show_poster = f"https://image.tmdb.org/t/p/original{show_poster}"
        if show_backdrop:
            show_backdrop = f"https://image.tmdb.org/t/p/original{show_backdrop}"

        tv_show = create_tv_show(show_id, show_poster, show_backdrop, is_anime, add_to_watchlist)
        if not tv_show:
            return Response({"error": "TV Show not found"}, status=status.HTTP_404_NOT_FOUND)
        
        return Response(TVShowSerializer(tv_show).data, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        summary="Get TV Show",
        description="Get a TV show by ID.",
        responses={200: TVShowSerializer}
    )
    def retrieve(self, request, pk=None):
        queryset = TVShow.objects.all()
        tv_show = get_object_or_404(queryset, pk=pk)
        serializer = TVShowSerializer(tv_show)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Update TV Show",
        description="Update a TV show by ID.",
        request=TVShowSerializer,
        responses={200: TVShowSerializer}
    )
    def update(self, request, pk=None):
        tv_show = TVShow.objects.get(pk=pk)
        serializer = TVShowSerializer(tv_show, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PopularTVShowsView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Popular TV Shows",
        description="Retrieve the top 5 popular TV shows with poster URLs.",
        responses={200: OpenApiExample("Popular TV Shows", value=[
            {"name": "Show 1", "poster_path": "https://image.tmdb.org/t/p/w500/path_to_poster1"},
            {"name": "Show 2", "poster_path": "https://image.tmdb.org/t/p/w500/path_to_poster2"},
        ])}
    )
    def get(self, request):
        popular_shows = TVShowsService().get_popular_shows()
        popular_shows = sorted(popular_shows['results'], key=lambda x: x['popularity'], reverse=True)[:5]
        for show in popular_shows:
            show['poster_path'] = f"https://image.tmdb.org/t/p/w500{show['poster_path']}"
        return Response(popular_shows, status=status.HTTP_200_OK)
    
class TVShowImagesView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get TV Show Pictures",
        description="Provides the TV show posters and backdrops in the create TV show page.",
        parameters=[
            OpenApiParameter(name="show_id", description="ID of the TV show to retrieve pictures for", required=True, type=int),
        ]
    )
    def get(self, request):
        show_id = request.GET.get('show_id')
        if not show_id:
            return Response({"error": "TV Show ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        show = TVShowsService().get_show_images(show_id)
        if not show:
            return Response({"error": "TV Show not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(json.dumps(show), status=status.HTTP_200_OK)
