"""FilterSet for the Movies explorer endpoint."""
from __future__ import annotations

import django_filters
from django.db.models import Q

from custom_auth.services.search import text_search
from explorer.filters import UserContextFilterMixin

from .models import Movie


class MovieFilter(UserContextFilterMixin):
    _content_type_model = Movie

    q = django_filters.CharFilter(method="_filter_q")
    genres = django_filters.BaseInFilter(field_name="genres__id")
    countries = django_filters.BaseInFilter(field_name="countries__id")
    keywords = django_filters.BaseInFilter(field_name="keywords__id")
    collection = django_filters.NumberFilter(field_name="collection_id")

    year_min = django_filters.NumberFilter(field_name="release_date__year", lookup_expr="gte")
    year_max = django_filters.NumberFilter(field_name="release_date__year", lookup_expr="lte")
    user_rating_min = django_filters.NumberFilter(method="_filter_user_rating_min")
    user_rating_max = django_filters.NumberFilter(method="_filter_user_rating_max")
    user_rating_count_min = django_filters.NumberFilter(method="_filter_user_rating_count_min")
    user_rating_count_max = django_filters.NumberFilter(method="_filter_user_rating_count_max")
    runtime_min = django_filters.NumberFilter(field_name="runtime", lookup_expr="gte")
    runtime_max = django_filters.NumberFilter(field_name="runtime", lookup_expr="lte")

    is_anime = django_filters.BooleanFilter(field_name="is_anime")
    status = django_filters.CharFilter(field_name="status", lookup_expr="iexact")
    has_poster = django_filters.BooleanFilter(method="_filter_has_poster")

    class Meta:
        model = Movie
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
