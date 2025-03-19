from django.urls import path
from . import views

urlpatterns = [
    path('add_movie/', views.create_movie_page, name='add_movie'),

    # requests
    path('search/', views.tmdb_search, name='tmdb_search'),
]