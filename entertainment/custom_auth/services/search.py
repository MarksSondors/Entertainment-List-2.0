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

    # The match has to be the `@@` operator, not the rank. Django's
    # `SearchRank(...) > 0` filter compiles to a function call the planner has
    # to evaluate row by row, so the full-text tier seq-scanned the whole
    # table - 73k people rows and ~290 ms per search - even with a GIN index
    # present. Matching the vector against the query compiles to `@@`, which is
    # the only form a GIN index over the tsvector can serve.
    #
    # `alias` rather than `annotate`: the tsvector is only needed in the WHERE
    # clause, and an annotation would add it to every selected row.
    fts_qs = (
        queryset.alias(search_vector=vector)
        .alias(rank=SearchRank(vector, query))
        .filter(search_vector=query)
        # "pk" breaks ties between equally ranked rows. Without it the order
        # depends on the plan (seq scan vs index scan), so the same query can
        # return the same rows in a different order - and paginated results
        # then repeat or drop rows.
        .order_by("-rank", "pk")
    )
    if fts_qs.exists():
        return fts_qs

    trigram_q = Q()
    similarity = None
    for field in fields:
        # Filtering on `trigram_similar` compiles to Postgres's `%` operator, which
        # can use a `gin_trgm_ops` index (e.g. `person_name_trgm_idx`). Filtering only
        # on `TrigramSimilarity(...) > 0.3` compiles to a function call that forces a
        # full seq scan over 73k+ rows.
        trigram_q |= Q(**{f"{field}__trigram_similar": term})
        component = TrigramSimilarity(field, term)
        similarity = component if similarity is None else similarity + component

    trigram_qs = (
        queryset.filter(trigram_q)
        .annotate(similarity=similarity)
        .filter(similarity__gt=trigram_threshold)
        # "pk" breaks ties between equally similar rows, so the same query
        # returns the same rows in the same order (paginated results then do
        # not repeat or drop rows).
        .order_by("-similarity", "pk")
    )
    if trigram_qs.exists():
        return trigram_qs

    icontains_q = Q()
    for field in fields:
        icontains_q |= Q(**{f"{field}__icontains": term})
    return queryset.filter(icontains_q).order_by("pk")
