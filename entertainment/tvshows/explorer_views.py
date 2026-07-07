"""TV Shows explorer viewset."""
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

from .filters import TVShowFilter
from .models import TVShow
from .serializers import TVShowListSerializer


class TVShowExplorerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = TVShowListSerializer
    pagination_class = ExplorerPagination
    filter_backends = [DjangoFilterBackend, ExplorerOrderingFilter, SearchFilter]
    filterset_class = TVShowFilter
    ordering_fields = [
        "title",
        "first_air_date",
        "last_air_date",
        "user_rating",
        "date_added",
    ]
    ordering = ["-date_added"]

    def get_queryset(self):
        qs = (
            TVShow.objects.only(
                "id",
                "title",
                "original_title",
                "tmdb_id",
                "poster",
                "first_air_date",
                "last_air_date",
                "rating",
                "status",
                "is_anime",
                "date_added",
            )
            .prefetch_related(
                Prefetch("genres", queryset=Genre.objects.only("id", "name")),
            )
        )
        qs = annotate_user_rating(qs, TVShow)
        qs = annotate_user_context(qs, TVShow, getattr(self.request, "user", None))
        return qs
