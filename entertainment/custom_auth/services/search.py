"""Reusable PostgreSQL text search helper.

Wraps the three-tier fallback used by `search_bar_discover`:
    1. Full-text search (SearchVector + SearchRank) — fast, indexed.
    2. Trigram similarity — fuzzy, tolerant of typos.
    3. icontains OR chain — final fallback for very short/edge inputs.

Every layer returns the queryset with an annotated ordering column, and the
caller receives the queryset annotated + ordered so it can be paged/filtered
further by DRF.
"""
from __future__ import annotations

from typing import Iterable

from django.contrib.postgres.search import (
    SearchQuery,
    SearchRank,
    SearchVector,
    TrigramSimilarity,
)
from django.db.models import Q, QuerySet


TRIGRAM_THRESHOLD = 0.3


def text_search(
    queryset: QuerySet,
    fields: Iterable[str],
    term: str,
    *,
    config: str = "english",
    trigram_threshold: float = TRIGRAM_THRESHOLD,
) -> QuerySet:
    """Filter+rank a queryset with FTS → trigram → icontains fallback.

    The result is ordered by relevance. If `term` is empty the queryset is
    returned unchanged so callers can chain safely.
    """
    term = (term or "").strip()
    if not term:
        return queryset

    fields = list(fields)
    if not fields:
        return queryset.none()

    vector = SearchVector(*fields, config=config)
    query = SearchQuery(term, config=config)

    fts_qs = (
        queryset.annotate(rank=SearchRank(vector, query))
        .filter(rank__gt=0)
        .order_by("-rank")
    )
    if fts_qs.exists():
        return fts_qs

    similarity = None
    for field in fields:
        component = TrigramSimilarity(field, term)
        similarity = component if similarity is None else similarity + component

    trigram_qs = (
        queryset.annotate(similarity=similarity)
        .filter(similarity__gt=trigram_threshold)
        .order_by("-similarity")
    )
    if trigram_qs.exists():
        return trigram_qs

    icontains_q = Q()
    for field in fields:
        icontains_q |= Q(**{f"{field}__icontains": term})
    return queryset.filter(icontains_q)
