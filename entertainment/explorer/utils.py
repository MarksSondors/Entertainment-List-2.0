"""Shared helpers for explorer serializers/filters.

Kept small: mostly ContentType caching to avoid `ContentType.objects.get_for_model`
running on every list request.
"""
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count, FloatField, IntegerField, OuterRef, Subquery


_CT_CACHE: dict[type, int] = {}


def content_type_id(model_cls) -> int:
    """Return the ContentType PK for `model_cls`, cached at module level."""
    ct = _CT_CACHE.get(model_cls)
    if ct is None:
        ct = ContentType.objects.get_for_model(model_cls).pk
        _CT_CACHE[model_cls] = ct
    return ct


MEDIA_TYPES = {
    "movies": "movies",
    "tvshows": "tvshows",
    "books": "books",
    "games": "games",
    "people": "people",
}


def build_thumbnail(url: str | None, tmdb_size: str = "w342") -> str | None:
    """Downgrade a TMDB `original` poster URL to a smaller size for cards.

    Non-TMDB URLs (Hardcover, RAWG, SteamGridDB, gravatar, etc.) pass through
    unchanged.
    """
    if not url:
        return None
    if "image.tmdb.org/t/p/original" in url:
        return url.replace("/t/p/original", f"/t/p/{tmdb_size}")
    return url


def annotate_user_rating(queryset, model_cls):
    """Attach `user_rating` (avg) and `user_rating_count` to each row.

    Uses two correlated Subqueries so the whole list stays a single SELECT
    with no GROUP BY join. `Review` is defined in `custom_auth.models` and
    is generic — filtered by cached ContentType id here.
    """
    # Imported lazily to avoid a top-level cycle: custom_auth.models loads at
    # app-init time and imports from a lot of places.
    from custom_auth.models import Review

    ct_id = content_type_id(model_cls)
    base = Review.objects.filter(
        content_type_id=ct_id,
        object_id=OuterRef("pk"),
    ).values("object_id")

    return queryset.annotate(
        user_rating=Subquery(
            base.annotate(v=Avg("rating")).values("v")[:1],
            output_field=FloatField(),
        ),
        user_rating_count=Subquery(
            base.annotate(v=Count("id")).values("v")[:1],
            output_field=IntegerField(),
        ),
    )


def annotate_user_context(queryset, model_cls, user):
    """Attach `on_my_watchlist` and `reviewed_by_me` bool flags per row.

    Used to colour cards by engagement status. For anonymous users both
    flags evaluate to `False` (the subqueries stay empty).
    """
    from django.db.models import BooleanField, Exists, Value
    from custom_auth.models import Review, Watchlist

    if user is None or not user.is_authenticated:
        return queryset.annotate(
            on_my_watchlist=Value(False, output_field=BooleanField()),
            reviewed_by_me=Value(False, output_field=BooleanField()),
        )

    ct_id = content_type_id(model_cls)
    wl = Watchlist.objects.filter(
        user=user,
        content_type_id=ct_id,
        object_id=OuterRef("pk"),
    )
    rv = Review.objects.filter(
        user=user,
        content_type_id=ct_id,
        object_id=OuterRef("pk"),
    )
    return queryset.annotate(
        on_my_watchlist=Exists(wl),
        reviewed_by_me=Exists(rv),
    )


def annotate_person_rating(queryset):
    """Attach aggregated `user_rating` and `user_rating_count` to each Person.

    Definition: average / count of every user Review across every media
    item (Movie / TVShow / Book / Game — anything MediaPerson links to)
    this person appears in.

    Implemented via two correlated RawSQL subqueries so we don't have to
    juggle Django's nested-OuterRef traversal across a GenericForeignKey.
    """
    from django.db.models import FloatField, IntegerField
    from django.db.models.expressions import RawSQL

    # NB: `custom_auth_person.id` is safe to inline — a Person queryset
    # always has this column, and RawSQL runs as a correlated subquery
    # against the outer Person table.
    exists_clause = """
        EXISTS (
            SELECT 1 FROM custom_auth_mediaperson mp
            WHERE mp.person_id = custom_auth_person.id
              AND mp.content_type_id = r.content_type_id
              AND mp.object_id = r.object_id
        )
    """
    avg_sql = f"(SELECT AVG(r.rating) FROM custom_auth_review r WHERE {exists_clause})"
    cnt_sql = f"(SELECT COUNT(*) FROM custom_auth_review r WHERE {exists_clause})"

    return queryset.annotate(
        user_rating=RawSQL(avg_sql, [], output_field=FloatField()),
        user_rating_count=RawSQL(cnt_sql, [], output_field=IntegerField()),
    )




