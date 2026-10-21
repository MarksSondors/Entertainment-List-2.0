"""Statistics service for aggregating user review data across media types.

Reviews and metadata are fetched in bulk for movies, shows, games, books and
albums. Year slices reuse that data. Ratings follow local review dates; TV
runtime follows episode watch dates, independently of whether a show is rated.
"""

import logging
from collections import defaultdict
from statistics import median
from django.core.cache import cache
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Sum
from django.db.models.functions import ExtractYear, TruncMonth
from django.utils import timezone

from custom_auth.models import Review, MediaPerson, Watchlist, WatchlistEvent
from movies.models import Movie
from tvshows.models import TVShow, WatchedEpisode
from games.models import Game
from books.models import Book
from music.models import Album

logger = logging.getLogger(__name__)

STATS_CACHE_TIMEOUT = 60 * 60 * 24  # 24 hours


def _stats_cache_key(user_id):
    """Generate cache key for a user's statistics."""
    return f'stats_v2_user_{user_id}'


def invalidate_stats_cache(user_id):
    """Delete cached statistics after reviews, watchlists or episode watches change."""
    key = _stats_cache_key(user_id)
    cache.delete(key)
    logger.debug(f'Invalidated stats cache for user {user_id}')


def _get_content_types():
    """Return content types for all supported reviewable media."""
    return {
        'movies': ContentType.objects.get_for_model(Movie),
        'tvshows': ContentType.objects.get_for_model(TVShow),
        'games': ContentType.objects.get_for_model(Game),
        'books': ContentType.objects.get_for_model(Book),
        'music': ContentType.objects.get_for_model(Album),
    }


# ── One-time data fetching (per media type) ──

def _fetch_all_reviews(user, content_type):
    """Single DB query: all review rows for a user + content_type."""
    rows = list(
        Review.objects.filter(user=user, content_type=content_type)
        .values('object_id', 'rating', 'date_added')
    )
    for row in rows:
        row['date_added'] = timezone.localtime(row['date_added'])
    return rows


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
        'genre_ids': {},            # {item_id: [genre_id, ...]} for explorer links
        'release_years': {},        # {item_id: year}
        'runtimes': {},             # {item_id: minutes} (movies and albums)
        'tv_watch_monthly': [],     # [{'month': datetime, 'total_mins': int}, ...]
        'people': [],               # [(object_id, role, person_name, person_id), ...]
        'country_map': {},          # {item_id: [country_name, ...]} (movies/tv)
    }
    if model_class == TVShow:
        data['tv_watch_monthly'] = list(
            WatchedEpisode.objects.filter(user=user, episode__runtime__isnull=False)
            .annotate(month=TruncMonth('watched_date'))
            .values('month')
            .annotate(total_mins=Sum('episode__runtime'))
            .order_by('month')
        )
    if not object_ids:
        return data

    # Single query for items: genres (prefetch) + release year (annotation)
    date_field = {
        TVShow: 'first_air_date', Book: 'published_date',
    }.get(model_class, 'release_date')
    has_countries = model_class in (Movie, TVShow)
    items = (
        model_class.objects
        .filter(id__in=object_ids)
        .prefetch_related('genres', *(('countries',) if has_countries else ()))
        .annotate(release_year=ExtractYear(date_field))
    )
    for item in items:
        data['genre_map'][item.id] = [g.name for g in item.genres.all()]
        if item.genres.all():
            data['genre_ids'][item.id] = [g.id for g in item.genres.all()]
        if item.release_year is not None:
            data['release_years'][item.id] = item.release_year
        if model_class in (Movie, Album):
            data['runtimes'][item.id] = item.runtime
        if has_countries:
            data['country_map'][item.id] = [c.name for c in item.countries.all()]

    if model_class in (Movie, TVShow, Book):
        data['people'] = list(
            MediaPerson.objects.order_by().filter(
                content_type=content_type,
                object_id__in=object_ids,
                role__in=['Actor', 'Director', 'Author'],
            )
            .select_related('person')
            .values_list('object_id', 'role', 'person__name', 'person_id')
        )
        if model_class == Book:
            data['people'].extend(
                (book.pk, 'Author', author.name, author.pk)
                for book in Book.objects.filter(pk__in=object_ids).prefetch_related('authors')
                for author in book.authors.all()
            )
    elif model_class == Album:
        albums = Album.objects.filter(id__in=object_ids).select_related(
            'primary_artist',
        ).prefetch_related('featured_artists')
        data['people'] = [
            (album.id, 'Artist', person.name, person.id)
            for album in albums
            for person in [album.primary_artist, *album.featured_artists.all()]
        ]

    return data


# ── Pure in-memory stat computations ──

def _compute_rating_distribution(ctx, is_tvshow):
    """Count titles in [n, n+1) rating bins, with an exact-10 final bin."""
    distribution = {str(i): 0 for i in range(11)}
    if is_tvshow:
        for agg in _aggregate_tvshow_reviews(ctx):
            key = str(int(agg['rating']))
            if key in distribution:
                distribution[key] += 1
    else:
        for ratings in ctx['rating_map'].values():
            key = str(int(sum(ratings) / len(ratings)))
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


