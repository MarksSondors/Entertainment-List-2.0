from django.shortcuts import render, get_object_or_404, redirect
from api.services.movies import MoviesService
from django.http import JsonResponse, Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

import json
import random

from .serializers import MovieSerializer
from .parsers import create_movie
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.decorators import login_required
from .models import *
from django.http import JsonResponse

from django.db.models import Count, Q, Min
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services.recommendation import MovieRecommender
from .tasks import create_movie_async
from datetime import timedelta
from django.contrib import messages
from custom_auth.models import CustomUser, Review

# Create your views here.


@login_required
def movie_page(request, movie_id):
    if request.user.is_authenticated:
        try:
            user_settings = request.user.settings
        except:
            # Create default settings if they don't exist
            from custom_auth.models import UserSettings
            user_settings = UserSettings.objects.create(user=request.user)

    try:
        movie_db = Movie.objects.prefetch_related(
            'genres', 
            'countries', 
            'keywords'
        ).get(tmdb_id=movie_id)
    except Movie.DoesNotExist:
        task_id = create_movie_async(
            movie_id=movie_id,
            user_id=request.user.id,
            add_to_watchlist=False
        )
        # Replace the wait_for_task import and usage with:
        from django_q.models import Task
        from time import sleep
        
        # Wait for the task to complete
        max_attempts = 30
        for _ in range(max_attempts):
            try:
                task = Task.objects.get(id=task_id)
                if task.success is not None:  # Task has completed (success or failure)
                    break
            except Task.DoesNotExist:
                pass
            sleep(1)
            
        # Check task result
        try:
            task = Task.objects.get(id=task_id)
            if task.success:
                movie_db = Movie.objects.get(tmdb_id=movie_id)
            else:
                return JsonResponse({"error": "Movie not found"}, status=status.HTTP_404_NOT_FOUND)
        except Task.DoesNotExist:
            return JsonResponse({"error": "Task processing failed"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    # get user information if he has written a review is the movie in his watchlist
    user_watchlist = Watchlist.objects.filter(
        user=request.user,
        content_type=ContentType.objects.get_for_model(Movie),
        object_id=movie_db.id
    ).exists()
    context = {
        'movie': movie_db,
        'user_watchlist': user_watchlist,
        'user_settings': user_settings
    }
    return render(request, 'movie_page.html', context)


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
        description="Create a new movie in the background.",
        request=MovieSerializer,
        responses={
            201: MovieSerializer,
            202: OpenApiExample("Task Queued", value={"message": "Movie creation has been queued", "task_id": "task-uuid-example"})
        },
    )
    def create(self, request):
        movie_id = request.data.get('id')
        if not movie_id:
            return Response({"error": "Movie ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        add_to_watchlist = request.data.get('add_to_watchlist', False)

        if Movie.objects.filter(tmdb_id=movie_id).exists():
            if add_to_watchlist:
                # Add to watchlist if it already exists
                content_type = ContentType.objects.get_for_model(Movie)
                watchlist_item, created = Watchlist.objects.get_or_create(
                    user=request.user,
                    content_type=content_type,
                    object_id=Movie.objects.filter(tmdb_id=movie_id).first().id,
                )
                if created:
                    return Response({"message": "Movie added to watchlist"}, status=status.HTTP_201_CREATED)
                else:
                    return Response({"message": "Movie already in watchlist"}, status=status.HTTP_200_OK)
            return Response({"error": "Movie already exists"}, status=status.HTTP_400_BAD_REQUEST)
        
        movie_poster = request.data.get('poster')
        movie_backdrop = request.data.get('backdrop')

        is_anime = request.data.get('is_anime', False)
        # Add the URL base if poster or backdrop is provided
        if movie_poster:
            movie_poster = f"https://image.tmdb.org/t/p/original{movie_poster}"
        if movie_backdrop:
            movie_backdrop = f"https://image.tmdb.org/t/p/original{movie_backdrop}"

        # Queue the movie creation as a background task
        task_id = create_movie_async(
            movie_id=movie_id, 
            movie_poster=movie_poster, 
            movie_backdrop=movie_backdrop, 
            is_anime=is_anime, 
            add_to_watchlist=add_to_watchlist, 
            user_id=request.user.id
        )
        
        # Return a response immediately with the task ID
        return Response({
            "message": "Movie creation has been queued",
            "task_id": str(task_id)
        }, status=status.HTTP_202_ACCEPTED)
    
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
        popular_movies = sorted(popular_movies['results'], key=lambda x: x['popularity'], reverse=True)[:10]
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

class WatchlistMovie(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Add Movie to Watchlist",
        description="Add a movie to the user's watchlist.",
        request=MovieSerializer,
        responses={201: OpenApiExample("Watchlist Movie", value={"message": "Movie added to watchlist"})}
    )
    def post(self, request):
        movie_id = request.data.get('id')
        if not movie_id:
            return Response({"error": "Movie ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Check if the user has already reviewed this movie
        content_type = ContentType.objects.get_for_model(Movie)
        existing_review = Review.objects.filter(
            user=request.user,
            content_type=content_type,
            object_id=movie_id
        ).exists()
        
        if existing_review:
            return Response({"error": "Cannot add to watchlist: movie has already been reviewed"}, 
                           status=status.HTTP_400_BAD_REQUEST)
        
        # If no review exists, add to watchlist
        watchlist_item, created = Watchlist.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=movie_id,
        )
        
        if created:
            return Response({"message": "Movie added to watchlist"}, status=status.HTTP_201_CREATED)
        else:
            return Response({"message": "Movie already in watchlist"}, status=status.HTTP_200_OK)
    
    def delete(self, request):
        movie_id = request.data.get('id')
        if not movie_id:
            return Response({"error": "Movie ID is required"}, status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(Movie)
        try:
            watchlist_item = Watchlist.objects.get(
                user=request.user,
                content_type=content_type,
                object_id=movie_id,
            )
            watchlist_item.delete()
            return Response({"message": "Movie removed from watchlist"}, status=status.HTTP_204_NO_CONTENT)
        except Watchlist.DoesNotExist:
            return Response({"error": "Movie not in watchlist"}, status=status.HTTP_404_NOT_FOUND)

class MovieReviewView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get Movie Reviews",
        description="Get all reviews for a specific movie.",
        parameters=[
            OpenApiParameter(name="movie_id", description="ID of the movie to retrieve reviews for", required=True, type=int),
        ],
        responses={200: OpenApiExample("Movie Reviews", value=[
            {"id": 1, "user": "username", "rating": 8.5, "review_text": "Great movie!", "date_added": "2025-04-04T12:00:00Z"}
        ])}
    )
    def get(self, request):
        movie_id = request.GET.get('movie_id')
        if not movie_id:
            return Response({"error": "Movie ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            movie = Movie.objects.get(id=movie_id)
        except Movie.DoesNotExist:
            return Response({"error": "Movie not found"}, status=status.HTTP_404_NOT_FOUND)
            
        content_type = ContentType.objects.get_for_model(Movie)
        reviews = Review.objects.filter(
            content_type=content_type,
            object_id=movie_id
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
        summary="Create Movie Review",
        description="Create a new review for a movie.",
        request=OpenApiExample("Review Request", value={
            "movie_id": 1,
            "rating": 8.5,
            "review_text": "This was an excellent movie!"
        }),
        responses={201: OpenApiExample("Review Created", value={"message": "Review added successfully"})}
    )
    def post(self, request):
        movie_id = request.data.get('movie_id')
        rating = request.data.get('rating')
        review_text = request.data.get('review_text', '')
        date_added = request.data.get('date_added', timezone.now())
        
        if not movie_id:
            return Response({"error": "Movie ID is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        if not rating:
            return Response({"error": "Rating is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            rating = float(rating)
            if rating < 0 or rating > 10:
                return Response({"error": "Rating must be between 0 and 10"}, status=status.HTTP_400_BAD_REQUEST)
        except (ValueError, TypeError):
            return Response({"error": "Rating must be a valid number"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            movie = Movie.objects.get(id=movie_id)
        except Movie.DoesNotExist:
            return Response({"error": "Movie not found"}, status=status.HTTP_404_NOT_FOUND)
            
        content_type = ContentType.objects.get_for_model(Movie)
        
        # Check if user already has a review for this movie
        existing_review = Review.objects.filter(
            user=request.user,
            content_type=content_type,
            object_id=movie_id
        ).first()
        
        if existing_review:
            return Response({"error": "You have already reviewed this movie"}, status=status.HTTP_400_BAD_REQUEST)
            
        # Create the review
        review = Review.objects.create(
            user=request.user,
            content_type=content_type,
            object_id=movie_id,
            rating=rating,
            review_text=review_text,
            date_added=date_added
        )
            
        return Response({"message": "Review added successfully", "id": review.id}, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Update Movie Review",
        description="Update an existing review for a movie.",
        request=OpenApiExample("Update Review Request", value={
            "review_id": 1,
            "rating": 9.0,
            "review_text": "After watching it again, I liked it even more!"
        }),
        responses={200: OpenApiExample("Review Updated", value={"message": "Review updated successfully"})}
    )
    def put(self, request):
        review_id = request.data.get('review_id')
        rating = request.data.get('rating')
        review_text = request.data.get('review_text')
        date_added = request.data.get('date_added')
        print(f"Date reviewed: {date_added}")

        
        if not review_id:
            return Response({"error": "Review ID is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            review = Review.objects.get(id=review_id, user=request.user)
        except Review.DoesNotExist:
            return Response({"error": "Review not found or you don't have permission to edit it"}, 
                           status=status.HTTP_404_NOT_FOUND)
        
        if rating is not None:
            try:
                rating = float(rating)
                if rating < 0 or rating > 10:
                    return Response({"error": "Rating must be between 0 and 10"}, 
                                   status=status.HTTP_400_BAD_REQUEST)
                review.rating = rating
            except (ValueError, TypeError):
                return Response({"error": "Rating must be a valid number"}, 
                               status=status.HTTP_400_BAD_REQUEST)
        
        if review_text is not None:
            review.review_text = review_text
        
        print(f"Date reviewed: {date_added}")
        if date_added is not None:
            review.date_added = date_added
            
        review.save()
        return Response({"message": "Review updated successfully"}, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Delete Movie Review",
        description="Delete an existing review for a movie.",
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
            return Response({"error": "Review not found or you don't have permission to delete it"}, 
                           status=status.HTTP_404_NOT_FOUND)
            
        review.delete()
        return Response({"message": "Review deleted successfully"}, status=status.HTTP_204_NO_CONTENT)
    

@login_required
def collection_detail(request, collection_id):
    collection = get_object_or_404(Collection, id=collection_id)
    
    # Get all movies in this collection
    movies = Movie.objects.filter(collection=collection).order_by('-release_date')
    
    # Get content type for Movie model for annotations
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Get user's reviews for these movies
    user_reviews = Review.objects.filter(
        user=request.user,
        content_type=movie_content_type,
        object_id__in=movies.values_list('id', flat=True)
    ).values('object_id', 'rating')
    
    # Create a dictionary for quick lookup of ratings
    ratings_dict = {review['object_id']: review['rating'] for review in user_reviews}
    
    # Get user's watchlist items
    user_watchlist = Watchlist.objects.filter(
        user=request.user,
        content_type=movie_content_type,
        object_id__in=movies.values_list('id', flat=True)
    ).values_list('object_id', flat=True)
    
    # Annotate each movie with user's rating and watchlist status
    for movie in movies:
        movie.user_rating = int(ratings_dict.get(movie.id)) if ratings_dict.get(movie.id) is not None else None
        movie.in_watchlist = movie.id in user_watchlist
    
    return render(request, 'collection_page.html', {
        'collection': collection,
        'movies': movies
    })

@login_required
def all_movies_page(request):
    # Get filter parameters
    filter_title = request.GET.get('title', '')
    genre_id = request.GET.get('genre', '')
    current_sort = request.GET.get('sort_by', 'title')
    view_type = request.GET.get('view_type', 'grid')
    
    # Get content type for Movie model
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Define sort order
    sort_field = 'title'
    if current_sort == 'release_date':
        sort_field = '-release_date'
    elif current_sort == 'rating':
        sort_field = '-rating'
    
    # Base query for movies with filtering
    base_query = Movie.objects.prefetch_related('genres')
    
    if filter_title:
        base_query = base_query.filter(title__icontains=filter_title)
    
    if genre_id:
        base_query = base_query.filter(genres__id=genre_id)
    
    # Get all movies that match the filter criteria
    all_movies = list(base_query.order_by(sort_field))
    
    # Get user's reviews
    user_reviews = {
        review.object_id: review.rating 
        for review in Review.objects.filter(
            user=request.user,
            content_type=movie_content_type
        )
    }
    
    # Get user's watchlist with date_added
    user_watchlist = {
        item.object_id: item.date_added 
        for item in Watchlist.objects.filter(
            user=request.user,
            content_type=movie_content_type
        )
    }
    
    # Get all watchlist counts by movie
    watchlist_counts = {}
    for item in Watchlist.objects.filter(content_type=movie_content_type).values('object_id').annotate(count=Count('user')):
        watchlist_counts[item['object_id']] = item['count']
    
    # Get average ratings across the platform
    avg_ratings = {}
    rating_counts = {}
    for item in Review.objects.filter(content_type=movie_content_type).values('object_id').annotate(
        avg_rating=models.Avg('rating'),
        count=models.Count('id')
    ):
        avg_ratings[item['object_id']] = round(item['avg_rating'], 1)
        rating_counts[item['object_id']] = item['count']
    
    # Get reviews by other users (not current user)
    other_users_reviews = {}
    for review in Review.objects.filter(
        content_type=movie_content_type
    ).exclude(
        user=request.user
    ).select_related('user'):
        if review.object_id not in other_users_reviews:
            other_users_reviews[review.object_id] = []
        other_users_reviews[review.object_id].append({
            'user': review.user,
            'username': review.user.username,
            'rating': review.rating,
            'review_text': review.review_text,
            'date_added': review.date_added
        })
    
    # Categorize the movies
    reviewed_movies = []
    watchlist_movies = []
    others_watchlist = []
    undiscovered = []
    friends_reviewed = []  # New category for movies reviewed by others
    
    for movie in all_movies:
        # Add average rating if available
        if movie.id in avg_ratings:
            movie.avg_rating = avg_ratings[movie.id]
            movie.rating_count = rating_counts[movie.id]
        
        # Add other users' reviews if available
        if movie.id in other_users_reviews:
            movie.other_reviews = other_users_reviews[movie.id]
        
        # Is this movie reviewed by the current user?
        if movie.id in user_reviews:
            movie.user_rating = user_reviews[movie.id]
            reviewed_movies.append(movie)
        # Is this movie in the current user's watchlist?
        elif movie.id in user_watchlist:
            movie.date_added = user_watchlist[movie.id]
            watchlist_movies.append(movie)
        # Is this movie reviewed by others but not in user's watchlist or reviewed by user?
        elif movie.id in other_users_reviews:
            friends_reviewed.append(movie)
        # Is this movie in any watchlist?
        elif movie.id in watchlist_counts:
            movie.user_count = watchlist_counts[movie.id]
            others_watchlist.append(movie)
        # This movie is undiscovered
        else:
            undiscovered.append(movie)
    
    # Get all genres for filter dropdown
    genres = Genre.objects.all().order_by('name')
    current_genre = None
    if genre_id:
        current_genre = get_object_or_404(Genre, id=genre_id).name
    
    context = {
        'reviewed_movies': reviewed_movies,
        'watchlist_movies': watchlist_movies,
        'others_watchlist_movies': others_watchlist,
        'undiscovered_movies': undiscovered,
        'friends_reviewed_movies': friends_reviewed,  # Add to context
        'genres': genres,
        'current_genre': current_genre,
        'filter_title': filter_title,
        'current_sort': current_sort,
        'view_type': view_type,
    }
    
    return render(request, 'all_movies_page.html', context)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def movie_recommendations(request):
    """Get personalized movie recommendations for the current user"""
    limit = int(request.GET.get('limit', 10))
    recommender = MovieRecommender()
    recommendations = recommender.get_recommendations_for_user(request.user.id, limit)
    
    # Format the response data
    data = []
    for movie in recommendations:
        data.append({
            'id': movie.id,
            'tmdb_id': movie.tmdb_id,
            'title': movie.title,
            'poster': movie.poster,
            'release_date': movie.release_date,
            'rating': movie.rating,
            'collection': movie.collection.name if movie.collection else None
        })
    
    return Response(data)

# Add this new view to check task status

class MovieTaskStatusView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Check Movie Task Status",
        description="Check the status of a background movie creation task.",
        parameters=[
            OpenApiParameter(name="task_id", description="ID of the task to check", required=True, type=str),
        ],
        responses={
            200: OpenApiExample("Task Status", value={
                "complete": True,
                "success": True,
                "movie": {"id": 1, "title": "Movie Name", "...": "other fields"}
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
            
            # If task is complete and successful, try to get the movie
            if task.success and task.result:
                try:
                    # The result should be a Movie object
                    movie = task.result
                    response_data["movie"] = MovieSerializer(movie).data
                except Exception as e:
                    response_data["result"] = str(task.result)
                    response_data["error"] = str(e)
            elif not task.success and task.result:
                # If task failed, include error information
                response_data["error"] = str(task.result)
                
            return Response(response_data)
            
        except Task.DoesNotExist:
            return Response({"error": "Task not found"}, status=status.HTTP_404_NOT_FOUND)

@login_required
def random_unwatched_movie(request):
    """Redirects to a random movie that no one has watched yet"""
    # Get content type for Movie model
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Find movies that have no reviews (unwatched by anyone)
    reviewed_movie_ids = Review.objects.filter(
        content_type=movie_content_type
    ).values_list('object_id', flat=True)
    
    unwatched_movies = Movie.objects.exclude(id__in=reviewed_movie_ids, status='Released')
    
    # If there are no completely unwatched movies, get movies with fewest reviews
    if not unwatched_movies.exists():
        # Count reviews per movie and get those with minimum reviews
        movie_review_counts = Review.objects.filter(
            content_type=movie_content_type
        ).values('object_id').annotate(count=Count('id')).order_by('count')
        
        if movie_review_counts:
            min_count = movie_review_counts[0]['count']
            least_watched_ids = [
                item['object_id'] for item in movie_review_counts 
                if item['count'] == min_count
            ]
            unwatched_movies = Movie.objects.filter(id__in=least_watched_ids)
    
    if unwatched_movies.exists():
        # Get a random movie from the unwatched/least watched movies
        random_movie = random.choice(list(unwatched_movies))
        return redirect('movie_page', movie_id=random_movie.tmdb_id)
    else:
        # Fallback to a completely random movie if something went wrong
        random_movie = random.choice(list(Movie.objects.all()))
        return redirect('movie_page', movie_id=random_movie.tmdb_id)

@login_required
def shortest_watchlist_movie(request):
    """Redirects to one of the three shortest movies in the user's watchlist, selected randomly.
    If watchlist is empty, selects from the three shortest movies in the database."""
    
    # Get content type for Movie model
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Get user's watchlist items
    watchlist_movie_ids = Watchlist.objects.filter(
        user=request.user,
        content_type=movie_content_type
    ).values_list('object_id', flat=True)
    
    # Find the three shortest movies in watchlist by runtime
    shortest_movies = list(Movie.objects.filter(
        id__in=watchlist_movie_ids,
        runtime__isnull=False,  # Ensure runtime is not null
        status='Released'  # Ensure the movie is released
    ).order_by('runtime')[:3])
    
    # If no movies in watchlist, get the three shortest movies in the database
    if not shortest_movies:
        shortest_movies = list(Movie.objects.filter(
            runtime__isnull=False,
            status='Released'
        ).order_by('runtime')[:3])
    
    if shortest_movies:
        # Randomly select one of the three shortest movies
        selected_movie = random.choice(shortest_movies)
        return redirect('movie_page', movie_id=selected_movie.tmdb_id)
    else:
        # If no movies with runtime found at all, redirect to watchlist page
        return redirect('watchlist_page')

def community_page(request):
    """Main community page with Movie of the Week feature"""
    # Get current active movie of the week
    current_pick = MovieOfWeekPick.objects.filter(status='active').first()
    
    # If no active pick but there are queued picks, activate the next one
    if not current_pick and MovieOfWeekPick.objects.filter(status='queued').exists():
        next_pick = MovieOfWeekPick.objects.filter(status='queued').order_by('date_created').first()
        # Code to activate the next pick
    
    # Get queued movie suggestions
    queued_picks = MovieOfWeekPick.objects.filter(status='queued').order_by('date_created')
    
    # Get completed movies of the week
    completed_picks = MovieOfWeekPick.objects.filter(status='completed').order_by('-end_date')
    
    # Get reviews for current movie if one exists
    movie_reviews = []
    not_watched_users = []
    
    if current_pick:
        movie_content_type = ContentType.objects.get_for_model(Movie)
        movie_reviews = Review.objects.filter(
            content_type=movie_content_type,
            object_id=current_pick.movie.id
        ).select_related('user').order_by('-date_added')
        
        # Add reviews to current pick for easy access in template
        current_pick.reviews = movie_reviews
        
        active_users = CustomUser.objects.all()
        
        # Find users who haven't watched
        watched_user_ids = current_pick.watched_by.values_list('id', flat=True)
        not_watched_users = active_users.exclude(id__in=watched_user_ids)
    
    # Get user statistics
    user_context = {}
    if request.user.is_authenticated:
        if current_pick:
            # Check if user watched current movie
            user_context['has_watched_current'] = current_pick.watched_by.filter(id=request.user.id).exists()
            
            # Check if user has reviewed current movie
            if movie_reviews:
                user_review = movie_reviews.filter(user=request.user).first()
                user_context['has_reviewed_current'] = user_review is not None
                user_context['current_review'] = user_review
        
        # Check if user has an active suggestion
        user_context['has_active_suggestion'] = MovieOfWeekPick.objects.filter(
            suggested_by=request.user, 
            status__in=['active', 'queued']
        ).exists()
    
    context = {
        'current_pick': current_pick,
        'queued_picks': queued_picks,
        'completed_picks': completed_picks,
        'user_context': user_context,
        'not_watched_users': not_watched_users,
        'amount_of_users': CustomUser.objects.all().count(),
    }
    
    return render(request, 'community_page.html', context)

@login_required
def suggest_movie(request):
    """Handle movie suggestions for Movie of the Week"""
    if request.method != 'POST':
        return redirect('community_page')
    
    # Check if user already has an active suggestion
    if MovieOfWeekPick.objects.filter(
        suggested_by=request.user,
        status__in=['queued', 'active']
    ).exists():
        messages.error(request, "You already have an active movie suggestion.")
        return redirect('community_page')
    
    movie_id = request.POST.get('movie_id')
    reason = request.POST.get('reason')
    
    if not movie_id or not reason:
        messages.error(request, "Please provide both a movie and a reason.")
        return redirect('community_page')
    
    try:
        movie = Movie.objects.get(pk=movie_id)
        
        # Create the suggestion
        pick = MovieOfWeekPick.objects.create(
            movie=movie,
            suggested_by=request.user,
            suggestion_reason=reason
        )
        
        messages.success(request, f"Your suggestion '{movie.title}' has been added to the queue!")
        
        # If no active pick, make this one active
        if not MovieOfWeekPick.objects.filter(status='active').exists():
            activate_next_movie()
            
    except Movie.DoesNotExist:
        messages.error(request, "The selected movie was not found.")
    
    return redirect('community_page')

@login_required
def mark_movie_watched(request, pick_id):
    """Mark a movie of the week as watched"""
    pick = get_object_or_404(MovieOfWeekPick, pk=pick_id)
    
    # Verify this is the active movie
    if pick.status != 'active':
        messages.error(request, "You can only mark the current movie of the week as watched.")
        return redirect('community_page')
    
    # Add user to watched list if not already there
    if request.user not in pick.watched_by.all():
        pick.watched_by.add(request.user)
        messages.success(request, f"You've marked '{pick.movie.title}' as watched!")
        
        # Check if enough people have watched to move to next movie
        check_and_update_movie_status(pick)
    
    return redirect('community_page')

@login_required
def movie_week_discussion(request, pick_id):
    """Discussion page for a movie of the week"""
    pick = get_object_or_404(MovieOfWeekPick, pk=pick_id)
    movie = pick.movie
    
    # Get movie content type for reviews
    movie_type = ContentType.objects.get_for_model(Movie)
    
    # Get all reviews for this movie
    reviews = Review.objects.filter(
        content_type=movie_type,
        object_id=movie.id
    ).select_related('user').order_by('-date_added')
    
    # Check if user has already reviewed
    user_has_reviewed = False
    user_review = None
    
    if request.user.is_authenticated:
        user_review = Review.objects.filter(
            content_type=movie_type,
            object_id=movie.id,
            user=request.user
        ).first()
        user_has_reviewed = user_review is not None
    
    # Handle new review submission
    if request.method == 'POST' and not user_has_reviewed:
        rating = request.POST.get('rating')
        review_text = request.POST.get('review_text', '')
        review_date = request.POST.get('review_date')
        
        try:
            rating = float(rating)
            if 1 <= rating <= 10:
                # Create review
                review = Review.objects.create(
                    user=request.user,
                    content_type=movie_type,
                    object_id=movie.id,
                    rating=rating,
                    review_text=review_text
                )
                
                # If review date was provided and is valid, update the review date
                if review_date:
                    try:
                        from datetime import datetime
                        date_obj = datetime.strptime(review_date, '%Y-%m-%d')
                        review.date_watched = date_obj
                        review.save()
                    except (ValueError, TypeError):
                        # If date parsing fails, just continue - it's not critical
                        pass
                
                # Mark as watched
                if request.user not in pick.watched_by.all():
                    pick.watched_by.add(request.user)
                    # Check if enough people have watched
                    check_and_update_movie_status(pick)
                
                messages.success(request, "Your review has been added!")
                return redirect('movie_week_discussion')
            else:
                messages.error(request, "Rating must be between 1 and 10.")
        except (ValueError, TypeError):
            messages.error(request, "Please provide a valid rating.")
    
    context = {
        'pick': pick,
        'movie': movie,
        'reviews': reviews,
        'user_has_reviewed': user_has_reviewed,
        'user_review': user_review,
    }
    
    return render(request, 'movie_week_discussion.html', context)

def check_and_update_movie_status(pick):
    """Check if enough users have watched the current movie and update status if needed"""
    active_users = CustomUser.objects.all().count()
    
    # Calculate percentage of users who watched
    watched_percentage = (pick.watched_by.count() / active_users) * 100
    
    # Check if end date passed or enough users watched (100%)
    end_date_passed = pick.end_date and timezone.now().date() >= pick.end_date
    enough_watched = watched_percentage >= 100
    
    if end_date_passed or enough_watched:
        # Mark current pick as completed
        pick.status = 'completed'
        pick.save()
        
        # Activate next movie
        activate_next_movie()

def activate_next_movie():
    """Activate the next movie in the queue"""
    next_pick = MovieOfWeekPick.objects.filter(status='queued').order_by('date_created').first()
    
    if next_pick:
        start_date = timezone.now().date()
        # Set end date to 7 days from now
        end_date = start_date + timedelta(days=7)
        
        next_pick.status = 'active'
        next_pick.start_date = start_date
        next_pick.end_date = end_date
        next_pick.save()
        
        return next_pick
    
    return None

@login_required
def review_current_movie(request):
    """Handle submission of movie reviews for the current movie of the week"""
    if request.method != 'POST':
        return redirect('community_page')
    
    movie_id = request.POST.get('movie_id')
    rating = request.POST.get('rating')
    review_text = request.POST.get('review_text')
    
    if not movie_id or not rating:
        messages.error(request, "Missing required review information")
        return redirect('community_page')
    
    # Get current active pick
    current_pick = MovieOfWeekPick.objects.filter(status='active', movie_id=movie_id).first()
    
    if not current_pick:
        messages.error(request, "Invalid movie or movie is not currently active")
        return redirect('community_page')
    
    # Create or update review
    content_type = ContentType.objects.get_for_model(Movie)
    review, created = Review.objects.update_or_create(
        user=request.user,
        content_type=content_type,
        object_id=movie_id,
        defaults={
            'rating': float(rating),
            'review_text': review_text
        }
    )
    
    # Mark movie as watched if not already
    if not current_pick.watched_by.filter(id=request.user.id).exists():
        current_pick.watched_by.add(request.user)
        current_pick.save()
        
    if created:
        messages.success(request, "Review added successfully!")
    else:
        messages.success(request, "Review updated successfully!")
        
    return redirect('community_page')

from django.http import JsonResponse
from django.db.models import Q

@login_required
def movie_search(request):
    """Search endpoint for finding movies in the database when suggesting movies of the week"""
    query = request.GET.get('q', '').strip()
    
    if not query or len(query) < 2:
        return JsonResponse({'results': []})
    
    # Search in the database for movies matching the query
    # Limited to 20 results for performance
    movies = Movie.objects.filter(
        Q(title__icontains=query) | 
        Q(original_title__icontains=query)
    ).order_by('-rating', '-release_date')[:20]
    
    results = []
    for movie in movies:
        results.append({
            'id': movie.id,
            'tmdb_id': movie.tmdb_id,
            'title': movie.title,
            'release_date': movie.release_date.strftime('%Y-%m-%d') if movie.release_date else None,
            'poster_path': movie.poster,
            'rating': movie.rating,
            'runtime': movie.runtime,
            'genres': [genre.name for genre in movie.genres.all()[:3]]
        })
    
    return JsonResponse({'results': results})

@api_view(['GET'])
def current_community_pick(request):
    """Return the current movie of the week for the home page"""
    current_pick = MovieOfWeekPick.objects.filter(status='active').first()
    
    if not current_pick:
        return Response({})
    
    data = {
        'id': current_pick.id,
        'movie': {
            'id': current_pick.movie.id,
            'tmdb_id': current_pick.movie.tmdb_id,
            'title': current_pick.movie.title,
            'poster': current_pick.movie.poster,
        },
        'suggested_by': current_pick.suggested_by.username,
        'watched_count': current_pick.watched_count,
        'total_users': CustomUser.objects.all().count(),
        'end_date': current_pick.end_date.strftime('%b %d, %Y'),
    }
    
    return Response(data)