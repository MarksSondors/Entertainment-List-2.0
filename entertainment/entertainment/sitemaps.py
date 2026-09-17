"""robots.txt and sitemap.xml for entertaint.men.

robots.txt is a plain static file served from /static/ (whitenoise collects it);
the redirect to /static/robots.txt is wired in entertainment/urls.py so the
canonical /robots.txt path works.

The sitemap lists public, non-personal pages: the login page and each media
detail page. Personal pages (watchlists, profiles) are deliberately excluded.
"""
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from books.models import Book
from games.models import Game
from movies.models import Movie
from tvshows.models import TVShow


class StaticViewSitemap(Sitemap):
    priority = 0.5
    changefreq = 'weekly'

    def items(self):
        return ['login_request']  # the only genuinely public page

    def location(self, item):
        return reverse(item)


class MovieSitemap(Sitemap):
    priority = 0.7
    changefreq = 'weekly'

    def items(self):
        return Movie.objects.all().only('id', 'tmdb_id', 'date_updated')

    def location(self, obj):
        return obj.get_absolute_url()

    def lastmod(self, obj):
        return obj.date_updated


class TVShowSitemap(Sitemap):
    priority = 0.7
    changefreq = 'weekly'

    def items(self):
        return TVShow.objects.all().only('id', 'tmdb_id', 'date_updated')

    def location(self, obj):
        return obj.get_absolute_url()

    def lastmod(self, obj):
        return obj.date_updated


class BookSitemap(Sitemap):
    priority = 0.6
    changefreq = 'weekly'

    def items(self):
        return Book.objects.all().only('id', 'date_updated')

    def location(self, obj):
        return f'/books/{obj.id}/'

    def lastmod(self, obj):
        return obj.date_updated


class GameSitemap(Sitemap):
    priority = 0.6
    changefreq = 'weekly'

    def items(self):
        return Game.objects.all().only('id', 'date_updated')

    def location(self, obj):
        return f'/games/{obj.id}/'

    def lastmod(self, obj):
        return obj.date_updated


sitemaps = {
    'static': StaticViewSitemap,
    'movies': MovieSitemap,
    'tvshows': TVShowSitemap,
    'books': BookSitemap,
    'games': GameSitemap,
}
