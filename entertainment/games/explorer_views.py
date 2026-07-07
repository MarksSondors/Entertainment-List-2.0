"""Games explorer viewset."""
from __future__ import annotations

from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from explorer.ordering import ExplorerOrderingFilter
from explorer.pagination import ExplorerPagination
from explorer.utils import annotate_user_context, annotate_user_rating

from .filters import GameFilter
from .models import Game, GameGenre, Platform
from .serializers import GameListSerializer


class GameExplorerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = GameListSerializer
    pagination_class = ExplorerPagination
    filter_backends = [DjangoFilterBackend, ExplorerOrderingFilter, SearchFilter]
    filterset_class = GameFilter
    ordering_fields = [
        "title",
        "release_date",
        "user_rating",
        "metacritic",
        "date_added",
    ]
    ordering = ["-date_added"]

    def get_queryset(self):
        qs = (
            Game.objects.only(
                "id",
                "title",
                "original_title",
                "poster",
                "release_date",
                "rating",
                "metacritic",
                "date_added",
            )
            .prefetch_related(
                Prefetch("genres", queryset=GameGenre.objects.only("id", "name")),
                Prefetch("platforms", queryset=Platform.objects.only("id", "name")),
            )
        )
        qs = annotate_user_rating(qs, Game)
        qs = annotate_user_context(qs, Game, getattr(self.request, "user", None))
        return qs
