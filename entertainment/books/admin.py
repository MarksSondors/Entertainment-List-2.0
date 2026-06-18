from django.contrib import admin
from .models import Book, BookSeries, Publisher, BookCollection, BookGenre


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ('title', 'author_names', 'published_date', 'rating', 'hardcover_id')
    search_fields = ('title', 'isbn_10', 'isbn_13')
    list_filter = ('language', 'genres')
    raw_id_fields = ('authors', 'keywords', 'added_by')
    filter_horizontal = ('genres', 'publishers')


@admin.register(BookSeries)
class BookSeriesAdmin(admin.ModelAdmin):
    list_display = ('name', 'hardcover_id')
    search_fields = ('name',)


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    list_display = ('name', 'hardcover_id')
    search_fields = ('name',)


@admin.register(BookCollection)
class BookCollectionAdmin(admin.ModelAdmin):
    list_display = ('name', 'hardcover_id')
    search_fields = ('name',)


@admin.register(BookGenre)
class BookGenreAdmin(admin.ModelAdmin):
    list_display = ('name', 'hardcover_tag_id')
    search_fields = ('name',)

