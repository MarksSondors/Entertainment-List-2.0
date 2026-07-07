"""FilterSet for the Books explorer endpoint."""
from __future__ import annotations

import django_filters
from django.db.models import Q

from custom_auth.services.search import text_search
from explorer.filters import UserContextFilterMixin

from .models import Book


class BookFilter(UserContextFilterMixin):
    _content_type_model = Book

    q = django_filters.CharFilter(method="_filter_q")
    genres = django_filters.BaseInFilter(field_name="genres__id")
    publishers = django_filters.BaseInFilter(field_name="publishers__id")
    authors = django_filters.BaseInFilter(field_name="authors__id")
    series = django_filters.NumberFilter(field_name="series_id")
    language = django_filters.CharFilter(field_name="language", lookup_expr="iexact")

    year_min = django_filters.NumberFilter(field_name="published_date__year", lookup_expr="gte")
    year_max = django_filters.NumberFilter(field_name="published_date__year", lookup_expr="lte")
    user_rating_min = django_filters.NumberFilter(method="_filter_user_rating_min")
    user_rating_max = django_filters.NumberFilter(method="_filter_user_rating_max")
    user_rating_count_min = django_filters.NumberFilter(method="_filter_user_rating_count_min")
    user_rating_count_max = django_filters.NumberFilter(method="_filter_user_rating_count_max")
    pages_min = django_filters.NumberFilter(field_name="pages", lookup_expr="gte")
    pages_max = django_filters.NumberFilter(field_name="pages", lookup_expr="lte")

    has_cover = django_filters.BooleanFilter(method="_filter_has_cover")

    class Meta:
        model = Book
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

    def _filter_has_cover(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.exclude(image_url__isnull=True).exclude(image_url="") if value else queryset.filter(Q(image_url__isnull=True) | Q(image_url=""))
