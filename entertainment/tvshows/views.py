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
from django.views.decorators.csrf import csrf_protect
from custom_auth.models import Watchlist, Genre, Review
from .tasks import create_tvshow_async

from django.db.models import Prefetch, Count, Q, OuterRef, Subquery, F

# Create your views here.

@login_required
def tv_show_page(request, show_id):
    try:
        # Initial fetch with all needed relationships prefetched in one query
        tv_show_db = TVShow.objects.prefetch_related(
            'genres', 
            'countries', 
            'keywords',
            'seasons',
            'seasons__episodes',
            Prefetch(
                'episode_groups__sub_groups',
                queryset=EpisodeSubGroup.objects.prefetch_related(
                    Prefetch(
                        'episodes',
                        queryset=Episode.objects.order_by('air_date')
                    )
                )
            )
        ).get(tmdb_id=show_id)
    except TVShow.DoesNotExist:
        # Existing task creation code remains unchanged
        task_id = create_tvshow_async(
            show_id,
            user_id=request.user.id,
            add_to_watchlist=False
        )
        # Wait for task completion code remains unchanged
        from django_q.models import Task
        from time import sleep
        max_attempts = 30
        for _ in range(max_attempts):
            try:
                task = Task.objects.get(id=task_id)
                if task.success is not None:  # Task has completed (success or failure)
                    break
            except Task.DoesNotExist:
                pass
            sleep(1)
            
        # Try to fetch the TV show again - same prefetch pattern as above
        try:
            tv_show_db = TVShow.objects.prefetch_related(
                'genres', 
                'countries', 
                'keywords',
                'seasons',
                'seasons__episodes',
                Prefetch(
                    'episode_groups__sub_groups',
                    queryset=EpisodeSubGroup.objects.prefetch_related(
                        Prefetch(
                            'episodes',
                            queryset=Episode.objects.order_by('air_date')
                        )
                    )
                )
            ).get(tmdb_id=show_id)
        except TVShow.DoesNotExist:
            raise Http404(f"TV Show with ID {show_id} could not be created")
    
    # Get content type once to reuse
    tv_show_content_type = ContentType.objects.get_for_model(TVShow)
    
    # Check if show is in user's watchlist - single query
    user_watchlist = Watchlist.objects.filter(
        user=request.user,
        content_type=tv_show_content_type,
        object_id=tv_show_db.id
    ).exists()
    
    # Get all watched episodes for this user and show in one query with select_related to avoid N+1 issues
    watched_episodes_qs = WatchedEpisode.objects.filter(
        episode__season__show=tv_show_db
    ).select_related('user', 'episode__season')
    
    # Create a set of watched episode IDs for the current user - faster lookups
    user_watched_episodes = set(
        watched_episodes_qs.filter(user=request.user).values_list('episode_id', flat=True)
    )
    
    # Create episode_id -> users mapping efficiently
    episode_watched_by_users = {}
    for watched in watched_episodes_qs:
        if watched.episode_id not in episode_watched_by_users:
            episode_watched_by_users[watched.episode_id] = []
        episode_watched_by_users[watched.episode_id].append(watched.user)
    
    # Get all reviews in one query
    all_reviews = Review.objects.filter(
        content_type=tv_show_content_type,
        object_id=tv_show_db.id
    ).select_related('user', 'season', 'episode_subgroup')
    
    # Create user's season reviews mapping 
    season_reviews = {
        review.season_id: review 
        for review in all_reviews.filter(user=request.user, season__isnull=False)
    }
    
    # Create user's subgroup reviews mapping
    subgroup_reviews = {
        review.episode_subgroup_id: review 
        for review in all_reviews.filter(user=request.user, episode_subgroup__isnull=False)
    }
    
    # Pre-calculate all watched counts for all subgroups in a single query
    subgroup_watched_counts = {
        sg.id: sg.watched_count 
        for sg in EpisodeSubGroup.objects.filter(
            parent_group__show=tv_show_db
        ).annotate(
            watched_count=Count('episodes', filter=Q(episodes__id__in=user_watched_episodes))
        )
    }
    
    # Process seasons with pre-calculated data
    for season in tv_show_db.seasons.all():
        total_episodes = season.episodes.count()
        # Mark episodes as watched using our pre-collected set (fast lookups)
        for episode in season.episodes.all():
            episode.is_watched = episode.id in user_watched_episodes
            episode.watched_users = episode_watched_by_users.get(episode.id, [])
        
        # Calculate progress with a single count
        watched_count = sum(1 for episode in season.episodes.all() if episode.id in user_watched_episodes)
        season.watched_episodes_count = watched_count
        season.progress = (watched_count / total_episodes * 100) if total_episodes > 0 else 0
        
        # Add user's review data
        if season.id in season_reviews:
            review = season_reviews[season.id]
            season.user_rating = review.rating
            season.user_review = review.review_text
            season.user_review_id = review.id
    
    # Process subgroups with pre-calculated data
    subgroups_data = {}
    
    # Pre-collect all episodes in subgroups to avoid N+1 queries
    subgroup_episodes = {}
    for group in tv_show_db.episode_groups.all():
        for subgroup in group.sub_groups.all():
            subgroup_episodes[subgroup.id] = list(subgroup.episodes.all())
    
    # Now build subgroups data
    for group in tv_show_db.episode_groups.all():
        for subgroup in group.sub_groups.all():
            episodes = subgroup_episodes[subgroup.id]
            total_episodes = len(episodes)
            
            # Get first episode still image if available
            first_episode_still = None
            if episodes and episodes[0].still:
                first_episode_still = episodes[0].still
            
            if total_episodes > 0:
                watched_count = subgroup_watched_counts.get(subgroup.id, 0)
                
                subgroup.watched_episodes_count = watched_count
                subgroup.progress = (watched_count / total_episodes) * 100
                
                user_rating = None
                review_id = None
                if subgroup.id in subgroup_reviews:
                    user_rating = subgroup_reviews[subgroup.id].rating
                    review_id = subgroup_reviews[subgroup.id].id
                
                subgroups_data[subgroup.id] = {
                    'watched_count': watched_count,
                    'total': total_episodes,
                    'progress': (watched_count / total_episodes) * 100,
                    'completed': watched_count == total_episodes,
                    'user_rating': user_rating,
                    'review_id': review_id,
                    'progress_display': f"{watched_count}/{total_episodes}",
                    'still_image': first_episode_still
                }
            else:
                subgroup.watched_episodes_count = 0
                subgroup.progress = 0
                subgroups_data[subgroup.id] = {
                    'watched_count': 0,
                    'total': 0,
                    'progress': 0,
                    'completed': False,
                    'user_rating': None,
                    'review_id': None,
                    'progress_display': "0/0",
                    'still_image': first_episode_still
                }

    # Get episodes excluding specials (season 0) - ADD select_related here
    regular_episodes = Episode.objects.filter(
        season__show=tv_show_db,
        season__season_number__gt=0
    ).select_related('season').order_by('season__season_number', 'episode_number')
    
    # Group episodes by season for better organization
    episodes_by_season = {}
    total_regular_episodes = 0
    
    for episode in regular_episodes:
        season_num = episode.season.season_number
        if season_num not in episodes_by_season:
            episodes_by_season[season_num] = []
        episodes_by_season[season_num].append({
            'id': episode.id,
            'season_num': season_num,
            'episode_num': episode.episode_number,
            'title': episode.title,
            'is_watched': episode.id in user_watched_episodes,
        })
        total_regular_episodes += 1
    
    # Get all users who watched at least one episode of this show
    users_with_progress = CustomUser.objects.filter(
        watched_episodes__episode__season__show=tv_show_db,
        watched_episodes__episode__season__season_number__gt=0
    ).distinct()
    
    # Replace the users_progress loop with this approach:
    # Get all watched episodes for all relevant users in one query
    all_watched = WatchedEpisode.objects.filter(
        user__in=users_with_progress,
        episode__season__show=tv_show_db,
        episode__season__season_number__gt=0
    ).select_related('user').values('user_id', 'episode_id')

    # Create a dictionary mapping users to their watched episodes
    user_watched_map = {}
    for entry in all_watched:
        user_id = entry['user_id']
        episode_id = entry['episode_id']
        if user_id not in user_watched_map:
            user_watched_map[user_id] = set()
        user_watched_map[user_id].add(episode_id)

    # Now process each user using the pre-collected data
    users_progress = []
    for user in users_with_progress:
        watched_episodes_ids = user_watched_map.get(user.id, set())
        # Continue with your existing processing...
        # Get the user's season and subgroup reviews in a single query with all needed relationships
        user_reviews = Review.objects.filter(
            user=user,
            content_type=tv_show_content_type,
            object_id=tv_show_db.id
        ).select_related('season', 'episode_subgroup', 'episode_subgroup__parent_group')
        
        # Get all season reviews
        season_reviews = []
        for review in user_reviews.filter(season__isnull=False):
            season_reviews.append({
                'season_number': review.season.season_number,
                'rating': review.rating,
                'id': review.season_id
            })
        
        # Sort season reviews by season_number
        season_reviews.sort(key=lambda x: x['season_number'])
        
        # Convert season reviews to an easy-to-access dictionary format
        season_ratings = {}
        for review in season_reviews:
            season_ratings[review['season_number']] = review['rating']
        
        # Get all subgroup reviews efficiently using a single query
        subgroup_reviews_data = user_reviews.filter(episode_subgroup__isnull=False).values(
            'id',
            'rating',
            'episode_subgroup_id',
            'episode_subgroup__name',
            'episode_subgroup__order',
            'episode_subgroup__parent_group__order'
        )

        subgroup_orders = {}
        subgroup_reviews = []

        for data in subgroup_reviews_data:
            subgroup_id = data['episode_subgroup_id']
            subgroup_orders[subgroup_id] = (
                data['episode_subgroup__parent_group__order'], 
                data['episode_subgroup__order']
            )
            
            subgroup_reviews.append({
                'name': data['episode_subgroup__name'],
                'rating': data['rating'],
                'id': subgroup_id
            })

        # Sort by the predefined order in the database
        subgroup_reviews.sort(key=lambda x: subgroup_orders.get(x['id'], (999, 999)))

        # Calculate user's average rating from season and subgroup reviews
        all_ratings = [review['rating'] for review in season_reviews] + [review['rating'] for review in subgroup_reviews]
        average_rating = sum(all_ratings) / len(all_ratings) if all_ratings else None

        # Find all episodes that are part of reviewed subgroups
        reviewed_episodes = set()
        
        # Get all subgroup IDs from the reviews
        reviewed_subgroup_ids = [review['id'] for review in subgroup_reviews]
        
        # Get all episodes belonging to reviewed subgroups in a single efficient query
        if reviewed_subgroup_ids:
            # This single query replaces multiple individual queries
            episode_subgroup_mapping = {}
            episode_subgroup_entries = EpisodeSubGroup.objects.filter(
                id__in=reviewed_subgroup_ids
            ).prefetch_related('episodes').values_list('id', 'episodes__id')
            
            # Organize the episodes by subgroup id
            for subgroup_id, episode_id in episode_subgroup_entries:
                if episode_id is not None:  # Ensure episode_id is not None
                    if subgroup_id not in episode_subgroup_mapping:
                        episode_subgroup_mapping[subgroup_id] = []
                    episode_subgroup_mapping[subgroup_id].append(episode_id)
                    reviewed_episodes.add(episode_id)
                    
            # Create a mapping between episodes and their subgroup reviews
            episode_review_map = {}
            for review in subgroup_reviews:
                subgroup_id = review['id']
                if subgroup_id in episode_subgroup_mapping:
                    for episode_id in episode_subgroup_mapping[subgroup_id]:
                        episode_review_map[episode_id] = {
                            'rating': review['rating'],
                            'name': review['name']
                        }
        else:
            # No subgroup reviews, so empty mappings
            episode_review_map = {}

        user_progress = {
            'username': user.username,
            'is_current_user': user.id == request.user.id,
            'watched_count': len(watched_episodes_ids),
            'total_episodes': total_regular_episodes,
            'progress_percentage': (len(watched_episodes_ids) / total_regular_episodes * 100) if total_regular_episodes > 0 else 0,
            'watched_episodes': watched_episodes_ids,
            'season_reviews': season_reviews,
            'season_ratings': season_ratings,
            'subgroup_reviews': subgroup_reviews,
            'reviewed_episodes': reviewed_episodes,
            'episode_review_map': episode_review_map,
            'average_rating': average_rating  # Add average rating for this user
        }
        users_progress.append(user_progress)
    
    # Calculate combined average rating from all users
    all_user_ratings = [up['average_rating'] for up in users_progress if up['average_rating'] is not None]
    combined_average_rating = sum(all_user_ratings) / len(all_user_ratings) if all_user_ratings else None

    # Get the most recent review for each season in one query
    latest_reviews = Review.objects.filter(
        season=OuterRef('pk')
    ).order_by('-date_added')

    # Annotate seasons with review data
    seasons = tv_show_db.seasons.all().annotate(
        review_id=Subquery(latest_reviews.values('id')[:1]),
        review_rating=Subquery(latest_reviews.values('rating')[:1]),
        review_text=Subquery(latest_reviews.values('review_text')[:1])
    )

    # Add to context (replace tv_show.seasons with this)
    context = {
        'tv_show': tv_show_db,
        'user_watchlist': user_watchlist,
        'subgroups_data': subgroups_data,
        'episodes_by_season': episodes_by_season,
        'total_regular_episodes': total_regular_episodes,
        'users_progress': users_progress,
        'combined_average_rating': combined_average_rating,  # Add combined average to context
        'seasons_with_reviews': seasons
    }
    
    return render(request, 'tv_show_page.html', context)


