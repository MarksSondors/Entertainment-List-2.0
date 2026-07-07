"""People explorer viewset.

Optimizations:
* `.only(...)` projection — no `bio` (large text field).
* `media_count` — reads from the denormalized `Person.media_count` column
  populated by the `MediaPerson` post-save/delete signals (see
  custom_auth/signals.py). Sortable and cheap.
* No prefetch of `mediaperson_set` — role labels come from boolean flags
  which are all local columns.
* `user_rating` — aggregate of all Reviews on media this person appears in,
  computed via `annotate_person_rating` (correlated Subquery). Not
  indexed; acceptable for the current dataset size. Denormalize if it
  becomes a hot path.
"""
from __future__ import annotations

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, viewsets
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated

from explorer.ordering import ExplorerOrderingFilter
from explorer.pagination import ExplorerPagination
from explorer.utils import annotate_person_rating

from .filters import PersonFilter
from .models import Person
from .serializers import PersonListSerializer


class PersonExplorerViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = PersonListSerializer
    pagination_class = ExplorerPagination
    filter_backends = [DjangoFilterBackend, ExplorerOrderingFilter, SearchFilter]
    filterset_class = PersonFilter
    ordering_fields = [
        "name",
        "date_of_birth",
        "date_of_death",
        "media_count",
        "user_rating",
    ]
    ordering = ["name"]

    def get_queryset(self):
        qs = Person.objects.only(
            "id",
            "name",
            "profile_picture",
            "date_of_birth",
            "date_of_death",
            "is_actor",
            "is_director",
            "is_screenwriter",
            "is_musician",
            "is_book_author",
            "is_novelist",
            "is_comic_artist",
            "is_graphic_novelist",
            "is_original_music_composer",
            "is_tv_creator",
            "media_count",
        )
        return annotate_person_rating(qs)

