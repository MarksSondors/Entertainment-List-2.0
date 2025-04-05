from django.urls import path
from rest_framework.routers import DefaultRouter
from . import views

urlpatterns = [
    # Search endpoint
    path('search/', views.MusicSearchView.as_view(), name='music_search'),
    
    # Album endpoints
    path('albums/', views.AlbumViewSet.as_view({'post': 'create'}), name='albums-list'),
]