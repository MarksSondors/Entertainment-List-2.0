from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .views import RAWGSearchView, GameScreenshotsView, GameBackdropsView, GamePosterView, GameCoversView, GameViewSet, GameReviewView, GameWatchlistView

# Create a router for the viewset
router = DefaultRouter()
router.register(r'api', GameViewSet, basename='games')

urlpatterns = [
    # Page views
    path('', views.game_list, name='game_list'),
    path('<int:pk>/', views.game_detail, name='game_detail'),
    path('developer/<int:pk>/', views.developer_detail, name='developer_detail'),
    path('publisher/<int:pk>/', views.publisher_detail, name='publisher_detail'),
    path('collection/<int:pk>/', views.collection_detail, name='game_collection_detail'),
    
    # API endpoints
    path('search/', RAWGSearchView.as_view(), name='rawg_search'),
    path('screenshots/', GameScreenshotsView.as_view(), name='game_screenshots'),
    path('backdrops/', GameBackdropsView.as_view(), name='game_backdrops'),
    path('poster/', GamePosterView.as_view(), name='game_poster'),
    path('covers/', GameCoversView.as_view(), name='game_covers'),
    path('reviews/', GameReviewView.as_view(), name='game_reviews'),
    path('watchlist/', GameWatchlistView.as_view(), name='game_watchlist'),
    
    # Include router URLs
    path('', include(router.urls)),
]
