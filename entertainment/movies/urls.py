from django.urls import path
from . import views
from .views import TMDBSearchView

urlpatterns = [
    path('add', views.create_movie_page, name='add_movie'),

    # API
    path('search/', TMDBSearchView.as_view(), name='tmdb_search'),
]