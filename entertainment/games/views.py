from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Avg
from django.contrib.auth import get_user_model

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

from .models import Game, GameGenre, Platform, GameDeveloper, GamePublisher, GameCollection
from custom_auth.models import Review, Watchlist, Keyword
from api.services.games import GamesService
from api.services.steamgriddb import SteamGridDBService

User = get_user_model()

# Create your views here.

@login_required
def game_list(request):
    """Display list of all games"""
    games = Game.objects.all().order_by('-date_added')
    
    context = {
        'games': games,
    }
    return render(request, 'games/game_list.html', context)


@login_required
def game_detail(request, pk):
    """Display detailed information about a specific game"""
    game = get_object_or_404(Game.objects.prefetch_related('genres', 'platforms'), pk=pk)
    
    # Get content type for Game model
    content_type = ContentType.objects.get_for_model(Game)
    
    # Get user's review if it exists
    user_review = None
    if request.user.is_authenticated:
        try:
            user_review = Review.objects.get(
                user=request.user,
                content_type=content_type,
                object_id=game.id
            )
        except Review.DoesNotExist:
            pass
    
    # Check if in watchlist
    in_watchlist = False
    if request.user.is_authenticated:
        in_watchlist = Watchlist.objects.filter(
            user=request.user,
            content_type=content_type,
            object_id=game.id
        ).exists()
    
    # Get user average rating and count
    reviews = Review.objects.filter(content_type=content_type, object_id=game.id)
    user_avg_rating = None
    user_rating_count = 0
    if reviews.exists():
        avg = reviews.aggregate(Avg('rating'))['rating__avg']
        user_avg_rating = round(avg, 1) if avg else None
        user_rating_count = reviews.count()
    
    # Get users who have this game in their watchlist (Fellow Game Collectors)
    watchlist_users = User.objects.filter(
        watchlist_items__content_type=content_type,
        watchlist_items__object_id=game.id
    ).exclude(id=request.user.id)[:10]  # Limit to 10 users
    
    context = {
        'game': game,
        'user_review': user_review,
        'in_watchlist': in_watchlist,
        'user_avg_rating': user_avg_rating,
        'user_rating_count': user_rating_count,
        'watchlist_users': watchlist_users,
    }
    return render(request, 'games/game_detail.html', context)


