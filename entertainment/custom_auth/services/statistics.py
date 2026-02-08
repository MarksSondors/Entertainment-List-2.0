"""Statistics service for aggregating user review data across media types."""

from collections import defaultdict
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Avg, F
from django.db.models.functions import ExtractYear, TruncDate

from custom_auth.models import Review
from movies.models import Movie
from tvshows.models import TVShow
from games.models import Game


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


def get_avg_rating_by_year(user, content_type, year=None):
    """Average user rating grouped by the year the review was created."""
    reviews = _filter_by_year(_get_user_reviews(user, content_type), year)

    data = (
        reviews
        .annotate(review_year=ExtractYear('date_added'))
        .values('review_year')
        .annotate(avg_rating=Avg('rating'))
        .order_by('review_year')
    )

    return {
        str(item['review_year']): round(float(item['avg_rating']), 2)
        for item in data
    }


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


def get_all_stats(user):
    """
    Compute all statistics for all three media types.
    Returns a nested dict ready for JSON serialization:
    {
        "movies": {"years": [...], "rating_distribution": {...}, ...},
        "tvshows": {...},
        "games": {...},
    }
    """
    content_types = _get_content_types()
    result = {}

    for media_key, ct in content_types.items():
        available_years = _get_available_years(user, ct)
        total_reviews = _get_user_reviews(user, ct).count()

        stats = {
            'years': available_years,
            'total_reviews': total_reviews,
            'rating_distribution': get_rating_distribution(user, ct),
            'ratings_over_time': get_ratings_over_time(user, ct),
            'release_year_distribution': get_release_year_distribution(user, ct),
            'avg_rating_by_year': get_avg_rating_by_year(user, ct),
            'count_by_genre': get_count_by_genre(user, ct),
            'avg_rating_by_genre': get_avg_rating_by_genre(user, ct),
        }

        # Also compute per-year breakdowns for client-side year filtering
        yearly_data = {}
        for y in available_years:
            yearly_data[str(y)] = {
                'rating_distribution': get_rating_distribution(user, ct, year=y),
                'ratings_over_time': get_ratings_over_time(user, ct, year=y),
                'release_year_distribution': get_release_year_distribution(user, ct, year=y),
                'avg_rating_by_year': get_avg_rating_by_year(user, ct, year=y),
                'count_by_genre': get_count_by_genre(user, ct, year=y),
                'avg_rating_by_genre': get_avg_rating_by_genre(user, ct, year=y),
            }

        stats['yearly'] = yearly_data
        result[media_key] = stats

    return result
