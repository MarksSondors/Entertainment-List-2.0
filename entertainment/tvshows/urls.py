from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import TMDBTVSearchView, TVShowViewSet, PopularTVShowsView
from django.views.decorators.cache import cache_page, never_cache

router = DefaultRouter()
router.register(r'', TVShowViewSet, basename='tvshows')

urlpatterns = [
    path('<int:show_id>/', views.tv_show_page, name='tv_show_page'),
    
    # API endpoints
    path('search/', TMDBTVSearchView.as_view(), name='tmdb_tv_search'),
    path('popular/', never_cache(PopularTVShowsView.as_view()), name='popular_tv_shows'),
    path('images/', views.TVShowImagesView.as_view(), name='tv_show_images'),
    path('watchlist/', views.WatchlistTVShow.as_view(), name='watchlist_tv_show'),
] + router.urls