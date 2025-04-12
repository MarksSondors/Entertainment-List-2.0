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

    # countries
    path('countries/', views.browse_by_country, name='browse_by_country'),
    path('countries/<int:country_id>/', views.country_detail, name='browse_by_country_detail'),

    # profile
    path('profile/', views.profile_page, name='profile_page'),
    path('profile/<str:username>/', views.profile_page, name='profile_with_username'),

    # watchlist
    path('watchlist/', views.watchlist_page, name='watchlist_page'),

    # People
    path('people/<int:person_id>/', views.person_detail, name='person_detail'),
    path('people/', views.browse_by_people, name='browse_by_people'),

    # reviews
    path('reviews/recent/', views.recent_reviews, name='recent_reviews'),

    # activity
    path('activity/recent/', views.recent_activity, name='recent_activity'),

]