@login_required
def subgroup_episodes(request, subgroup_id):
    """Get all episodes for a specific subgroup."""
    subgroup = get_object_or_404(EpisodeSubGroup, id=subgroup_id)
    
    # Get all watched episodes for this user
    watched_episodes = WatchedEpisode.objects.filter(
        user=request.user
    ).values_list('episode_id', flat=True)
    
    episodes_data = []
    for episode in subgroup.episodes.all().order_by('air_date'):
        # Get all users who watched this episode
        watched_users = WatchedEpisode.objects.filter(
            episode=episode
        ).select_related('user')
        
        episodes_data.append({
            'id': episode.id,
            'title': episode.title,
            'season_number': episode.season.season_number,
            'episode_number': episode.episode_number,
            'still': episode.still,
            'overview': episode.overview,
            'rating': episode.rating,
            'runtime': episode.runtime,
            'air_date': episode.air_date,
            'is_watched': episode.id in watched_episodes,
            'watched_by': [watched.user.username for watched in watched_users],
        })
    
    return JsonResponse({
        'episodes': episodes_data,
        'subgroup_name': subgroup.name
    })


@login_required
@csrf_protect
def subgroup_review(request, subgroup_id):
    """
    Endpoint to create or update a review for a TV show episode subgroup.
    """
    # Get the subgroup or return 404
    subgroup = get_object_or_404(EpisodeSubGroup, id=subgroup_id)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST method is allowed'}, status=405)
    
    try:
        # Parse JSON data from request body
        data = json.loads(request.body)
        rating = data.get('rating')
        watch_date = data.get('watch_date')
        review_text = data.get('review_text')
        
        # Validate required fields
        if not rating or not watch_date:
            return JsonResponse({'success': False, 'error': 'Rating and watch date are required'}, status=400)
        
        # Convert rating to float
        try:
            rating = float(rating)
            if not (1 <= rating <= 10):
                return JsonResponse({'success': False, 'error': 'Rating must be between 1 and 10'}, status=400)
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid rating value'}, status=400)
        
        # Check if user has completed watching all episodes in the subgroup
        if not subgroup.user_has_completed(request.user):
            return JsonResponse({
                'success': False, 
                'error': 'You must watch all episodes in this group before reviewing'
            }, status=400)
        
        # Get the TV show related to this subgroup through the parent group
        tv_show = subgroup.parent_group.show
        
        # Get TV show content type
        tv_show_content_type = ContentType.objects.get_for_model(TVShow)
        
        # Check if review exists already and update, or create new
        review, created = Review.objects.update_or_create(
            user=request.user,
            content_type=tv_show_content_type,
            object_id=tv_show.id,
            episode_subgroup=subgroup,
            defaults={
                'rating': rating,
                'review_text': review_text
            }
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Review added successfully',
            'review_id': review.id
        })
        
    except AttributeError as e:
        # Handle the case where the model relationships aren't what we expected
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        # Log the exception if needed
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@csrf_protect
def season_review(request, season_id):
    """
    Endpoint to create or update a review for a TV show season.
    """
    # Get the season or return 404
    season = get_object_or_404(Season, id=season_id)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST method is allowed'}, status=405)
    
    try:
        # Parse JSON data from request body
        data = json.loads(request.body)
        rating = data.get('rating')
        watch_date = data.get('watch_date')
        review_text = data.get('review_text')
        
        # Validate required fields
        if not rating or not watch_date:
            return JsonResponse({'success': False, 'error': 'Rating and watch date are required'}, status=400)
        
        # Convert rating to float
        try:
            rating = float(rating)
            if not (1 <= rating <= 10):
                return JsonResponse({'success': False, 'error': 'Rating must be between 1 and 10'}, status=400)
        except ValueError:
            return JsonResponse({'success': False, 'error': 'Invalid rating value'}, status=400)
        
        # Check if user has completed watching all episodes in the season
        if not season.user_has_completed(request.user):
            return JsonResponse({
                'success': False, 
                'error': 'You must watch all episodes in this season before reviewing'
            }, status=400)
        
        # Get the TV show related to this season
        tv_show = season.show
        
        # Get TV show content type
        tv_show_content_type = ContentType.objects.get_for_model(TVShow)
        
        # Check if review exists already and update, or create new
        review, created = Review.objects.update_or_create(
            user=request.user,
            content_type=tv_show_content_type,
            object_id=tv_show.id,
            season=season,
            defaults={
                'rating': rating,
                'review_text': review_text
            }
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Review added successfully',
            'review_id': review.id
        })
        
    except AttributeError as e:
        # Handle the case where the model relationships aren't what we expected
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        # Log the exception if needed
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


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
        
        # Calculate subgroup progress for any subgroups this episode belongs to
        subgroup_progress = {}
        subgroups = episode.sub_groups.all()
        for subgroup in subgroups:
            total_sg_episodes = subgroup.episodes.count()
            if total_sg_episodes > 0:
                watched_sg_episodes = WatchedEpisode.objects.filter(
                    user=request.user,
                    episode__in=subgroup.episodes.all()
                ).count()
                
                percentage = (watched_sg_episodes / total_sg_episodes) * 100
                
                subgroup_progress[subgroup.id] = {
                    'total': total_sg_episodes,
                    'watched': watched_sg_episodes,
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
            'marked_episodes': marked_episodes,
            'subgroup_progress': subgroup_progress
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