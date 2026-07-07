"""Shared filter mixins for explorer list endpoints.

`UserContextFilterMixin` adds three cross-media filters that operate on the
generic `Watchlist` / `Review` tables:

* `on_watchlist=true`  — restrict to items in the current user's watchlist
* `reviewed_by_me=true` — restrict to items the current user has reviewed
* `my_rating_min` / `my_rating_max` — restrict by the current user's rating

Each concrete FilterSet must set `_content_type_model` (the media model class)
so the mixin knows which ContentType to look up. When the request is anonymous
these filters are silently ignored — they never leak other users' data because
the underlying subquery always includes `user=request.user`.
"""
from __future__ import annotations

import django_filters
from django.db.models import Exists, OuterRef

from custom_auth.models import Review, Watchlist

from .utils import content_type_id


class UserContextFilterMixin(django_filters.FilterSet):
    on_watchlist = django_filters.BooleanFilter(method="_filter_on_watchlist")
    reviewed_by_me = django_filters.BooleanFilter(method="_filter_reviewed_by_me")
    my_rating_min = django_filters.NumberFilter(method="_filter_my_rating_min")
    my_rating_max = django_filters.NumberFilter(method="_filter_my_rating_max")

    #: subclasses override with the media model class (e.g. Movie, Book, Game)
    _content_type_model = None

    def _current_user(self):
        request = getattr(self, "request", None)
        if request is None:
            return None
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return None
        return user

    def _filter_on_watchlist(self, queryset, name, value):
        user = self._current_user()
        if user is None or value is None:
            return queryset
        subquery = Watchlist.objects.filter(
            user=user,
            content_type_id=content_type_id(self._content_type_model),
            object_id=OuterRef("pk"),
        )
        annotated = queryset.annotate(_on_watchlist=Exists(subquery))
        return annotated.filter(_on_watchlist=bool(value))

    def _filter_reviewed_by_me(self, queryset, name, value):
        user = self._current_user()
        if user is None or value is None:
            return queryset
        subquery = Review.objects.filter(
            user=user,
            content_type_id=content_type_id(self._content_type_model),
            object_id=OuterRef("pk"),
        )
        annotated = queryset.annotate(_reviewed_by_me=Exists(subquery))
        return annotated.filter(_reviewed_by_me=bool(value))

    def _filter_my_rating_min(self, queryset, name, value):
        return self._apply_my_rating(queryset, min_value=value)

    def _filter_my_rating_max(self, queryset, name, value):
        return self._apply_my_rating(queryset, max_value=value)

    def _apply_my_rating(self, queryset, *, min_value=None, max_value=None):
        user = self._current_user()
        if user is None or (min_value is None and max_value is None):
            return queryset
        review_filter = {"user": user, "content_type_id": content_type_id(self._content_type_model)}
        if min_value is not None:
            review_filter["rating__gte"] = min_value
        if max_value is not None:
            review_filter["rating__lte"] = max_value
        matching_ids = Review.objects.filter(
            object_id=OuterRef("pk"),
            **review_filter,
        )
        return queryset.annotate(_my_rating_match=Exists(matching_ids)).filter(
            _my_rating_match=True
        )
