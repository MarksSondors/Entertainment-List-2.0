"""Movies explorer viewset — separate from `MovieViewSet` to avoid touching
the existing (non-paginated) endpoint that current UI consumers depend on.
"""
from __future__ import annotations

from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from custom_auth.models import Genre
from explorer.ordering import ExplorerOrderingFilter
from explorer.pagination import ExplorerPagination
from explorer.utils import annotate_user_context, annotate_user_rating

from .filters import MovieFilter
from .models import Movie
from .serializers import MovieListSerializer


class MovieExplorerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = MovieListSerializer
    pagination_class = ExplorerPagination
    filter_backends = [DjangoFilterBackend, ExplorerOrderingFilter, SearchFilter]
    filterset_class = MovieFilter
    ordering_fields = [
        "title",
        "release_date",
        "user_rating",
        "runtime",
        "date_added",
    ]
    ordering = ["-date_added"]

    def get_queryset(self):
        qs = (
            Movie.objects.only(
                "id",
                "title",
                "original_title",
                "tmdb_id",
                "poster",
                "release_date",
                "rating",
                "runtime",
                "status",
                "is_anime",
                "date_added",
            )
            .prefetch_related(
                Prefetch("genres", queryset=Genre.objects.only("id", "name")),
            )
        )
        qs = annotate_user_rating(qs, Movie)
        qs = annotate_user_context(qs, Movie, getattr(self.request, "user", None))
        return qs
