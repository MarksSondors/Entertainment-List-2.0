from django.urls import path
from . import views

app_name = 'books'

urlpatterns = [
    path('search/', views.HardcoverSearchView.as_view(), name='hardcover_search'),
    path('add/<int:hardcover_id>/', views.AddBookView.as_view(), name='add_book'),
    path('<int:book_id>/', views.book_page, name='book_page'),
    path('collections/<int:collection_id>/', views.book_collection_page, name='book_collection_page'),
    path('series/<int:series_id>/', views.book_series_page, name='book_series_page'),
    path('watchlist/', views.WatchlistBookView.as_view(), name='book_watchlist'),
    path('reviews/', views.BookReviewView.as_view(), name='book_reviews'),
]