class RAWGSearchView(APIView):
    """Search for games using the RAWG API"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search Games",
        description="Search for games using the RAWG API by providing a query string.",
        parameters=[
            OpenApiParameter(name="query", description="Search term for games", required=True, type=str),
            OpenApiParameter(name="page", description="Page number for pagination (default is 1)", required=False, type=int),
            OpenApiParameter(name="page_size", description="Number of results per page (default is 20, max 40)", required=False, type=int),
        ]
    )
    def get(self, request):
        query = request.GET.get('query')
        page = int(request.GET.get('page', 1))
        page_size = int(request.GET.get('page_size', 20))

        if not query:
            return Response({"error": "Query parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            games_service = GamesService()
            results = games_service.search_games(query=query, page=page, page_size=page_size)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Transform results to match expected format
        games = results.get('results', [])
        
        # Add a field to show if game is already in database and in user's watchlist
        existing_game_ids = Game.objects.filter(
            rawg_id__in=[game['id'] for game in games]
        ).values_list('rawg_id', flat=True)
        
        # Get the user's watchlist game IDs
        user_watchlist = Watchlist.objects.filter(
            user=request.user,
            content_type=ContentType.objects.get_for_model(Game)
        ).values_list('object_id', flat=True)
        
        user_watchlist_rawg_ids = Game.objects.filter(
            id__in=user_watchlist
        ).values_list('rawg_id', flat=True)
        
        for game in games:
            game['in_database'] = game['id'] in existing_game_ids
            game['in_watchlist'] = game['id'] in user_watchlist_rawg_ids
        
        response_data = {
            'count': results.get('count', 0),
            'next': results.get('next'),
            'previous': results.get('previous'),
            'results': games,
            'page': page,
            'page_size': page_size,
            'total_pages': (results.get('count', 0) + page_size - 1) // page_size if results.get('count', 0) > 0 else 0,
            'total_results': results.get('count', 0),
        }
        
        return Response(response_data, status=status.HTTP_200_OK)


class GamePosterView(APIView):
    """Get poster/cover art for a game from SteamGridDB"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Game Poster",
        description="Get poster/cover art for a game using SteamGridDB API.",
        parameters=[
            OpenApiParameter(name="name", description="Game name to search for", required=True, type=str),
        ]
    )
    def get(self, request):
        game_name = request.GET.get('name')
        
        if not game_name:
            return Response({"error": "name parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            steamgriddb = SteamGridDBService()
            if not steamgriddb.api_key:
                return Response({"poster_url": None, "error": "SteamGridDB API key not configured"}, status=status.HTTP_200_OK)
            
            poster_url = steamgriddb.get_poster_url(game_name)
            return Response({"poster_url": poster_url}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"poster_url": None, "error": str(e)}, status=status.HTTP_200_OK)


class GameScreenshotsView(APIView):
    """Get screenshots for a specific game from RAWG API"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Game Screenshots",
        description="Get screenshots for a specific game using the RAWG API.",
        parameters=[
            OpenApiParameter(name="game_id", description="RAWG game ID", required=True, type=int),
        ]
    )
    def get(self, request):
        game_id = request.GET.get('game_id')
        
        if not game_id:
            return Response({"error": "game_id parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            games_service = GamesService()
            screenshots = games_service.get_game_screenshots(game_id=game_id)
            return Response(screenshots, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameBackdropsView(APIView):
    """Get backdrop images for a specific game from RAWG API"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Game Backdrops",
        description="Get backdrop/background images for a specific game using the RAWG API.",
        parameters=[
            OpenApiParameter(name="game_id", description="RAWG game ID", required=True, type=int),
        ]
    )
    def get(self, request):
        game_id = request.GET.get('game_id')
        
        if not game_id:
            return Response({"error": "game_id parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            games_service = GamesService()
            game_details = games_service.get_game_details(game_id=game_id)
            
            backdrops = []
            # Add main background image
            if game_details.get('background_image'):
                backdrops.append({
                    'image': game_details['background_image'],
                    'type': 'background'
                })
            # Add additional background image
            if game_details.get('background_image_additional'):
                backdrops.append({
                    'image': game_details['background_image_additional'],
                    'type': 'background_additional'
                })
            
            return Response({"results": backdrops}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameCoversView(APIView):
    """Get all available covers for a game from SteamGridDB for user selection"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Game Covers",
        description="Get all available covers for a game from SteamGridDB for user selection.",
        parameters=[
            OpenApiParameter(name="name", description="Game name to search for", required=True, type=str),
        ]
    )
    def get(self, request):
        game_name = request.GET.get('name')
        
        if not game_name:
            return Response({"error": "name parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            steamgriddb = SteamGridDBService()
            if not steamgriddb.api_key:
                return Response({"results": [], "error": "SteamGridDB API key not configured"}, status=status.HTTP_200_OK)
            
            covers = steamgriddb.get_all_covers(game_name, limit=20)
            return Response({"results": covers}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"results": [], "error": str(e)}, status=status.HTTP_200_OK)


class GameViewSet(viewsets.ViewSet):
    """ViewSet for managing games"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]
    queryset = Game.objects.all()

    @extend_schema(
        summary="Get Games",
        description="Get a list of all games in the database."
    )
    def list(self, request):
        queryset = Game.objects.all().order_by('-date_added')
        games_data = [{
            'id': game.id,
            'title': game.title,
            'rawg_id': game.rawg_id,
            'release_date': game.release_date,
            'rating': game.rating,
            'poster': game.poster,
        } for game in queryset]
        return Response(games_data)

    @extend_schema(
        summary="Create Game",
        description="Create a new game from RAWG data."
    )
    def create(self, request):
        rawg_id = request.data.get('id')
        if not rawg_id:
            return Response({"error": "Game ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Check if game already exists
        existing_game = Game.objects.filter(rawg_id=rawg_id).first()
        if existing_game:
            # Add to watchlist if requested
            add_to_watchlist = request.data.get('add_to_watchlist', True)
            if add_to_watchlist:
                content_type = ContentType.objects.get_for_model(Game)
                Watchlist.objects.get_or_create(
                    user=request.user,
                    content_type=content_type,
                    object_id=existing_game.id
                )
            return Response({
                "message": "Game already exists",
                "game_id": existing_game.id,
                "rawg_id": existing_game.rawg_id
            }, status=status.HTTP_200_OK)
        
        # Fetch game details from RAWG
        try:
            games_service = GamesService()
            game_data = games_service.get_game_details(rawg_id)
        except Exception as e:
            return Response({"error": f"Failed to fetch game data: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Parse release date
        release_date = None
        if game_data.get('released'):
            from datetime import datetime
            try:
                release_date = datetime.strptime(game_data['released'], '%Y-%m-%d').date()
            except ValueError:
                pass
        
        # Check if poster URL is provided in the request
        poster_url = request.data.get('poster')
        
        game_name = game_data.get('name', '')
        
        # If no poster provided, try to get a proper vertical poster from SteamGridDB
        if not poster_url:
            try:
                steamgriddb = SteamGridDBService()
                if steamgriddb.api_key:
                    poster_url = steamgriddb.get_poster_url(game_name)
            except Exception as e:
                print(f"SteamGridDB poster fetch failed: {e}")
        
        # Fallback to RAWG background_image if no SteamGridDB poster
        if not poster_url:
            poster_url = game_data.get('background_image')
        
        # Create the game
        game = Game.objects.create(
            title=game_name or 'Unknown',
            rawg_id=game_data.get('id'),
            rawg_slug=game_data.get('slug'),
            description=game_data.get('description_raw', game_data.get('description', '')),
            release_date=release_date,
            rating=game_data.get('rating'),
            metacritic=game_data.get('metacritic'),
            poster=poster_url,
            backdrop=request.data.get('backdrop') or game_data.get('background_image_additional') or game_data.get('background_image'),
            added_by=request.user,
        )
        
        # Process Genres
        if game_data.get('genres'):
            for genre_data in game_data['genres']:
                genre, _ = GameGenre.objects.get_or_create(
                    rawg_id=genre_data.get('id'),
                    defaults={
                        'name': genre_data.get('name'),
                        'slug': genre_data.get('slug')
                    }
                )
                game.genres.add(genre)
        
        # Process Platforms
        if game_data.get('platforms'):
            for platform_wrapper in game_data['platforms']:
                platform_data = platform_wrapper.get('platform')
                if platform_data:
                    platform, _ = Platform.objects.get_or_create(
                        rawg_id=platform_data.get('id'),
                        defaults={
                            'name': platform_data.get('name'),
                            'slug': platform_data.get('slug')
                        }
                    )
                    game.platforms.add(platform)
        
        # Process Keywords (Tags in RAWG)
        if game_data.get('tags'):
            for tag_data in game_data['tags']:
                tag_name = tag_data.get('name')
                tag_id = tag_data.get('id')
                
                if not tag_name:
                    continue
                
                # Normalize to lowercase to match TMDB/existing keywords
                tag_name = tag_name.lower()
                    
                # Try to find by RAWG ID first
                keyword = Keyword.objects.filter(rawg_id=tag_id).first()
                
                if not keyword:
                    # Try to find by name (case insensitive)
                    keyword = Keyword.objects.filter(name__iexact=tag_name).first()
                    if keyword:
                        # Update RAWG ID if found by name and missing
                        if not keyword.rawg_id:
                            keyword.rawg_id = tag_id
                            keyword.save(update_fields=['rawg_id'])
                    else:
                        # Create new if not found
                        keyword = Keyword.objects.create(
                            name=tag_name,
                            rawg_id=tag_id
                        )
                
                game.keywords.add(keyword)
        
        # Process Developers
        if game_data.get('developers'):
            for dev_data in game_data['developers']:
                developer, _ = GameDeveloper.objects.get_or_create(
                    rawg_id=dev_data.get('id'),
                    defaults={
                        'name': dev_data.get('name'),
                        'slug': dev_data.get('slug'),
                        'image_background': dev_data.get('image_background')
                    }
                )
                game.developers.add(developer)

        # Process Publishers
        if game_data.get('publishers'):
            for pub_data in game_data['publishers']:
                publisher, _ = GamePublisher.objects.get_or_create(
                    rawg_id=pub_data.get('id'),
                    defaults={
                        'name': pub_data.get('name'),
                        'slug': pub_data.get('slug'),
                        'image_background': pub_data.get('image_background')
                    }
                )
                game.publishers.add(publisher)

        # Process Collection (Series)
        # RAWG doesn't have a direct "Collection" object, but has "game-series" endpoint.
        # We use a heuristic: fetch the series, find the earliest game, and use it as the collection anchor.
        if game_data.get('game_series_count', 0) > 0:
            try:
                series_response = games_service.get_game_series(game_data['id'])
                series_games = series_response.get('results', [])
                
                if series_games:
                    # Filter out games with no release date and sort by release date
                    valid_games = [g for g in series_games if g.get('released')]
                    if valid_games:
                        valid_games.sort(key=lambda x: x['released'])
                        first_game = valid_games[0]
                        
                        # Use the first game's ID as the collection ID (proxy)
                        # and its name + " Collection" as the name
                        collection_name = f"{first_game['name']} Collection"
                        
                        collection, _ = GameCollection.objects.get_or_create(
                            rawg_id=first_game['id'],
                            defaults={
                                'name': collection_name,
                                'slug': first_game.get('slug'),
                                'image_background': first_game.get('background_image')
                            }
                        )
                        
                        game.game_collection = collection
                        game.save(update_fields=['game_collection'])
            except Exception as e:
                print(f"Failed to process game collection: {e}")

        # Add to watchlist if requested
        add_to_watchlist = request.data.get('add_to_watchlist', True)
        if add_to_watchlist:
            content_type = ContentType.objects.get_for_model(Game)
            Watchlist.objects.get_or_create(
                user=request.user,
                content_type=content_type,
                object_id=game.id
            )
        
        return Response({
            "message": "Game created successfully",
            "game_id": game.id,
            "rawg_id": game.rawg_id
        }, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Get Game",
        description="Get details for a specific game."
    )
    def retrieve(self, request, pk=None):
        game = get_object_or_404(Game, pk=pk)
        game_data = {
            'id': game.id,
            'title': game.title,
            'rawg_id': game.rawg_id,
            'rawg_slug': game.rawg_slug,
            'description': game.description,
            'release_date': game.release_date,
            'rating': game.rating,
            'metacritic': game.metacritic,
            'poster': game.poster,
            'backdrop': game.backdrop,
        }
        return Response(game_data)


class GameReviewView(APIView):
    """API view for game reviews - GET, POST, PUT, DELETE"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Game Reviews",
        description="Get all reviews for a specific game.",
        parameters=[
            OpenApiParameter(name="game_id", description="ID of the game to retrieve reviews for", required=True, type=int),
        ],
        responses={200: OpenApiExample("Game Reviews", value=[
            {"id": 1, "user": "username", "rating": 8.5, "review_text": "Great game!", "date_added": "2025-04-04T12:00:00Z"}
        ])}
    )
    def get(self, request):
        game_id = request.GET.get('game_id')
        if not game_id:
            return Response({"error": "Game ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            game = Game.objects.get(id=game_id)
        except Game.DoesNotExist:
            return Response({"error": "Game not found"}, status=status.HTTP_404_NOT_FOUND)
            
        content_type = ContentType.objects.get_for_model(Game)
        reviews = Review.objects.filter(
            content_type=content_type,
            object_id=game_id
        ).select_related('user')
        
        result = []
        for review in reviews:
            result.append({
                "id": review.id,
                "user": review.user.username,
                "rating": review.rating,
                "review_text": review.review_text,
                "date_added": review.date_added
            })
            
        return Response(result, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Create Game Review",
        description="Create a new review for a game.",
        request=OpenApiExample("Review Request", value={
            "game_id": 1,
            "rating": 8.5,
            "review_text": "This was an excellent game!"
        }),
        responses={201: OpenApiExample("Review Created", value={"message": "Review added successfully"})}
    )
    def post(self, request):
        game_id = request.data.get('game_id')
        rating = request.data.get('rating')
        review_text = request.data.get('review_text', '')
        date_added = request.data.get('date_added', timezone.now())
        
        if not game_id:
            return Response({"error": "Game ID is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        if not rating:
            return Response({"error": "Rating is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            rating = float(rating)
            if rating < 0 or rating > 10:
                return Response({"error": "Rating must be between 0 and 10"}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, TypeError):
            return Response({"error": "Rating must be a valid number"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            game = Game.objects.get(id=game_id)
        except Game.DoesNotExist:
            return Response({"error": "Game not found"}, status=status.HTTP_404_NOT_FOUND)
            
        content_type = ContentType.objects.get_for_model(Game)
        
        # Check if user already has a review for this game
        existing_review = Review.objects.filter(
            user=request.user,
            content_type=content_type,
            object_id=game_id
        ).first()
        
        if existing_review:
            return Response({"error": "You have already reviewed this game"}, status=status.HTTP_400_BAD_REQUEST)
        
        today_date = timezone.now().date()

        if isinstance(date_added, str):
            date_added = timezone.datetime.strptime(date_added, '%Y-%m-%d').date()
        else:
            date_added = today_date

        if date_added == today_date:
            date_added = timezone.now()
        elif date_added < today_date:
            date_added = timezone.make_aware(timezone.datetime.combine(date_added, timezone.datetime.min.time()))
        else:
            return Response({"error": "Date added cannot be in the future"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Create the review
        review = Review.objects.create(
            user=request.user,
            content_type=content_type,
            object_id=game_id,
            rating=rating,
            review_text=review_text
        )

        review.date_added = date_added
        review.save()
        
        return Response({"message": "Review added successfully", "review_id": review.id}, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Update Game Review",
        description="Update an existing review.",
        request=OpenApiExample("Update Review Request", value={
            "review_id": 1,
            "rating": 9.0,
            "review_text": "Updated review text"
        }),
        responses={200: OpenApiExample("Review Updated", value={"message": "Review updated successfully"})}
    )
    def put(self, request):
        review_id = request.data.get('review_id')
        rating = request.data.get('rating')
        review_text = request.data.get('review_text', '')
        date_added = request.data.get('date_added')
        
        if not review_id:
            return Response({"error": "Review ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            review = Review.objects.get(id=review_id, user=request.user)
        except Review.DoesNotExist:
            return Response({"error": "Review not found or you don't have permission to edit it"}, status=status.HTTP_404_NOT_FOUND)
        
        if rating:
            try:
                rating = float(rating)
                if rating < 0 or rating > 10:
                    return Response({"error": "Rating must be between 0 and 10"}, status=status.HTTP_400_BAD_REQUEST)
                review.rating = rating
            except (ValueError, TypeError):
                return Response({"error": "Rating must be a valid number"}, status=status.HTTP_400_BAD_REQUEST)
        
        review.review_text = review_text
        
        if date_added:
            today_date = timezone.now().date()
            if isinstance(date_added, str):
                date_added = timezone.datetime.strptime(date_added, '%Y-%m-%d').date()
            
            if date_added == today_date:
                review.date_added = timezone.now()
            elif date_added < today_date:
                review.date_added = timezone.make_aware(timezone.datetime.combine(date_added, timezone.datetime.min.time()))
        
        review.save()
        
        return Response({"message": "Review updated successfully"}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Delete Game Review",
        description="Delete an existing review.",
        parameters=[
            OpenApiParameter(name="review_id", description="ID of the review to delete", required=True, type=int),
        ],
        responses={204: OpenApiExample("Review Deleted", value={"message": "Review deleted successfully"})}
    )
    def delete(self, request):
        review_id = request.data.get('review_id')
        
        if not review_id:
            return Response({"error": "Review ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            review = Review.objects.get(id=review_id, user=request.user)
        except Review.DoesNotExist:
            return Response({"error": "Review not found or you don't have permission to delete it"}, status=status.HTTP_404_NOT_FOUND)
        
        review.delete()
        
        return Response({"message": "Review deleted successfully"}, status=status.HTTP_204_NO_CONTENT)


class GameWatchlistView(APIView):
    """API view for game watchlist - POST (add) and DELETE (remove)"""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Add Game to Watchlist",
        description="Add a game to the user's watchlist.",
        request=OpenApiExample("Add to Watchlist", value={"id": 1}),
        responses={201: OpenApiExample("Added", value={"message": "Game added to watchlist"})}
    )
    def post(self, request):
        game_id = request.data.get('id')
        
        if not game_id:
            return Response({"error": "Game ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            game = Game.objects.get(id=game_id)
        except Game.DoesNotExist:
            return Response({"error": "Game not found"}, status=status.HTTP_404_NOT_FOUND)
        
        content_type = ContentType.objects.get_for_model(Game)
        
        # Check if already in watchlist
        if Watchlist.objects.filter(user=request.user, content_type=content_type, object_id=game_id).exists():
            return Response({"error": "Game already in watchlist"}, status=status.HTTP_400_BAD_REQUEST)
        
        Watchlist.objects.create(
            user=request.user,
            content_type=content_type,
            object_id=game_id
        )
        
        return Response({"message": "Game added to watchlist"}, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Remove Game from Watchlist",
        description="Remove a game from the user's watchlist.",
        request=OpenApiExample("Remove from Watchlist", value={"id": 1}),
        responses={204: OpenApiExample("Removed", value={"message": "Game removed from watchlist"})}
    )
    def delete(self, request):
        game_id = request.data.get('id')
        
        if not game_id:
            return Response({"error": "Game ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            game = Game.objects.get(id=game_id)
        except Game.DoesNotExist:
            return Response({"error": "Game not found"}, status=status.HTTP_404_NOT_FOUND)
        
        content_type = ContentType.objects.get_for_model(Game)
        
        try:
            watchlist_item = Watchlist.objects.get(
                user=request.user,
                content_type=content_type,
                object_id=game_id
            )
            watchlist_item.delete()
            return Response({"message": "Game removed from watchlist"}, status=status.HTTP_204_NO_CONTENT)
        except Watchlist.DoesNotExist:
            return Response({"error": "Game not in watchlist"}, status=status.HTTP_404_NOT_FOUND)


@login_required
def developer_detail(request, pk):
    """Display detailed information about a specific game developer"""
    developer = get_object_or_404(GameDeveloper, pk=pk)
    games = developer.developed_games.all().order_by('-release_date')
    
    # Attach user ratings
    content_type = ContentType.objects.get_for_model(Game)
    user_reviews = Review.objects.filter(
        user=request.user,
        content_type=content_type,
        object_id__in=games.values_list('id', flat=True)
    )
    review_map = {review.object_id: review.rating for review in user_reviews}
    
    for game in games:
        game.user_rating = review_map.get(game.id)
        
    return render(request, 'games/developer_detail.html', {'developer': developer, 'games': games})


@login_required
def publisher_detail(request, pk):
    """Display detailed information about a specific game publisher"""
    publisher = get_object_or_404(GamePublisher, pk=pk)
    games = publisher.published_games.all().order_by('-release_date')
    
    # Attach user ratings
    content_type = ContentType.objects.get_for_model(Game)
    user_reviews = Review.objects.filter(
        user=request.user,
        content_type=content_type,
        object_id__in=games.values_list('id', flat=True)
    )
    review_map = {review.object_id: review.rating for review in user_reviews}
    
    for game in games:
        game.user_rating = review_map.get(game.id)
        
    return render(request, 'games/publisher_detail.html', {'publisher': publisher, 'games': games})


@login_required
def collection_detail(request, pk):
    """Display detailed information about a specific game collection"""
    collection = get_object_or_404(GameCollection, pk=pk)
    games = collection.games.all().order_by('release_date')
    
    # Attach user ratings
    content_type = ContentType.objects.get_for_model(Game)
    user_reviews = Review.objects.filter(
        user=request.user,
        content_type=content_type,
        object_id__in=games.values_list('id', flat=True)
    )
    review_map = {review.object_id: review.rating for review in user_reviews}
    
    for game in games:
        game.user_rating = review_map.get(game.id)
        
    return render(request, 'games/collection_detail.html', {'collection': collection, 'games': games})
