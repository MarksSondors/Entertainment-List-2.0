from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views
from .views import TMDBSearchView, MovieViewSet

router = DefaultRouter()
router.register(r'', MovieViewSet, basename='movies')

urlpatterns = [
    path('add', views.create_movie_page, name='add_movie'),

    # API
    path('search/', TMDBSearchView.as_view(), name='tmdb_search'),   
] + router.urls