"""Statistics service for aggregating user review data across media types.

Architecture: all reviews and model data are fetched once per media type
(~5 DB queries each), then every stat is computed purely in-memory.
Per-year breakdowns reuse the same prefetched data with in-memory filtering.
"""

import logging
from collections import defaultdict
from django.core.cache import cache
from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum
from django.db.models.functions import ExtractYear, TruncMonth

from custom_auth.models import Review, MediaPerson
from movies.models import Movie
from tvshows.models import TVShow, WatchedEpisode
from games.models import Game

logger = logging.getLogger(__name__)

STATS_CACHE_TIMEOUT = 60 * 60 * 24  # 24 hours


def _stats_cache_key(user_id):
    """Generate cache key for a user's statistics."""
    return f'stats_user_{user_id}'


def invalidate_stats_cache(user_id):
    """Delete cached statistics for a user. Called on review create/update/delete."""
    key = _stats_cache_key(user_id)
    cache.delete(key)
    logger.debug(f'Invalidated stats cache for user {user_id}')


def _get_content_types():
    """Return a dict of content types for movies, TV shows, and games."""
    return {
        'movies': ContentType.objects.get_for_model(Movie),
        'tvshows': ContentType.objects.get_for_model(TVShow),
        'games': ContentType.objects.get_for_model(Game),
    }


# ── One-time data fetching (per media type) ──

def _fetch_all_reviews(user, content_type):
    """Single DB query: all review rows for a user + content_type."""
    return list(
        Review.objects.filter(user=user, content_type=content_type)
        .values('object_id', 'rating', 'date_added')
    )


def _build_review_context(all_reviews, year=None):
    """Build rating_map and object_ids from pre-fetched reviews, optionally year-filtered."""
    reviews = all_reviews if year is None else [
        r for r in all_reviews if r['date_added'].year == year
    ]
    rating_map = defaultdict(list)
    for r in reviews:
        rating_map[r['object_id']].append(float(r['rating']))
    return {
        'reviews': reviews,
        'rating_map': dict(rating_map),
        'object_ids': list(rating_map.keys()),
    }


def _aggregate_tvshow_reviews(ctx):
    """Collapse per-season/subgroup TV reviews into one entry per show."""
    show_data = defaultdict(lambda: {'ratings': [], 'latest_date': None})
    for r in ctx['reviews']:
        entry = show_data[r['object_id']]
        entry['ratings'].append(float(r['rating']))
        if entry['latest_date'] is None or r['date_added'] > entry['latest_date']:
            entry['latest_date'] = r['date_added']
    return [
        {
            'object_id': oid,
            'rating': sum(d['ratings']) / len(d['ratings']),
            'date_added': d['latest_date'],
        }
        for oid, d in show_data.items()
    ]


def _fetch_media_data(content_type, object_ids, user):
    """Fetch all model-level data in bulk: genres, release years, runtimes, people."""
    model_class = content_type.model_class()
    data = {
        'genre_map': {},            # {item_id: [genre_name, ...]}
        'release_years': {},        # {item_id: year}
        'runtimes': {},             # {item_id: minutes}  (movies only)
        'tv_minutes_by_show': {},   # {show_id: total_minutes}
        'tv_watch_monthly': [],     # [{'month': datetime, 'total_mins': int}, ...]
        'people': [],               # [(object_id, role, person_name), ...]
    }
    if not object_ids:
        return data

    # Single query for items: genres (prefetch) + release year (annotation)
    date_field = 'first_air_date' if model_class == TVShow else 'release_date'
    items = (
        model_class.objects
        .filter(id__in=object_ids)
        .prefetch_related('genres')
        .annotate(release_year=ExtractYear(date_field))
    )
    for item in items:
        data['genre_map'][item.id] = [g.name for g in item.genres.all()]
        if item.release_year is not None:
            data['release_years'][item.id] = item.release_year
        if model_class == Movie:
            data['runtimes'][item.id] = item.runtime

    # TV-specific: per-show watched minutes + monthly watch time
    if model_class == TVShow:
        watched_by_show = (
            WatchedEpisode.objects.filter(
                user=user,
                episode__season__show_id__in=object_ids,
                episode__runtime__isnull=False,
            )
            .values('episode__season__show_id')
            .annotate(total=Sum('episode__runtime'))
        )
        data['tv_minutes_by_show'] = {
            row['episode__season__show_id']: row['total']
            for row in watched_by_show
        }
        data['tv_watch_monthly'] = list(
            WatchedEpisode.objects.filter(
                user=user,
                episode__runtime__isnull=False,
            )
            .annotate(month=TruncMonth('watched_date'))
            .values('month')
            .annotate(total_mins=Sum('episode__runtime'))
            .order_by('month')
        )

    # People: actors + directors (movies and TV only)
    if model_class != Game:
        data['people'] = list(
            MediaPerson.objects.filter(
                content_type=content_type,
                object_id__in=object_ids,
                role__in=['Actor', 'Director'],
            )
            .select_related('person')
            .values_list('object_id', 'role', 'person__name')
        )

    return data


