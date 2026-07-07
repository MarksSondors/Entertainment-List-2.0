"""Books explorer viewset."""
from __future__ import annotations

from django.db.models import Prefetch
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from custom_auth.models import Person
from explorer.ordering import ExplorerOrderingFilter
from explorer.pagination import ExplorerPagination
from explorer.utils import annotate_user_context, annotate_user_rating

from .filters import BookFilter
from .models import Book, BookGenre
from .serializers import BookListSerializer


class BookExplorerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = BookListSerializer
    pagination_class = ExplorerPagination
    filter_backends = [DjangoFilterBackend, ExplorerOrderingFilter, SearchFilter]
    filterset_class = BookFilter
    ordering_fields = [
        "title",
        "published_date",
        "user_rating",
        "pages",
        "date_added",
    ]
    ordering = ["-date_added"]

    def get_queryset(self):
        qs = (
            Book.objects.only(
                "id",
                "title",
                "original_title",
                "subtitle",
                "image_url",
                "published_date",
                "rating",
                "pages",
                "language",
                "date_added",
            )
            .prefetch_related(
                Prefetch("authors", queryset=Person.objects.only("id", "name")),
                Prefetch("genres", queryset=BookGenre.objects.only("id", "name")),
            )
        )
        qs = annotate_user_rating(qs, Book)
        qs = annotate_user_context(qs, Book, getattr(self.request, "user", None))
        return qs
