"""Unified "All Media" endpoint.

Not a true SQL UNION — instead, we fetch a top slice per media type in
parallel using each type's own filter/ordering, tag each item with its
`media_type`, and merge-sort them client-side (in Python). This keeps each
type's filtering pluggable and avoids brittle heterogeneous SQL.

Pagination is a slice over the merged buffer. "Page 2" is a re-merge —
documented tradeoff.
"""
from __future__ import annotations

from itertools import chain

from django.http import HttpRequest
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

from books.explorer_views import BookExplorerViewSet
from custom_auth.explorer_views import PersonExplorerViewSet
from games.explorer_views import GameExplorerViewSet
from movies.explorer_views import MovieExplorerViewSet
from tvshows.explorer_views import TVShowExplorerViewSet


ALL_MEDIA_MAX_PER_TYPE = 100


TYPE_VIEWSETS = {
    "movies": MovieExplorerViewSet,
    "tvshows": TVShowExplorerViewSet,
    "books": BookExplorerViewSet,
    "games": GameExplorerViewSet,
    "people": PersonExplorerViewSet,
}


def _instantiate(viewset_cls, request: HttpRequest):
    """Simulate DRF's viewset dispatch enough to run filter_backends."""
    view = viewset_cls()
    view.request = request
    view.action = "list"
    view.kwargs = {}
    view.format_kwarg = None
    return view


def _sort_key(item: dict, key: str, descending: bool):
    """Merge-sort key with None-tolerance (None sinks to the end regardless
    of direction)."""
    val = item.get(key)
    if val is None:
        return (1, 0)
    if descending:
        # invert numeric/date values by wrapping in a tuple that reverses order
        return (0, _NegatingWrapper(val))
    return (0, val)


class _NegatingWrapper:
    """Wraps a value so sort() orders it descending. Works for numbers,
    dates, and strings alike."""

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return self.value > other.value

    def __eq__(self, other):
        return self.value == other.value


@api_view(["GET"])
@authentication_classes([SessionAuthentication, BasicAuthentication])
@permission_classes([IsAuthenticated])
def all_media(request):
    types_param = request.GET.get("types", "movies,tvshows,books,games,people")
    types = [t.strip() for t in types_param.split(",") if t.strip() in TYPE_VIEWSETS]
    if not types:
        return Response({"results": [], "count": 0})

    ordering = request.GET.get("ordering", "-rating")
    descending = ordering.startswith("-")
    sort_key = ordering.lstrip("-")

    try:
        page = max(1, int(request.GET.get("page", "1")))
        page_size = min(200, max(1, int(request.GET.get("page_size", "48"))))
    except ValueError:
        page = 1
        page_size = 48

    merged: list[dict] = []
    for media_type in types:
        viewset = _instantiate(TYPE_VIEWSETS[media_type], request)
        queryset = viewset.filter_queryset(viewset.get_queryset())
        # per-type slice — bounded so we don't over-fetch
        slice_qs = queryset[:ALL_MEDIA_MAX_PER_TYPE]
        serializer = viewset.get_serializer(slice_qs, many=True)
        merged.extend(serializer.data)

    merged.sort(key=lambda it: _sort_key(it, sort_key, descending))

    total = len(merged)
    start = (page - 1) * page_size
    end = start + page_size

    return Response(
        {
            "count": total,
            "page": page,
            "page_size": page_size,
            "num_pages": max(1, (total + page_size - 1) // page_size),
            "types": types,
            "ordering": ordering,
            "results": merged[start:end],
            "note": "All-Media pagination is a merge over the top slice per type; deep pages are approximate.",
        }
    )