# ── Pure in-memory stat computations ──

def _compute_rating_distribution(ctx, is_tvshow):
    """Count of items grouped by integer rating (0-10)."""
    distribution = {str(i): 0 for i in range(11)}
    if is_tvshow:
        for agg in _aggregate_tvshow_reviews(ctx):
            key = str(int(agg['rating']))
            if key in distribution:
                distribution[key] += 1
    else:
        for ratings in ctx['rating_map'].values():
            key = str(int(ratings[0]))
            if key in distribution:
                distribution[key] += 1
    return distribution


def _compute_ratings_over_time(ctx, is_tvshow):
    """Each item as {date, rating} sorted by date."""
    if is_tvshow:
        aggregated = _aggregate_tvshow_reviews(ctx)
        return sorted(
            [
                {'date': a['date_added'].date().isoformat(), 'rating': round(a['rating'], 1)}
                for a in aggregated
            ],
            key=lambda x: x['date'],
        )
    return sorted(
        [
            {'date': r['date_added'].date().isoformat(), 'rating': float(r['rating'])}
            for r in ctx['reviews']
        ],
        key=lambda x: x['date'],
    )


def _compute_release_year_distribution(ctx, media_data):
    """Count of rated items grouped by their release year."""
    year_counts = defaultdict(int)
    for oid in ctx['object_ids']:
        yr = media_data['release_years'].get(oid)
        if yr is not None:
            year_counts[yr] += 1
    return {str(yr): count for yr, count in sorted(year_counts.items())}


def _compute_avg_rating_by_release_year(ctx, media_data):
    """Average user rating grouped by the media's release year."""
    year_ratings = defaultdict(list)
    for oid in ctx['object_ids']:
        yr = media_data['release_years'].get(oid)
        if yr is None:
            continue
        ratings = ctx['rating_map'][oid]
        year_ratings[yr].append(sum(ratings) / len(ratings))
    result = {
        str(yr): round(sum(r) / len(r), 2)
        for yr, r in year_ratings.items()
    }
    return dict(sorted(result.items(), key=lambda x: int(x[0])))


def _compute_count_by_genre(ctx, media_data):
    """Count of rated items per genre."""
    genre_counts = defaultdict(int)
    for oid in ctx['object_ids']:
        for genre in media_data['genre_map'].get(oid, []):
            genre_counts[genre] += 1
    return dict(sorted(genre_counts.items(), key=lambda x: x[1], reverse=True))


def _compute_avg_rating_by_genre(ctx, media_data):
    """Average user rating per genre."""
    genre_ratings = defaultdict(list)
    for oid in ctx['object_ids']:
        ratings = ctx['rating_map'].get(oid, [])
        if not ratings:
            continue
        avg_item = sum(ratings) / len(ratings)
        for genre in media_data['genre_map'].get(oid, []):
            genre_ratings[genre].append(avg_item)
    result = {
        name: round(sum(r) / len(r), 2)
        for name, r in genre_ratings.items()
    }
    return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))


def _compute_summary(ctx, media_data, model_class, is_tvshow):
    """Summary: total rated, total hours, avg rating."""
    if is_tvshow:
        aggregated = _aggregate_tvshow_reviews(ctx)
        total_rated = len(aggregated)
        avg_rating = (
            round(sum(a['rating'] for a in aggregated) / len(aggregated), 2)
            if aggregated else 0
        )
        total_minutes = sum(
            media_data['tv_minutes_by_show'].get(a['object_id'], 0)
            for a in aggregated
        )
    else:
        total_rated = len(ctx['object_ids'])
        all_ratings = [r for ratings in ctx['rating_map'].values() for r in ratings]
        avg_rating = round(sum(all_ratings) / len(all_ratings), 2) if all_ratings else 0
        if model_class == Movie:
            total_minutes = sum(
                media_data['runtimes'].get(oid) or 0 for oid in ctx['object_ids']
            )
        else:
            total_minutes = 0

    total_hours = round(total_minutes / 60, 1) if total_minutes else 0
    return {
        'total_rated': total_rated,
        'total_hours': total_hours,
        'avg_rating': avg_rating,
    }


def _compute_watch_time_by_month(ctx, media_data, model_class, year=None):
    """Total runtime (hours) grouped by month."""
    if model_class == Game:
        return {}

    if model_class == Movie:
        month_minutes = defaultdict(int)
        for r in ctx['reviews']:
            key = r['date_added'].strftime('%Y-%m')
            month_minutes[key] += media_data['runtimes'].get(r['object_id']) or 0
        return [
            {'month': m, 'hours': round(mins / 60, 1)}
            for m, mins in sorted(month_minutes.items())
        ]

    if model_class == TVShow:
        monthly = media_data['tv_watch_monthly']
        if year is not None:
            monthly = [row for row in monthly if row['month'].year == year]
        return [
            {'month': row['month'].strftime('%Y-%m'), 'hours': round(row['total_mins'] / 60, 1)}
            for row in monthly if row['month']
        ]

    return {}


