"""FilterSet for the People explorer endpoint.

People are queried very differently from media: they have low-cardinality
boolean role flags (is_actor, is_director, etc.) and a name to fuzzy-match on.
Watchlist/reviewed-by-me filters don't apply — Person is not a media type.
"""
from __future__ import annotations

import django_filters

from .models import Person
from .services.search import text_search


class PersonFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(method="_filter_q")

    is_actor = django_filters.BooleanFilter(field_name="is_actor")
    is_director = django_filters.BooleanFilter(field_name="is_director")
    is_screenwriter = django_filters.BooleanFilter(field_name="is_screenwriter")
    is_musician = django_filters.BooleanFilter(field_name="is_musician")
    is_book_author = django_filters.BooleanFilter(field_name="is_book_author")
    is_novelist = django_filters.BooleanFilter(field_name="is_novelist")
    is_comic_artist = django_filters.BooleanFilter(field_name="is_comic_artist")
    is_graphic_novelist = django_filters.BooleanFilter(field_name="is_graphic_novelist")
    is_original_music_composer = django_filters.BooleanFilter(field_name="is_original_music_composer")
    is_tv_creator = django_filters.BooleanFilter(field_name="is_tv_creator")

    born_year_min = django_filters.NumberFilter(field_name="date_of_birth__year", lookup_expr="gte")
    born_year_max = django_filters.NumberFilter(field_name="date_of_birth__year", lookup_expr="lte")

    user_rating_min = django_filters.NumberFilter(method="_filter_user_rating_min")
    user_rating_max = django_filters.NumberFilter(method="_filter_user_rating_max")
    user_rating_count_min = django_filters.NumberFilter(method="_filter_user_rating_count_min")
    user_rating_count_max = django_filters.NumberFilter(method="_filter_user_rating_count_max")

    alive = django_filters.BooleanFilter(method="_filter_alive")
    has_profile_picture = django_filters.BooleanFilter(method="_filter_has_profile_picture")

    class Meta:
        model = Person
        fields: list[str] = []

    def _filter_q(self, queryset, name, value):
        return text_search(queryset, ["name"], value)

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

    def _filter_alive(self, queryset, name, value):
        if value is None:
            return queryset
        if value:
            return queryset.filter(date_of_death__isnull=True)
        return queryset.filter(date_of_death__isnull=False)

    def _filter_has_profile_picture(self, queryset, name, value):
        if value is None:
            return queryset
        if value:
            return queryset.exclude(profile_picture__isnull=True).exclude(profile_picture="")
        return queryset.filter(profile_picture__isnull=True) | queryset.filter(profile_picture="")
