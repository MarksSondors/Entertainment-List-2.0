"""Statistics service for aggregating user review data across media types."""

import logging
from collections import defaultdict
from django.core.cache import cache
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Avg, F, Sum
from django.db.models.functions import ExtractYear, TruncDate, TruncMonth

from custom_auth.models import Review, MediaPerson
from movies.models import Movie
from tvshows.models import TVShow, WatchedEpisode, Episode
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


def _get_user_reviews(user, content_type):
    """Return base queryset of a user's reviews for a given content type."""
    return Review.objects.filter(user=user, content_type=content_type)


def _get_available_years(user, content_type):
    """Return sorted list of distinct years the user has reviews for a content type."""
    years = (
        _get_user_reviews(user, content_type)
        .annotate(year=ExtractYear('date_added'))
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )
    return list(years)


def _filter_by_year(qs, year):
    """Apply year filter to queryset if year is not None."""
    if year is not None:
        qs = qs.filter(date_added__year=year)
    return qs


def get_rating_distribution(user, content_type, year=None):
    """Count of reviews grouped by integer rating (0-10)."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)

    # Initialize all ratings 0-10 with 0 count
    distribution = {str(i): 0 for i in range(11)}

    # Group by integer part of rating
    for review in reviews.values_list('rating', flat=True):
        key = str(int(review))
        if key in distribution:
            distribution[key] += 1

    return distribution


def get_ratings_over_time(user, content_type, year=None):
    """Each review as {date, rating} sorted by date."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)

    data = list(
        reviews
        .annotate(date=TruncDate('date_added'))
        .values('date', 'rating')
        .order_by('date')
    )

    # Convert dates to ISO strings for JSON serialization
    return [
        {'date': item['date'].isoformat(), 'rating': float(item['rating'])}
        for item in data
    ]


def get_release_year_distribution(user, content_type, year=None):
    """Count of rated items grouped by their media release year."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)
    object_ids = list(reviews.values_list('object_id', flat=True))

    if not object_ids:
        return {}

    model_class = content_type.model_class()

    # Determine the release date field name
    if model_class == TVShow:
        date_field = 'first_air_date'
    else:
        date_field = 'release_date'

    # Query the media items for their release years
    items = (
        model_class.objects
        .filter(id__in=object_ids, **{f'{date_field}__isnull': False})
        .annotate(release_year=ExtractYear(date_field))
        .values('release_year')
        .annotate(count=Count('id'))
        .order_by('release_year')
    )

    return {str(item['release_year']): item['count'] for item in items}


def get_avg_rating_by_release_year(user, content_type, year=None):
    """Average user rating grouped by the media's release year."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)
    object_ids = list(reviews.values_list('object_id', flat=True))

    if not object_ids:
        return {}

    # Build a map of object_id -> rating
    rating_map = {}
    for r in reviews.values('object_id', 'rating'):
        oid = r['object_id']
        if oid not in rating_map:
            rating_map[oid] = []
        rating_map[oid].append(float(r['rating']))

    model_class = content_type.model_class()
    date_field = 'first_air_date' if model_class == TVShow else 'release_date'

    items = (
        model_class.objects
        .filter(id__in=object_ids, **{f'{date_field}__isnull': False})
        .annotate(release_year=ExtractYear(date_field))
        .values('id', 'release_year')
    )

    year_ratings = defaultdict(list)
    for item in items:
        avg_item_rating = sum(rating_map[item['id']]) / len(rating_map[item['id']])
        year_ratings[item['release_year']].append(avg_item_rating)

    result = {
        str(yr): round(sum(ratings) / len(ratings), 2)
        for yr, ratings in year_ratings.items()
    }

    return dict(sorted(result.items(), key=lambda x: int(x[0])))


def get_count_by_genre(user, content_type, year=None):
    """Count of rated items per genre."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)
    object_ids = list(reviews.values_list('object_id', flat=True))

    if not object_ids:
        return {}

    model_class = content_type.model_class()
    items = model_class.objects.filter(id__in=object_ids).prefetch_related('genres')

    genre_counts = defaultdict(int)
    for item in items:
        for genre in item.genres.all():
            genre_counts[genre.name] += 1

    # Sort by count descending
    return dict(sorted(genre_counts.items(), key=lambda x: x[1], reverse=True))


def get_avg_rating_by_genre(user, content_type, year=None):
    """Average user rating per genre."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)

    # Build a map of object_id -> rating
    rating_map = {}
    for r in reviews.values('object_id', 'rating'):
        # For TV shows there can be multiple reviews per show (season/subgroup),
        # so we average them per object_id
        oid = r['object_id']
        if oid not in rating_map:
            rating_map[oid] = []
        rating_map[oid].append(float(r['rating']))

    if not rating_map:
        return {}

    model_class = content_type.model_class()
    items = model_class.objects.filter(id__in=list(rating_map.keys())).prefetch_related('genres')

    genre_ratings = defaultdict(list)
    for item in items:
        # Average all review ratings for this item
        avg_item_rating = sum(rating_map[item.id]) / len(rating_map[item.id])
        for genre in item.genres.all():
            genre_ratings[genre.name].append(avg_item_rating)

    result = {
        name: round(sum(ratings) / len(ratings), 2)
        for name, ratings in genre_ratings.items()
    }

    # Sort by avg rating descending
    return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))