def _compute_decade_distribution(ctx, media_data):
    """Count of rated items grouped by release decade (1960s, 1970s, ...)."""
    decade_counts = defaultdict(int)
    for oid in ctx['object_ids']:
        yr = media_data['release_years'].get(oid)
        if yr is not None:
            decade_counts[(yr // 10) * 10] += 1
    return {f"{d}s": count for d, count in sorted(decade_counts.items())}


def _compute_count_by_country(ctx, media_data):
    """Count of rated items grouped by production country (movies/tv only)."""
    country_counts = defaultdict(int)
    for oid in ctx['object_ids']:
        for name in media_data['country_map'].get(oid, []):
            country_counts[name] += 1
    return dict(sorted(country_counts.items(), key=lambda x: x[1], reverse=True))


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
    """Count of rated items per genre. Values are [count, genre_id] so the
    template can deep-link into the explorer filtered by genre."""
    genre_counts = defaultdict(lambda: [0, None])
    for oid in ctx['object_ids']:
        names = media_data['genre_map'].get(oid, [])
        ids = media_data.get('genre_ids', {}).get(oid, [])
        for i, genre in enumerate(names):
            gid = ids[i] if i < len(ids) else None
            genre_counts[genre][0] += 1
            if genre_counts[genre][1] is None:
                genre_counts[genre][1] = gid
    return dict(sorted(genre_counts.items(), key=lambda x: x[1][0], reverse=True))


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


def _compute_summary(ctx, media_data, model_class, is_tvshow, year=None):
    """One vote per title; TV time follows actual episode watch dates."""
    if is_tvshow:
        aggregated = _aggregate_tvshow_reviews(ctx)
        total_rated = len(aggregated)
        avg_rating = (
            round(sum(a['rating'] for a in aggregated) / len(aggregated), 2)
            if aggregated else 0
        )
        total_minutes = sum(
            row['total_mins'] for row in media_data['tv_watch_monthly']
            if row['month'] and (year is None or row['month'].year == year)
        )
    else:
        total_rated = len(ctx['object_ids'])
        all_ratings = [sum(ratings) / len(ratings) for ratings in ctx['rating_map'].values()]
        avg_rating = round(sum(all_ratings) / len(all_ratings), 2) if all_ratings else 0
        if model_class in (Movie, Album):
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
        'review_count': len(ctx['reviews']),
        'active_days': len({r['date_added'].date() for r in ctx['reviews']}),
        'median_rating': round(median([
            sum(ratings) / len(ratings) for ratings in ctx['rating_map'].values()
        ]), 2) if ctx['rating_map'] else None,
        'runtime_known': sum(bool(media_data['runtimes'].get(oid)) for oid in ctx['object_ids']),
    }


def _compute_watch_time_by_month(ctx, media_data, model_class, year=None):
    """Total runtime (hours) grouped by month."""
    if model_class in (Movie, Album):
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

    return []


def _compute_top_people(ctx, media_data, role, sort_by='count', limit=10, min_count=1):
    """Top people by count or by average rating for a given role."""
    oid_set = set(ctx['object_ids'])
    entries = [
        (oid, name, pid) for oid, r, name, pid in media_data['people']
        if r == role and oid in oid_set
    ]

    person_data = defaultdict(lambda: {'count': 0, 'ratings': [], 'person_id': None, 'name': ''})
    seen = set()
    for oid, name, pid in entries:
        if (pid, oid) in seen:
            continue
        seen.add((pid, oid))
        person_data[pid]['count'] += 1
        person_data[pid]['person_id'] = pid
        person_data[pid]['name'] = name
        ratings = ctx['rating_map'].get(oid, [])
        if ratings:
            person_data[pid]['ratings'].append(sum(ratings) / len(ratings))

    people = sorted(person_data.items(), key=lambda entry: (entry[1]['name'].casefold(), entry[0]))

    if sort_by == 'count':
        sorted_people = sorted(
            people, key=lambda x: x[1]['count'], reverse=True
        )[:limit]
    else:
        qualified = [
            (pid, info) for pid, info in people
            if info['count'] >= min_count and info['ratings']
        ]
        sorted_people = sorted(
            qualified,
            key=lambda x: sum(x[1]['ratings']) / len(x[1]['ratings']),
            reverse=True,
        )[:limit]

    return [
        {
            'name': info['name'],
            'count': info['count'],
            'avg_rating': round(sum(info['ratings']) / len(info['ratings']), 2) if info['ratings'] else 0,
            'person_id': info['person_id'],
        }
        for _, info in sorted_people
    ]


# ── Orchestration ──

def _compute_all_for_slice(ctx, media_data, content_type, year=None):
    """Compute every stat for a single time-slice (all-time or a specific year)."""
    model_class = content_type.model_class()
    is_tvshow = model_class == TVShow
    is_game = model_class == Game

    return {
        'summary': _compute_summary(ctx, media_data, model_class, is_tvshow, year),
        'activity_by_month': _compute_activity(ctx),
        'rating_distribution': _compute_rating_distribution(ctx, is_tvshow),
        'ratings_over_time': _compute_ratings_over_time(ctx, is_tvshow),
        'release_year_distribution': _compute_release_year_distribution(ctx, media_data),
        'avg_rating_by_release_year': _compute_avg_rating_by_release_year(ctx, media_data),
        'decade_distribution': _compute_decade_distribution(ctx, media_data),
        'count_by_genre': _compute_count_by_genre(ctx, media_data),
        'avg_rating_by_genre': _compute_avg_rating_by_genre(ctx, media_data),
        'count_by_country': (
            _compute_count_by_country(ctx, media_data)
            if model_class in (Movie, TVShow) else {}
        ),
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
        'top_creators': _compute_top_people(
            ctx, media_data, 'Author' if model_class == Book else 'Artist',
        ) if model_class in (Book, Album) else [],
        'top_creators_by_rating': _compute_top_people(
            ctx, media_data, 'Author' if model_class == Book else 'Artist',
            sort_by='rating', min_count=2,
        ) if model_class in (Book, Album) else [],
    }


def _compute_activity(ctx):
    """Monthly review activity, distinguishing review entries from distinct titles."""
    monthly = defaultdict(lambda: {'reviews': 0, 'titles': set()})
    for review in ctx['reviews']:
        month = review['date_added'].strftime('%Y-%m')
        monthly[month]['reviews'] += 1
        monthly[month]['titles'].add(review['object_id'])
    return [
        {'month': month, 'reviews': values['reviews'], 'titles': len(values['titles'])}
        for month, values in sorted(monthly.items())
    ]


def _fetch_watchlist_monthly(user, content_type):
    """Watchlist add/remove events aggregated by month for a content type.

    Uses the WatchlistEvent log (records adds AND removes). Historical removals
    before the event log shipped are unknown, so net size is exact only from
    that point; adds are backfilled from existing Watchlist rows.
    """
    rows = list(
        WatchlistEvent.objects.filter(user=user, content_type=content_type)
        .annotate(month=TruncMonth('date'))
        .values('month', 'action')
        .annotate(total=Count('id'))
        .order_by('month')
    )
    monthly = defaultdict(lambda: {'adds': 0, 'removes': 0})
    for row in rows:
        if not row['month']:
            continue
        key = row['month'].strftime('%Y-%m')
        if row['action'] == 'add':
            monthly[key]['adds'] += row['total']
        else:
            monthly[key]['removes'] += row['total']
    result = [
        {
            'month': m,
            'adds': v['adds'],
            'removes': v['removes'],
            'net': v['adds'] - v['removes'],
            '_year': int(m[:4]),
        }
        for m, v in sorted(monthly.items())
    ]
    # Carry the opening balance into year-filtered charts. History before the
    # event log is incomplete, so this is a recorded net, not an exact size.
    running = 0
    for row in result:
        running += row['net']
        row['balance'] = running
    return result


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
        existing_ids = set(ct.model_class().objects.filter(
            id__in={r['object_id'] for r in all_reviews},
        ).values_list('id', flat=True))
        all_reviews = [r for r in all_reviews if r['object_id'] in existing_ids]

        # 2. Build all-time context (in-memory)
        ctx = _build_review_context(all_reviews)

        # 4. Fetch model-level data once (~2-5 DB queries)
        media_data = _fetch_media_data(ct, ctx['object_ids'], user)

        # 5. Compute all-time stats (pure in-memory)
        stats = _compute_all_for_slice(ctx, media_data, ct)
        stats['total_reviews'] = len(ctx['object_ids'])

        # 6. Fetch watchlist monthly data for this content type (1 DB query)
        all_watchlist_monthly = _fetch_watchlist_monthly(user, ct)
        stats['watchlist_by_month'] = list(all_watchlist_monthly)
        stats['watchlist_opening_balance'] = 0
        stats['watchlist_count'] = Watchlist.objects.filter(user=user, content_type=ct).count()
        available_years = sorted(
            {r['date_added'].year for r in all_reviews}
            | {r['_year'] for r in all_watchlist_monthly}
            | {r['month'].year for r in media_data['tv_watch_monthly'] if r['month']},
            reverse=True,
        )
        stats['years'] = available_years

        # 7. Compute per-year stats by filtering in-memory
        yearly_data = {}
        for y in available_years:
            year_ctx = _build_review_context(all_reviews, year=y)
            year_slice = _compute_all_for_slice(year_ctx, media_data, ct, year=y)
            year_slice['watchlist_by_month'] = [
                r for r in all_watchlist_monthly if r['_year'] == y
            ]
            year_slice['watchlist_opening_balance'] = sum(
                r['net'] for r in all_watchlist_monthly if r['_year'] < y
            )
            yearly_data[str(y)] = year_slice
        stats['yearly'] = yearly_data

        result[media_key] = stats

    cache.set(cache_key, result, STATS_CACHE_TIMEOUT)
    logger.debug(f'Cached stats for user {user.id}')
    return result
