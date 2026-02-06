from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import TMDBSearchView, MovieViewSet, PopularMoviesView, MovieTaskStatusView
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
    path('recommendations/external/', views.ExternalRecommendationsView.as_view(), name='external_recommendations'),
    path('reviews/', views.MovieReviewView.as_view(), name='movie_reviews'),
    path('recommendations/', views.movie_recommendations, name='movie_recommendations'),
    path('task-status/', MovieTaskStatusView.as_view(), name='movie-task-status'),
    path('train-model/', views.train_model, name='train_model'),
    path('upload-model/', views.upload_model, name='upload_model'),
    path('download-dataset/', views.download_dataset, name='download_dataset'),
    path('search-local/', views.movie_search, name='movie_search_local'),

    # almost API
    path('random-unwatched/', views.random_unwatched_movie, name='random_unwatched_movie'),
    path('shortest-watchlist/', views.shortest_watchlist_movie, name='shortest_watchlist_movie'),

    path('movies/review-current-movie/', views.review_current_movie, name='review_current_movie'),
    path('community/current-pick/', views.current_community_pick, name='current_community_pick'),
    # Community
    path('community/', views.community_page, name='community_page'),
    path('community/suggest/', views.suggest_movie, name='suggest_movie'),
    path('community/movie/<int:pick_id>/watched/', views.mark_movie_watched, name='mark_movie_watched'),
    path('community/movie/<int:pick_id>/discussion/', views.movie_week_discussion, name='movie_week_discussion'),
    
    # Network Graph (Graph Theory)
    path('network-graph/', views.network_graph_page, name='network_graph_page'),
    path('network-graph/data/', views.network_graph_data, name='network_graph_data'),
] + router.urls