def get_summary_stats(user, content_type, year=None):
    """Summary: total rated, total hours watched, average rating."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)
    object_ids = list(reviews.values_list('object_id', flat=True))

    total_rated = len(object_ids)
    avg_rating = reviews.aggregate(avg=Avg('rating'))['avg']
    avg_rating = round(float(avg_rating), 2) if avg_rating else 0

    total_minutes = 0
    model_class = content_type.model_class()

    if model_class == Movie:
        total_minutes = (
            Movie.objects.filter(id__in=object_ids)
            .aggregate(total=Sum('runtime'))['total'] or 0
        )
    elif model_class == TVShow:
        # Sum runtime of all watched episodes for these shows
        total_minutes = (
            WatchedEpisode.objects.filter(
                user=user,
                episode__season__show_id__in=object_ids
            )
            .aggregate(total=Sum('episode__runtime'))['total'] or 0
        )

    total_hours = round(total_minutes / 60, 1) if total_minutes else 0

    return {
        'total_rated': total_rated,
        'total_hours': total_hours,
        'avg_rating': avg_rating,
    }


def get_watch_time_by_month(user, content_type, year=None):
    """Total runtime (hours) grouped by month. Movies only & TV shows only."""
    model_class = content_type.model_class()

    if model_class == Game:
        return {}

    if model_class == Movie:
        reviews = _filter_by_year(_get_user_reviews(user, content_type), year)
        # Join reviews with movie runtime, group by month of review date
        data = (
            reviews
            .annotate(month=TruncMonth('date_added'))
            .values('month', 'object_id')
        )
        # Fetch runtimes
        object_ids = list(reviews.values_list('object_id', flat=True))
        runtimes = dict(
            Movie.objects.filter(id__in=object_ids).values_list('id', 'runtime')
        )
        month_minutes = defaultdict(int)
        for row in data:
            month_minutes[row['month']] += runtimes.get(row['object_id'], 0)

        result = [
            {'month': m.strftime('%Y-%m'), 'hours': round(mins / 60, 1)}
            for m, mins in sorted(month_minutes.items())
        ]
        return result

    elif model_class == TVShow:
        qs = WatchedEpisode.objects.filter(
            user=user,
            episode__runtime__isnull=False,
        )
        if year is not None:
            qs = qs.filter(watched_date__year=year)

        data = (
            qs
            .annotate(month=TruncMonth('watched_date'))
            .values('month')
            .annotate(total_mins=Sum('episode__runtime'))
            .order_by('month')
        )
        return [
            {'month': row['month'].strftime('%Y-%m'), 'hours': round(row['total_mins'] / 60, 1)}
            for row in data if row['month']
        ]

    return {}


def _get_top_people_by_role(user, content_type, role, year=None, limit=10):
    """Top people by count for a given role. Movies and TV Shows only."""
    model_class = content_type.model_class()
    if model_class == Game:
        return []

    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)
    object_ids = list(reviews.values_list('object_id', flat=True))

    if not object_ids:
        return []

    rating_map = {}
    for r in reviews.values('object_id', 'rating'):
        oid = r['object_id']
        if oid not in rating_map:
            rating_map[oid] = []
        rating_map[oid].append(float(r['rating']))

    entries = (
        MediaPerson.objects
        .filter(
            content_type=content_type,
            object_id__in=object_ids,
            role=role
        )
        .select_related('person')
    )

    person_data = defaultdict(lambda: {'count': 0, 'ratings': []})
    for entry in entries:
        name = entry.person.name
        person_data[name]['count'] += 1
        if entry.object_id in rating_map:
            avg_item = sum(rating_map[entry.object_id]) / len(rating_map[entry.object_id])
            person_data[name]['ratings'].append(avg_item)

    sorted_people = sorted(person_data.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]

    return [
        {
            'name': name,
            'count': info['count'],
            'avg_rating': round(sum(info['ratings']) / len(info['ratings']), 2) if info['ratings'] else 0,
        }
        for name, info in sorted_people
    ]


def _get_top_people_by_rating(user, content_type, role, year=None, limit=10, min_count=2):
    """Top people by average rating for a given role (min count threshold). Movies and TV Shows only."""
    model_class = content_type.model_class()
    if model_class == Game:
        return []

    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)
    object_ids = list(reviews.values_list('object_id', flat=True))

    if not object_ids:
        return []

    rating_map = {}
    for r in reviews.values('object_id', 'rating'):
        oid = r['object_id']
        if oid not in rating_map:
            rating_map[oid] = []
        rating_map[oid].append(float(r['rating']))

    entries = (
        MediaPerson.objects
        .filter(
            content_type=content_type,
            object_id__in=object_ids,
            role=role
        )
        .select_related('person')
    )

    person_data = defaultdict(lambda: {'count': 0, 'ratings': []})
    for entry in entries:
        name = entry.person.name
        person_data[name]['count'] += 1
        if entry.object_id in rating_map:
            avg_item = sum(rating_map[entry.object_id]) / len(rating_map[entry.object_id])
            person_data[name]['ratings'].append(avg_item)

    qualified = [
        (name, info) for name, info in person_data.items()
        if info['count'] >= min_count and info['ratings']
    ]
    sorted_people = sorted(
        qualified,
        key=lambda x: sum(x[1]['ratings']) / len(x[1]['ratings']),
        reverse=True
    )[:limit]

    return [
        {
            'name': name,
            'count': info['count'],
            'avg_rating': round(sum(info['ratings']) / len(info['ratings']), 2),
        }
        for name, info in sorted_people
    ]


def get_top_directors(user, content_type, year=None, limit=10):
    """Top directors by number of rated items. Movies only."""
    if content_type.model_class() != Movie:
        return []
    return _get_top_people_by_role(user, content_type, 'Director', year, limit)


def get_top_directors_by_rating(user, content_type, year=None, limit=10, min_films=2):
    """Top directors by average rating. Movies only."""
    if content_type.model_class() != Movie:
        return []
    return _get_top_people_by_rating(user, content_type, 'Director', year, limit, min_films)


def get_top_actors(user, content_type, year=None, limit=10):
    """Top actors by number of rated items. Movies and TV Shows."""
    return _get_top_people_by_role(user, content_type, 'Actor', year, limit)


def get_top_actors_by_rating(user, content_type, year=None, limit=10, min_count=2):
    """Top actors by average rating. Movies and TV Shows."""
    return _get_top_people_by_rating(user, content_type, 'Actor', year, limit, min_count)


def get_all_stats(user):
    """
    Compute all statistics for all three media types.
    Results are cached per user and invalidated on review changes.
    """
    cache_key = _stats_cache_key(user.id)
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug(f'Stats cache hit for user {user.id}')
        return cached

    logger.debug(f'Stats cache miss for user {user.id}, computing...')
    content_types = _get_content_types()
    result = {}

    for media_key, ct in content_types.items():
        available_years = _get_available_years(user, ct)
        total_reviews = _get_user_reviews(user, ct).count()

        stats = {
            'years': available_years,
            'total_reviews': total_reviews,
            'summary': get_summary_stats(user, ct),
            'rating_distribution': get_rating_distribution(user, ct),
            'ratings_over_time': get_ratings_over_time(user, ct),
            'release_year_distribution': get_release_year_distribution(user, ct),
            'avg_rating_by_release_year': get_avg_rating_by_release_year(user, ct),
            'count_by_genre': get_count_by_genre(user, ct),
            'avg_rating_by_genre': get_avg_rating_by_genre(user, ct),
            'watch_time_by_month': get_watch_time_by_month(user, ct),
            'top_directors': get_top_directors(user, ct),
            'top_directors_by_rating': get_top_directors_by_rating(user, ct),
            'top_actors': get_top_actors(user, ct),
            'top_actors_by_rating': get_top_actors_by_rating(user, ct),
        }

        # Also compute per-year breakdowns for client-side year filtering
        yearly_data = {}
        for y in available_years:
            yearly_data[str(y)] = {
                'summary': get_summary_stats(user, ct, year=y),
                'rating_distribution': get_rating_distribution(user, ct, year=y),
                'ratings_over_time': get_ratings_over_time(user, ct, year=y),
                'release_year_distribution': get_release_year_distribution(user, ct, year=y),
                'avg_rating_by_release_year': get_avg_rating_by_release_year(user, ct, year=y),
                'count_by_genre': get_count_by_genre(user, ct, year=y),
                'avg_rating_by_genre': get_avg_rating_by_genre(user, ct, year=y),
                'watch_time_by_month': get_watch_time_by_month(user, ct, year=y),
                'top_directors': get_top_directors(user, ct, year=y),
                'top_directors_by_rating': get_top_directors_by_rating(user, ct, year=y),
                'top_actors': get_top_actors(user, ct, year=y),
                'top_actors_by_rating': get_top_actors_by_rating(user, ct, year=y),
            }

        stats['yearly'] = yearly_data
        result[media_key] = stats

    cache.set(cache_key, result, STATS_CACHE_TIMEOUT)
    logger.debug(f'Cached stats for user {user.id}')
    return result
