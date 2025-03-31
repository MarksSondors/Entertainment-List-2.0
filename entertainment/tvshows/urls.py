from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import TMDBTVSearchView, TVShowViewSet, PopularTVShowsView
from django.views.decorators.cache import cache_page

router = DefaultRouter()
router.register(r'', TVShowViewSet, basename='tvshows')

urlpatterns = [
    path('add/', views.create_tv_show_page, name='add_tv_show'),
    path('<int:show_id>/', views.tv_show_page, name='tv_show_page'),
    
    # API endpoints
    path('search/', TMDBTVSearchView.as_view(), name='tmdb_tv_search'),
    path('popular/', cache_page(60 * 60)(PopularTVShowsView.as_view()), name='popular_tv_shows'),
    path('images/', views.TVShowImagesView.as_view(), name='tv_show_images'),
] + router.urls