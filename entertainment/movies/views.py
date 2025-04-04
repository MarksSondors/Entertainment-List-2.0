from django.shortcuts import render, get_object_or_404
from api.services.movies import MoviesService
from django.http import JsonResponse, Http404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample

import json

from .serializers import MovieSerializer
from .parsers import create_movie
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth.decorators import login_required
from custom_auth.models import *
from django.http import JsonResponse

# Create your views here.


@login_required
def movie_page(request, movie_id):
    try:
        movie_db = Movie.objects.prefetch_related(
            'genres', 
            'countries', 
            'keywords'
        ).get(tmdb_id=movie_id)
    except Movie.DoesNotExist:
        movie_db = create_movie(movie_id)
        if not movie_db:
            raise Http404(f"Movie with ID {movie_id} could not be created")
    # get user information if he has written a review is the movie in his watchlist
    user_watchlist = Watchlist.objects.filter(
        user=request.user,
        content_type=ContentType.objects.get_for_model(Movie),
        object_id=movie_db.id
    ).exists()
    context = {
        'movie': movie_db,
        'user_watchlist': user_watchlist,
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
        description="Create a new movie.",
        request=MovieSerializer,
        responses={201: MovieSerializer},
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

        content_type = ContentType.objects.get_for_model(Movie)
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
            {"id": 1, "user": "username", "rating": 8.5, "review_text": "Great movie!", "date_reviewed": "2025-04-04T12:00:00Z"}
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
                "date_reviewed": review.date_reviewed
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
            review_text=review_text
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