from django.shortcuts import render, get_object_or_404
from api.services.music import MusicService
from django.http import JsonResponse, Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django.contrib.contenttypes.models import ContentType
from custom_auth.models import Album, Watchlist, Person, Movie, TVShow
from .parsers import create_album_from_musicbrainz


class MusicSearchView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search Music",
        description="Search for artists, albums, or songs using the MusicBrainz API.",
        parameters=[
            OpenApiParameter(name="query", description="Search term for music", required=True, type=str),
            OpenApiParameter(name="type", description="Type of search (artists, albums, songs)", required=False, type=str, default="artists"),
            OpenApiParameter(name="limit", description="Maximum number of results to return (default is 25)", required=False, type=int),
            OpenApiParameter(name="offset", description="Offset for pagination (default is 0)", required=False, type=int),
        ]
    )
    def get(self, request):
        query = request.GET.get('query')
        search_type = request.GET.get('type', 'artists').lower()
        limit = request.GET.get('limit', 25)
        offset = request.GET.get('offset', 0)

        if not query:
            return Response({"error": "Query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        music_service = MusicService()
        results = {}

        try:
            if search_type == 'artists':
                results = music_service.search_artists(query, limit=limit, offset=offset)
                if 'artists' in results:
                    results['results'] = sorted(results['artists'], 
                                              key=lambda x: x.get('score', 0), reverse=True)
                    del results['artists']
            elif search_type == 'albums':
                results = music_service.search_albums(query, limit=limit, offset=offset)
                if 'release-groups' in results:
                    results['results'] = sorted(results['release-groups'], 
                                              key=lambda x: x.get('score', 0), reverse=True)
                    del results['release-groups']
                    
                    # Check which albums are already in database
                    album_ids = [album.get('id') for album in results['results'] if album.get('id')]
                    existing_albums = Album.objects.filter(musicbrainz_id__in=album_ids)
                    existing_ids = set(existing_albums.values_list('musicbrainz_id', flat=True))
                    
                    # Get user's watchlist albums
                    album_content_type = ContentType.objects.get_for_model(Album)
                    user_watchlist_album_ids = Watchlist.objects.filter(
                        user=request.user,
                        content_type=album_content_type
                    ).values_list('object_id', flat=True)
                    
                    watchlist_musicbrainz_ids = set(
                        Album.objects.filter(id__in=user_watchlist_album_ids)
                        .values_list('musicbrainz_id', flat=True)
                    )
                    
                    # Add database and watchlist flags
                    for album in results['results']:
                        if album.get('id') in existing_ids:
                            album['in_database'] = True
                            album['in_watchlist'] = album.get('id') in watchlist_musicbrainz_ids
                        else:
                            album['in_database'] = False
                            album['in_watchlist'] = False
                    
            elif search_type == 'songs':
                results = music_service.search_recordings(query, limit=limit, offset=offset)
                if 'recordings' in results:
                    results['results'] = sorted(results['recordings'], 
                                              key=lambda x: x.get('score', 0), reverse=True)
                    del results['recordings']
            else:
                return Response({"error": f"Invalid search type: {search_type}. Use 'artists', 'albums', or 'songs'"},
                               status=status.HTTP_400_BAD_REQUEST)

            # Ensure we always have a results key for consistent response structure
            if 'results' not in results:
                results = {'results': results.get(next(iter(results), []))}

            # Add count information if not present
            if 'count' not in results and 'results' in results:
                results['count'] = len(results['results'])
                            
            return Response(results, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({"error": f"Search failed: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AlbumViewSet(viewsets.ViewSet):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        summary="Create Album",
        description="Create a new album from MusicBrainz ID and optionally add to watchlist. If marked as soundtrack, will attempt to link with movie/TV show.",
        request=OpenApiExample("Album Request", value={
            "id": "123e4567-e89b-12d3-a456-426614174000",  # MusicBrainz ID
            "add_to_watchlist": True,
        }),
        responses={
            201: OpenApiExample("Album Created", value={"id": 1, "title": "Album Title", "artist": "Artist Name"}),
            400: OpenApiExample("Bad Request", value={"error": "Error message"})
        }
    )
    def create(self, request):
        musicbrainz_id = request.data.get('id')
        add_to_watchlist = request.data.get('add_to_watchlist', False)
        
        if not musicbrainz_id:
            return Response({"error": "MusicBrainz ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Check if album already exists in database
            existing_album = Album.objects.filter(musicbrainz_id=musicbrainz_id).first()
            
            if existing_album:
                album = existing_album
            else:
                # Create new album from MusicBrainz data
                album = create_album_from_musicbrainz(musicbrainz_id)
                
            if not album:
                return Response({"error": "Failed to create album. Could not fetch data from MusicBrainz."}, 
                               status=status.HTTP_400_BAD_REQUEST)
            
            # Add to watchlist if requested
            if add_to_watchlist:
                album_content_type = ContentType.objects.get_for_model(Album)
                watchlist_entry, created = Watchlist.objects.get_or_create(
                    user=request.user,
                    content_type=album_content_type,
                    object_id=album.id
                )
            
            # Prepare response data
            response_data = {
                "id": album.id,
                "title": album.title,
                "artist": album.primary_artist.name if album.primary_artist else "Unknown Artist",
                "musicbrainz_id": album.musicbrainz_id,
                "album_type": album.album_type,
                "added_to_watchlist": add_to_watchlist,
                "release_date": album.release_date.isoformat() if album.release_date else None,
                "cover": album.cover
            }
            
            return Response(response_data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response({"error": f"Album creation failed: {str(e)}"}, 
                           status=status.HTTP_500_INTERNAL_SERVER_ERROR)