"""Folder-tree endpoint for the file explorer.

Returns a hierarchical listing of "folders" for a given media type. Folders
are virtual (Genre, Country, Year decade, Collection, My Watchlist, My
Reviews) and clicking one applies the corresponding filter to the list view.

Cached for 5 minutes per user (watchlist counts vary per user); non-user
folders like Genre counts share a longer 30-minute cache under a distinct
key so the anonymous case reuses it.
"""
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.db.models import Count
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.authentication import (
    BasicAuthentication,
    SessionAuthentication,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from books.models import Book, BookGenre
from custom_auth.models import Country, Genre, Person, Review, Watchlist
from games.models import Game, GameGenre, Platform
from movies.models import Movie
from tvshows.models import TVShow

from .utils import content_type_id


TREE_CACHE_TTL = 60 * 5
GLOBAL_CACHE_TTL = 60 * 30


def _decade_buckets(qs, date_field: str):
    """Return [{'label': '2010s', 'value': 2010, 'count': N}, ...]."""
    year_expr = f"{date_field}__year"
    rows = (
        qs.exclude(**{f"{date_field}__isnull": True})
        .values(year_expr)
        .annotate(n=Count("id"))
    )
    buckets: dict[int, int] = {}
    for row in rows:
        year = row[year_expr]
        if year is None:
            continue
        decade = (year // 10) * 10
        buckets[decade] = buckets.get(decade, 0) + row["n"]
    return [
        {"label": f"{d}s", "value": d, "count": buckets[d]}
        for d in sorted(buckets, reverse=True)
    ]


def _user_folder_counts(user, model_cls) -> dict[str, int]:
    if not user or not user.is_authenticated:
        return {"watchlist": 0, "reviewed": 0}
    ct = content_type_id(model_cls)
    return {
        "watchlist": Watchlist.objects.filter(user=user, content_type_id=ct).count(),
        "reviewed": Review.objects.filter(user=user, content_type_id=ct).count(),
    }


def _movies_tree(user):
    total = Movie.objects.count()
    genres = list(
        Genre.objects.annotate(count=Count("movie"))
        .filter(count__gt=0)
        .order_by("name")
        .values("id", "name", "count")
    )
    countries = list(
        Country.objects.annotate(count=Count("movie"))
        .filter(count__gt=0)
        .order_by("name")
        .values("id", "name", "count")
    )
    user_counts = _user_folder_counts(user, Movie)
    return {
        "media_type": "movies",
        "label": "Movies",
        "icon": "camera_vid",
        "total": total,
        "children": [
            {"key": "all", "label": "All Movies", "count": total, "icon": "directory_open"},
            {"key": "watchlist", "label": "On My Watchlist", "count": user_counts["watchlist"], "icon": "star"},
            {"key": "reviewed", "label": "Reviewed by Me", "count": user_counts["reviewed"], "icon": "notepad_file"},
            {"key": "genres", "label": "By Genre", "icon": "directory_closed", "children": genres},
            {"key": "countries", "label": "By Country", "icon": "world", "children": countries},
            {"key": "decades", "label": "By Decade", "icon": "calendar",
             "children": _decade_buckets(Movie.objects, "release_date")},
        ],
    }


def _tvshows_tree(user):
    total = TVShow.objects.count()
    genres = list(
        Genre.objects.annotate(count=Count("tvshow"))
        .filter(count__gt=0)
        .order_by("name")
        .values("id", "name", "count")
    )
    countries = list(
        Country.objects.annotate(count=Count("tvshow"))
        .filter(count__gt=0)
        .order_by("name")
        .values("id", "name", "count")
    )
    user_counts = _user_folder_counts(user, TVShow)
    return {
        "media_type": "tvshows",
        "label": "TV Shows",
        "icon": "network_television",
        "total": total,
        "children": [
            {"key": "all", "label": "All Shows", "count": total, "icon": "directory_open"},
            {"key": "watchlist", "label": "On My Watchlist", "count": user_counts["watchlist"], "icon": "star"},
            {"key": "reviewed", "label": "Reviewed by Me", "count": user_counts["reviewed"], "icon": "notepad_file"},
            {"key": "genres", "label": "By Genre", "icon": "directory_closed", "children": genres},
            {"key": "countries", "label": "By Country", "icon": "world", "children": countries},
            {"key": "decades", "label": "By Decade", "icon": "calendar",
             "children": _decade_buckets(TVShow.objects, "first_air_date")},
        ],
    }


def _books_tree(user):
    total = Book.objects.count()
    genres = list(
        BookGenre.objects.annotate(count=Count("books"))
        .filter(count__gt=0)
        .order_by("name")
        .values("id", "name", "count")
    )
    user_counts = _user_folder_counts(user, Book)
    return {
        "media_type": "books",
        "label": "Books",
        "icon": "document",
        "total": total,
        "children": [
            {"key": "all", "label": "All Books", "count": total, "icon": "directory_open"},
            {"key": "watchlist", "label": "On My Watchlist", "count": user_counts["watchlist"], "icon": "star"},
            {"key": "reviewed", "label": "Reviewed by Me", "count": user_counts["reviewed"], "icon": "notepad_file"},
            {"key": "genres", "label": "By Genre", "icon": "directory_closed", "children": genres},
            {"key": "decades", "label": "By Decade", "icon": "calendar",
             "children": _decade_buckets(Book.objects, "published_date")},
        ],
    }


def _games_tree(user):
    total = Game.objects.count()
    genres = list(
        GameGenre.objects.annotate(count=Count("game"))
        .filter(count__gt=0)
        .order_by("name")
        .values("id", "name", "count")
    )
    platforms = list(
        Platform.objects.annotate(count=Count("game"))
        .filter(count__gt=0)
        .order_by("name")
        .values("id", "name", "count")
    )
    user_counts = _user_folder_counts(user, Game)
    return {
        "media_type": "games",
        "label": "Games",
        "icon": "solitaire",
        "total": total,
        "children": [
            {"key": "all", "label": "All Games", "count": total, "icon": "directory_open"},
            {"key": "watchlist", "label": "On My Watchlist", "count": user_counts["watchlist"], "icon": "star"},
            {"key": "reviewed", "label": "Reviewed by Me", "count": user_counts["reviewed"], "icon": "notepad_file"},
            {"key": "genres", "label": "By Genre", "icon": "directory_closed", "children": genres},
            {"key": "platforms", "label": "By Platform", "icon": "computer", "children": platforms},
            {"key": "decades", "label": "By Decade", "icon": "calendar",
             "children": _decade_buckets(Game.objects, "release_date")},
        ],
    }


def _people_tree():
    total = Person.objects.count()
    role_folders = [
        ("is_actor", "Actors"),
        ("is_director", "Directors"),
        ("is_screenwriter", "Screenwriters"),
        ("is_musician", "Musicians"),
        ("is_book_author", "Authors"),
        ("is_original_music_composer", "Composers"),
        ("is_tv_creator", "TV Creators"),
        ("is_comic_artist", "Comic Artists"),
        ("is_graphic_novelist", "Graphic Novelists"),
    ]
    roles = [
        {"key": attr, "label": label, "count": Person.objects.filter(**{attr: True}).count()}
        for attr, label in role_folders
    ]
    return {
        "media_type": "people",
        "label": "People",
        "icon": "users",
        "total": total,
        "children": [
            {"key": "all", "label": "All People", "count": total, "icon": "directory_open"},
            {"key": "roles", "label": "By Role", "icon": "user_card", "children": roles},
        ],
    }


def _build_full_tree(user):
    return {
        "media_types": [
            _movies_tree(user),
            _tvshows_tree(user),
            _books_tree(user),
            _games_tree(user),
            _people_tree(),
        ]
    }


@api_view(["GET"])
@authentication_classes([SessionAuthentication, BasicAuthentication])
@permission_classes([IsAuthenticated])
def tree(request):
    user = request.user
    cache_key = f"explorer:tree:{user.pk}"
    payload = cache.get(cache_key)
    if payload is None:
        payload = _build_full_tree(user)
        cache.set(cache_key, payload, TREE_CACHE_TTL)
    return Response(payload)
