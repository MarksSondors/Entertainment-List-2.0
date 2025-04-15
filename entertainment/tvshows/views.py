from django.shortcuts import render, get_object_or_404
from api.services.tvshows import TVShowsService
from django.http import JsonResponse, Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from django_q.tasks import async_task
from .tasks import create_tvshow_async

import json

from .models import *
from .serializers import TVShowSerializer
from .parsers import create_tvshow
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.decorators import login_required
from custom_auth.models import Watchlist, Genre


# Create your views here.

@login_required
def tv_show_page(request, show_id):
    try:
        tv_show_db = TVShow.objects.prefetch_related(
            'genres', 
            'countries', 
            'keywords',
            'seasons',
            'seasons__episodes'
        ).get(tmdb_id=show_id)
    except TVShow.DoesNotExist:
        tv_show_db = create_tvshow(show_id)
        if not tv_show_db:
            raise Http404(f"TV Show with ID {show_id} could not be created")
    user_watchlist = Watchlist.objects.filter(
        user=request.user,
        content_type=ContentType.objects.get_for_model(TVShow),
        object_id=tv_show_db.id
    ).exists()
    
    # Get all watched episodes for this user and this show
    watched_episodes = WatchedEpisode.objects.filter(
        user=request.user,
        episode__season__show=tv_show_db
    ).values_list('episode_id', flat=True)
    
    # For each season, calculate progress
    for season in tv_show_db.seasons.all():
        # Get all episodes for this season
        total_episodes = season.episodes.count()
        
        # Mark episodes as watched or not
        for episode in season.episodes.all():
            episode.is_watched = episode.id in watched_episodes
        
        # Count watched episodes
        watched_count = sum(1 for episode in season.episodes.all() if episode.id in watched_episodes)
        
        # Calculate progress percentage
        season.watched_episodes_count = watched_count
        season.progress = (watched_count / total_episodes * 100) if total_episodes > 0 else 0
    
    context = {
        'tv_show': tv_show_db,
        'user_watchlist': user_watchlist,
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
        description="Create a new TV show in the background.",
        request=TVShowSerializer,
        responses={
            201: TVShowSerializer,
            202: OpenApiExample("Task Queued", value={"message": "TV show creation has been queued", "task_id": "task-uuid-example"})
        },
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

        # Queue the TV show creation as a background task using your existing function
        task_id = create_tvshow_async(
            tvshow_id=show_id, 
            tvshow_poster=show_poster, 
            tvshow_backdrop=show_backdrop, 
            is_anime=is_anime, 
            add_to_watchlist=add_to_watchlist, 
            user_id=request.user.id
        )
        
        # Return a response immediately with the task ID
        return Response({
            "message": "TV show creation has been queued",
            "task_id": str(task_id)
        }, status=status.HTTP_202_ACCEPTED)
    
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
        popular_shows = sorted(popular_shows['results'], key=lambda x: x['popularity'], reverse=True)[:10]
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

class WatchlistTVShow(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Add TV Show to Watchlist",
        description="Add a TV show to the user's watchlist.",
        request=TVShowSerializer,
        responses={201: OpenApiExample("Watchlist TV Show", value={"message": "TV Show added to watchlist"})}
    )
    def post(self, request):
        show_id = request.data.get('id')
        if not show_id:
            return Response({"error": "TV Show ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(TVShow)
        watchlist_item, created = Watchlist.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=show_id,
        )
        if created:
            return Response({"message": "TV Show added to watchlist"}, status=status.HTTP_201_CREATED)
        else:
            return Response({"message": "TV Show already in watchlist"}, status=status.HTTP_200_OK)

    def delete(self, request):
        show_id = request.data.get('id')
        if not show_id:
            return Response({"error": "TV Show ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(TVShow)
        try:
            watchlist_item = Watchlist.objects.get(
                user=request.user,
                content_type=content_type,
                object_id=show_id,
            )
            watchlist_item.delete()
            return Response({"message": "TV Show removed from watchlist"}, status=status.HTTP_204_NO_CONTENT)
        except Watchlist.DoesNotExist:
            return Response({"error": "TV Show not in watchlist"}, status=status.HTTP_404_NOT_FOUND)

# Add this new view to check task status

class TaskStatusView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Check Task Status",
        description="Check the status of a background TV show creation task.",
        parameters=[
            OpenApiParameter(name="task_id", description="ID of the task to check", required=True, type=str),
        ],
        responses={
            200: OpenApiExample("Task Status", value={
                "complete": True,
                "success": True,
                "tv_show": {"id": 1, "title": "Show Name", "...": "other fields"}
            })
        }
    )
    def get(self, request):
        task_id = request.GET.get('task_id')
        if not task_id:
            return Response({"error": "Task ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        from django_q.models import Task
        try:
            task = Task.objects.get(id=task_id)
            
            response_data = {
                "complete": task.success is not None,
                "success": task.success if task.success is not None else None,
            }
            
            # If task is complete and successful, try to get the TV show
            if task.success and task.result:
                try:
                    # The result should be a TVShow object
                    tv_show = task.result
                    response_data["tv_show"] = TVShowSerializer(tv_show).data
                except Exception as e:
                    response_data["result"] = str(task.result)
                    response_data["error"] = str(e)
            elif not task.success and task.result:
                # If task failed, include error information
                response_data["error"] = str(task.result)
                
            return Response(response_data)
            
        except Task.DoesNotExist:
            return Response({"error": "Task not found"}, status=status.HTTP_404_NOT_FOUND)

class EpisodeWatchedView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    def post(self, request, episode_id):
        episode = get_object_or_404(Episode, id=episode_id)
        watched = request.data.get('watched', True)
        season_number = request.data.get('season_number')
        episode_number = request.data.get('episode_number')
        mark_previous = request.data.get('mark_previous', False)
        
        marked_episodes = []
        
        # Handle marking as watched
        if watched:
            # Mark the selected episode as watched
            WatchedEpisode.objects.get_or_create(
                user=request.user,
                episode=episode
            )
            
            # If mark_previous is True and not a special (season 0), mark all previous episodes as watched
            if mark_previous and season_number != 0:
                # Get all previous episodes in the same season
                season = episode.season
                previous_episodes = Episode.objects.filter(
                    season=season,
                    episode_number__lt=episode_number
                ).order_by('episode_number')
                
                # Mark each previous episode as watched
                for prev_episode in previous_episodes:
                    _, created = WatchedEpisode.objects.get_or_create(
                        user=request.user,
                        episode=prev_episode
                    )
                    if created:
                        marked_episodes.append(prev_episode.id)
        else:
            # Remove the episode from watched episodes
            WatchedEpisode.objects.filter(
                user=request.user,
                episode=episode
            ).delete()
        
        # Calculate season progress
        season_progress = {}
        for user_season in episode.season.show.seasons.all():
            total_episodes = user_season.episodes.count()
            watched_episodes = WatchedEpisode.objects.filter(
                user=request.user,
                episode__season=user_season
            ).count()
            
            percentage = 0
            if total_episodes > 0:
                percentage = (watched_episodes / total_episodes) * 100
                
            season_progress[user_season.id] = {
                'total': total_episodes,
                'watched': watched_episodes,
                'percentage': percentage
            }
        
        # Calculate overall show progress
        total_episodes = episode.season.show.episodes_count
        watched_episodes = WatchedEpisode.objects.filter(
            user=request.user,
            episode__season__show=episode.season.show
        ).count()
        
        show_percentage = 0
        if total_episodes > 0:
            show_percentage = (watched_episodes / total_episodes) * 100
            
        show_progress = {
            'total': total_episodes,
            'watched': watched_episodes,
            'percentage': show_percentage
        }
        
        return Response({
            'success': True,
            'season_progress': season_progress,
            'show_progress': show_progress,
            'marked_episodes': marked_episodes
        })

class TVShowReviewView(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        summary="Create TV Show Review",
        description="Create a review for a TV show, either for a specific season or episode subgroup. Season is the default choice.",
        request=OpenApiExample("Review Request", value={
            "tv_show_id": 1,
            "season_id": 5,  # Optional if episode_subgroup_id is provided
            "episode_subgroup_id": None,  # Optional if season_id is provided
            "rating": 8.5,
            "review_text": "Great season with unexpected twists"
        }),
        responses={
            201: OpenApiExample("Review Created", value={"message": "Review created successfully"}),
            400: OpenApiExample("Bad Request", value={"error": "Error message"}),
            404: OpenApiExample("Not Found", value={"error": "TV Show/Season/Episode subgroup not found"})
        }
    )
    def post(self, request):
        tv_show_id = request.data.get('tv_show_id')
        season_id = request.data.get('season_id')
        episode_subgroup_id = request.data.get('episode_subgroup_id')
        rating = request.data.get('rating')
        review_text = request.data.get('review_text', '')
        
        # Validate required fields
        if not tv_show_id:
            return Response({"error": "TV Show ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not rating:
            return Response({"error": "Rating is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if not season_id and not episode_subgroup_id:
            return Response({"error": "Either Season ID or Episode Subgroup ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        if season_id and episode_subgroup_id:
            return Response({"error": "Cannot provide both Season ID and Episode Subgroup ID"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Get the TV show
            tv_show = get_object_or_404(TVShow, id=tv_show_id)
            
            # Get the content type for TVShow
            content_type = ContentType.objects.get_for_model(TVShow)
            
            # Check if season or episode subgroup exists
            season = None
            episode_subgroup = None
            
            if season_id:
                season = get_object_or_404(Season, id=season_id, show=tv_show)
                
                # Check if user has completed the season
                if not season.user_has_completed(request.user):
                    return Response(
                        {"error": f"You must watch all episodes in {season.name} before reviewing"}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            if episode_subgroup_id:
                episode_subgroup = get_object_or_404(EpisodeSubGroup, id=episode_subgroup_id, show=tv_show)
                
                # Check if user has completed the episode subgroup
                if not episode_subgroup.user_has_completed(request.user):
                    return Response(
                        {"error": f"You must watch all episodes in {episode_subgroup.name} before reviewing"}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Check for existing review
            existing_review = Review.objects.filter(
                user=request.user,
                content_type=content_type,
                object_id=tv_show.id,
                season=season,
                episode_subgroup=episode_subgroup
            ).first()
            
            if existing_review:
                # Update existing review
                existing_review.rating = float(rating)
                existing_review.review_text = review_text
                existing_review.save()
                return Response({"message": "Review updated successfully"}, status=status.HTTP_200_OK)
            
            # Create new review
            try:
                review = Review.objects.create(
                    user=request.user,
                    content_type=content_type,
                    object_id=tv_show.id,
                    season=season,
                    episode_subgroup=episode_subgroup,
                    rating=float(rating),
                    review_text=review_text
                )
                
                return Response({
                    "message": "Review created successfully",
                    "review_id": review.id
                }, status=status.HTTP_201_CREATED)
                
            except ValueError as e:
                return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            
        except TVShow.DoesNotExist:
            return Response({"error": f"TV Show with ID {tv_show_id} not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @extend_schema(
        summary="Get TV Show Reviews",
        description="Get reviews for a TV show, optionally filtered by season or episode subgroup.",
        parameters=[
            OpenApiParameter(name="tv_show_id", description="ID of the TV show", required=True, type=int),
            OpenApiParameter(name="season_id", description="ID of the season (optional)", required=False, type=int),
            OpenApiParameter(name="episode_subgroup_id", description="ID of the episode subgroup (optional)", required=False, type=int),
        ],
        responses={200: OpenApiExample("Reviews", value=[
            {
                "id": 1,
                "user": "username",
                "rating": 8.5,
                "review_text": "Great season!",
                "date_added": "2023-04-15T12:34:56Z"
            }
        ])}
    )
    def get(self, request):
        tv_show_id = request.query_params.get('tv_show_id')
        season_id = request.query_params.get('season_id')
        episode_subgroup_id = request.query_params.get('episode_subgroup_id')
        
        if not tv_show_id:
            return Response({"error": "TV Show ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            tv_show = get_object_or_404(TVShow, id=tv_show_id)
            content_type = ContentType.objects.get_for_model(TVShow)
            
            # Base query for reviews of this TV show
            reviews_query = Review.objects.filter(
                content_type=content_type,
                object_id=tv_show.id
            )
            
            # Add filters if provided
            if season_id:
                reviews_query = reviews_query.filter(season_id=season_id)
            
            if episode_subgroup_id:
                reviews_query = reviews_query.filter(episode_subgroup_id=episode_subgroup_id)
            
            # Serialize the reviews
            reviews_data = []
            for review in reviews_query:
                reviews_data.append({
                    'id': review.id,
                    'user': review.user.username,
                    'rating': review.rating,
                    'review_text': review.review_text,
                    'date_added': review.date_added,
                    'season': review.season.name if review.season else None,
                    'episode_subgroup': review.episode_subgroup.name if review.episode_subgroup else None
                })
            
            return Response(reviews_data)
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