def _compute_top_people(ctx, media_data, role, sort_by='count', limit=10, min_count=1):
    """Top people by count or by average rating for a given role."""
    oid_set = set(ctx['object_ids'])
    entries = [
        (oid, name) for oid, r, name in media_data['people']
        if r == role and oid in oid_set
    ]

    person_data = defaultdict(lambda: {'count': 0, 'ratings': []})
    for oid, name in entries:
        person_data[name]['count'] += 1
        ratings = ctx['rating_map'].get(oid, [])
        if ratings:
            person_data[name]['ratings'].append(sum(ratings) / len(ratings))

    if sort_by == 'count':
        sorted_people = sorted(
            person_data.items(), key=lambda x: x[1]['count'], reverse=True
        )[:limit]
    else:
        qualified = [
            (name, info) for name, info in person_data.items()
            if info['count'] >= min_count and info['ratings']
        ]
        sorted_people = sorted(
            qualified,
            key=lambda x: sum(x[1]['ratings']) / len(x[1]['ratings']),
            reverse=True,
        )[:limit]

    return [
        {
            'name': name,
            'count': info['count'],
            'avg_rating': round(sum(info['ratings']) / len(info['ratings']), 2) if info['ratings'] else 0,
        }
        for name, info in sorted_people
    ]


# ── Orchestration ──

def _compute_all_for_slice(ctx, media_data, content_type, year=None):
    """Compute every stat for a single time-slice (all-time or a specific year)."""
    model_class = content_type.model_class()
    is_tvshow = model_class == TVShow
    is_game = model_class == Game

    return {
        'summary': _compute_summary(ctx, media_data, model_class, is_tvshow),
        'rating_distribution': _compute_rating_distribution(ctx, is_tvshow),
        'ratings_over_time': _compute_ratings_over_time(ctx, is_tvshow),
        'release_year_distribution': _compute_release_year_distribution(ctx, media_data),
        'avg_rating_by_release_year': _compute_avg_rating_by_release_year(ctx, media_data),
        'count_by_genre': _compute_count_by_genre(ctx, media_data),
        'avg_rating_by_genre': _compute_avg_rating_by_genre(ctx, media_data),
        'watch_time_by_month': _compute_watch_time_by_month(ctx, media_data, model_class, year),
        'top_directors': (
            _compute_top_people(ctx, media_data, 'Director', sort_by='count')
            if model_class == Movie else []
        ),
        'top_directors_by_rating': (
            _compute_top_people(ctx, media_data, 'Director', sort_by='rating', min_count=2)
            if model_class == Movie else []
        ),
        'top_actors': (
            _compute_top_people(ctx, media_data, 'Actor', sort_by='count')
            if not is_game else []
        ),
        'top_actors_by_rating': (
            _compute_top_people(ctx, media_data, 'Actor', sort_by='rating', min_count=2)
            if not is_game else []
        ),
    }


def get_all_stats(user):
    """Compute all statistics for all media types. Cached per user."""
    cache_key = _stats_cache_key(user.id)
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug(f'Stats cache hit for user {user.id}')
        return cached

    logger.debug(f'Stats cache miss for user {user.id}, computing...')
    content_types = _get_content_types()
    result = {}

    for media_key, ct in content_types.items():
        # 1. Fetch all reviews once (1 DB query)
        all_reviews = _fetch_all_reviews(user, ct)

        # 2. Build all-time context (in-memory)
        ctx = _build_review_context(all_reviews)

        # 3. Available years from in-memory data
        available_years = sorted(
            {r['date_added'].year for r in all_reviews},
            reverse=True,
        )

        # 4. Fetch model-level data once (~2-5 DB queries)
        media_data = _fetch_media_data(ct, ctx['object_ids'], user)

        # 5. Compute all-time stats (pure in-memory)
        stats = _compute_all_for_slice(ctx, media_data, ct)
        stats['years'] = available_years
        stats['total_reviews'] = len(ctx['object_ids'])

        # 6. Compute per-year stats by filtering in-memory
        yearly_data = {}
        for y in available_years:
            year_ctx = _build_review_context(all_reviews, year=y)
            yearly_data[str(y)] = _compute_all_for_slice(year_ctx, media_data, ct, year=y)
        stats['yearly'] = yearly_data

        result[media_key] = stats

    cache.set(cache_key, result, STATS_CACHE_TIMEOUT)
    logger.debug(f'Cached stats for user {user.id}')
    return result
