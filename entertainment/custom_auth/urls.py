from django.urls import path
from . import views

urlpatterns = [    # discover page
    path('discover/', views.discover_page, name='discover_page'),
    # api endpoint for discover page
    path('discover/search/', views.search_bar_discover, name='discover_search'),
    path('discover/genres/', views.discover_genres, name='discover_genres'),

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
    path('settings/', views.settings_page, name='settings_page'),
    path('settings/stremio/', views.stremio_addon_page, name='stremio_addon_page'),

    # watchlist
    path('watchlist/', views.watchlist_page, name='watchlist_page'),
    path('api/watchlist/', views.api_watchlist, name='api_watchlist'),
    path('api/watchlist/remove/', views.remove_from_watchlist, name='remove_from_watchlist'),

    # People
    path('people/<int:person_id>/', views.people_detail, name='person_detail'),
    path('people/', views.browse_by_people, name='browse_by_people'),
    path('api/people/', views.api_people_by_category, name='api_people_by_category'),
    path('api/people/<int:person_id>/tmdb-filmography/', views.get_person_tmdb_filmography, name='person_tmdb_filmography'),

    # reviews
    path('reviews/recent/', views.recent_reviews, name='recent_reviews'),

    # activity
    path('activity/recent/', views.recent_activity, name='recent_activity'),

    # statistics
    path('statistics/', views.statistics_page, name='statistics_page'),

    # recommendations
    path('api/recommendations/combined/', views.combined_recommendations, name='combined_recommendations'),

    # release calendar
    path('calendar/', views.release_calendar, name='release_calendar'),

    path('production_companies/<int:company_id>/', views.production_company_detail, name='production_company_detail'),

]