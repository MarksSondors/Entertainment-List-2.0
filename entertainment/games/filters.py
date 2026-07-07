"""FilterSet for the Games explorer endpoint."""
from __future__ import annotations

import django_filters
from django.db.models import Q

from custom_auth.services.search import text_search
from explorer.filters import UserContextFilterMixin

from .models import Game


class GameFilter(UserContextFilterMixin):
    _content_type_model = Game

    q = django_filters.CharFilter(method="_filter_q")
    genres = django_filters.BaseInFilter(field_name="genres__id")
    platforms = django_filters.BaseInFilter(field_name="platforms__id")
    developers = django_filters.BaseInFilter(field_name="developers__id")
    publishers = django_filters.BaseInFilter(field_name="publishers__id")
    game_collection = django_filters.NumberFilter(field_name="game_collection_id")

    year_min = django_filters.NumberFilter(field_name="release_date__year", lookup_expr="gte")
    year_max = django_filters.NumberFilter(field_name="release_date__year", lookup_expr="lte")
    user_rating_min = django_filters.NumberFilter(method="_filter_user_rating_min")
    user_rating_max = django_filters.NumberFilter(method="_filter_user_rating_max")
    user_rating_count_min = django_filters.NumberFilter(method="_filter_user_rating_count_min")
    user_rating_count_max = django_filters.NumberFilter(method="_filter_user_rating_count_max")
    metacritic_min = django_filters.NumberFilter(field_name="metacritic", lookup_expr="gte")
    metacritic_max = django_filters.NumberFilter(field_name="metacritic", lookup_expr="lte")

    has_poster = django_filters.BooleanFilter(method="_filter_has_poster")

    class Meta:
        model = Game
        fields: list[str] = []

    def _filter_q(self, queryset, name, value):
        return text_search(queryset, ["title", "original_title"], value)

    def _filter_user_rating_min(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(user_rating__gte=value)

    def _filter_user_rating_max(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(user_rating__lte=value)

    def _filter_user_rating_count_min(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(user_rating_count__gte=value)

    def _filter_user_rating_count_max(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(user_rating_count__lte=value)

    def _filter_has_poster(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.exclude(poster__isnull=True).exclude(poster="") if value else queryset.filter(Q(poster__isnull=True) | Q(poster=""))
