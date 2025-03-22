from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import TMDBSearchView, MovieViewSet, PopularMoviesView
from django.views.decorators.cache import cache_page

router = DefaultRouter()
router.register(r'', MovieViewSet, basename='movies')

urlpatterns = [
    path('add', views.create_movie_page, name='add_movie'),
    path('<int:movie_id>/', views.movie_page, name='movie_page'),

    # API
    path('search/', TMDBSearchView.as_view(), name='tmdb_search'),
    path('popular/', cache_page(60 * 60)(PopularMoviesView.as_view()), name='popular_movies'),
] + router.urls