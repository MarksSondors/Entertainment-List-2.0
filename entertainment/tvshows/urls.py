from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import TMDBTVSearchView, TVShowViewSet, PopularTVShowsView, TaskStatusView
from django.views.decorators.cache import cache_page

router = DefaultRouter()
router.register(r'', TVShowViewSet, basename='tvshows')

urlpatterns = [
    path('<int:show_id>/', views.tv_show_page, name='tv_show_page'),
    
    # API endpoints
    path('search/', TMDBTVSearchView.as_view(), name='tmdb_tv_search'),
    path('popular/', cache_page(60*60)(PopularTVShowsView.as_view()), name='popular_tv_shows'),
    path('images/', views.TVShowImagesView.as_view(), name='tv_show_images'),
    path('watchlist/', views.WatchlistTVShow.as_view(), name='watchlist_tv_show'),
    path('task-status/', TaskStatusView.as_view(), name='task-status'),
    path('episodes/<int:episode_id>/watched/', views.EpisodeWatchedView.as_view(), name='episode_watched'),
    path('subgroup/<int:subgroup_id>/episodes/', views.subgroup_episodes, name='subgroup_episodes'),
    path('subgroup/<int:subgroup_id>/review/', views.subgroup_review, name='subgroup_review'),
    path('season/<int:season_id>/review/', views.season_review, name='season_review'),

] + router.urls