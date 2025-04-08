from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import TMDBSearchView, MovieViewSet, PopularMoviesView
from django.views.decorators.cache import cache_page

router = DefaultRouter()
router.register(r'', MovieViewSet, basename='movies')

urlpatterns = [
    path('<int:movie_id>/', views.movie_page, name='movie_page'),
    path('collections/<int:collection_id>/', views.collection_detail, name='collection_detail'),
    path('all/', views.all_movies_page, name='all_movies_page'),

    # API
    path('search/', TMDBSearchView.as_view(), name='tmdb_search'),
    path('popular/', cache_page(60 * 60)(PopularMoviesView.as_view()), name='popular_movies'),
    path('images/', views.MovieImagesView.as_view(), name='movie_images'),
    path('watchlist/', views.WatchlistMovie.as_view(), name='watchlist'),
    path('reviews/', views.MovieReviewView.as_view(), name='movie_reviews'),
    path('recommendations/', cache_page(60 * 60)(views.movie_recommendations), name='movie_recommendations'),
] + router.urls