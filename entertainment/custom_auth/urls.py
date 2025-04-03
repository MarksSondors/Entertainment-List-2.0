from django.urls import path
from . import views

urlpatterns = [
    # login page
    path('', views.login_page, name='login_page'),
    path('login/', views.login_request, name='login_request'),

    # home page
    path('home/', views.home_page, name='home_page'),
    path('logout/', views.logout_request, name='logout_request'),

    # genres
    path('genres/', views.browse_by_genre, name='browse_by_genre'),
    path('genres/<int:genre_id>/', views.genre_detail, name='browse_by_genre_detail'),

    # profile
    path('profile/', views.profile_page, name='profile'),
    path('profile/<str:username>/', views.profile_page, name='profile_with_username'),

    # watchlist
    path('watchlist/', views.watchlist_page, name='watchlist_page'),

]