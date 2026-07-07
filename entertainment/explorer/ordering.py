"""Custom DRF ordering filter with NULL-tolerant sorting.

`user_rating` is a Subquery / RawSQL annotation that is NULL for items
nobody has reviewed. Vanilla PostgreSQL sorts NULLs FIRST on DESC and
LAST on ASC, so `?ordering=-user_rating` would surface the *unrated*
items at the top — the opposite of what a "best rated first" sort
should do.

This filter intercepts any ordering key that ends in `user_rating`
(the field name is fixed by convention across all explorer viewsets)
and applies `F(...).desc(nulls_last=True)` / `.asc(nulls_last=True)`
so unrated items always sink to the bottom.
"""
from __future__ import annotations

from django.db.models import F
from rest_framework.filters import OrderingFilter


class ExplorerOrderingFilter(OrderingFilter):
    NULLS_LAST_FIELDS = {"user_rating", "user_rating_count"}

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset

        expressions = []
        for term in ordering:
            key = term.lstrip("-")
            if key in self.NULLS_LAST_FIELDS:
                expr = F(key)
                expressions.append(
                    expr.desc(nulls_last=True) if term.startswith("-")
                    else expr.asc(nulls_last=True)
                )
            else:
                expressions.append(term)
        return queryset.order_by(*expressions)
