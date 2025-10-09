"""Optimized database queries for movie and review data.

This module contains raw SQL queries optimized for performance when
building network graphs with large datasets.
"""

import logging
from collections import defaultdict
from typing import List, Dict, Any
from django.db import connection
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger(__name__)

# Cache the ContentType lookup
_MOVIE_CT = None


def _movie_ct():
    """Get Movie ContentType (cached for performance)."""
    global _MOVIE_CT
    if _MOVIE_CT is None:
        from movies.models import Movie
        _MOVIE_CT = ContentType.objects.get_for_model(Movie)
    return _MOVIE_CT


def get_movie_stats_optimized(movie_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Get movie statistics using optimized raw SQL query.
    
    Args:
        movie_ids: List of movie IDs to get stats for
    
    Returns:
        Dictionary mapping movie_id to stats (avg_rating, review_count, rating_stddev)
        
    Example:
        >>> stats = get_movie_stats_optimized([1, 2, 3])
        >>> 1 in stats
        True
        >>> 'avg_rating' in stats[1]
        True
    """
    if not movie_ids:
        return {}

    movie_ct_id = _movie_ct().id

    with connection.cursor() as cursor:
        # Use parameterized query for safety
        placeholders = ','.join(['%s'] * len(movie_ids))
        cursor.execute(f"""
            SELECT
                object_id,
                AVG(rating) as avg_rating,
                COUNT(*) as review_count,
                STDDEV(rating) as rating_stddev
            FROM custom_auth_review
            WHERE content_type_id = %s AND object_id IN ({placeholders})
            GROUP BY object_id
        """, [movie_ct_id] + movie_ids)

        results = cursor.fetchall()

    stats = {
        row[0]: {
            'avg_rating': float(row[1]) if row[1] else 0.0,
            'review_count': row[2],
            'rating_stddev': float(row[3]) if row[3] else 0.0
        }
        for row in results
    }
    
    logger.debug(f"Retrieved stats for {len(stats)} movies")
    return stats


def get_user_rating_matrix_optimized(user_ids: List[int]) -> Dict[int, Dict[int, float]]:
    """Get user-movie rating matrix using optimized query.
    
    Args:
        user_ids: List of user IDs to get ratings for
    
    Returns:
        Dictionary mapping user_id to {movie_id: rating}
        
    Example:
        >>> matrix = get_user_rating_matrix_optimized([1, 2, 3])
        >>> isinstance(matrix, dict)
        True
        >>> if matrix and list(matrix.keys())[0] in user_ids:
        ...     True
        ... else:
        ...     len(matrix) == 0
        True
    """
    if not user_ids:
        return {}

    movie_ct_id = _movie_ct().id

    with connection.cursor() as cursor:
        placeholders = ','.join(['%s'] * len(user_ids))
        cursor.execute(f"""
            SELECT user_id, object_id, rating
            FROM custom_auth_review
            WHERE content_type_id = %s AND user_id IN ({placeholders})
            ORDER BY user_id, object_id
        """, [movie_ct_id] + user_ids)

        results = cursor.fetchall()

    # Build rating matrix
    rating_matrix = defaultdict(dict)
    for user_id, movie_id, rating in results:
        rating_matrix[user_id][movie_id] = float(rating)

    logger.debug(f"Retrieved rating matrix for {len(rating_matrix)} users")
    return dict(rating_matrix)


def get_item_means_optimized(movie_ids: List[int]) -> Dict[int, float]:
    """Get average rating for each movie using optimized query.
    
    Args:
        movie_ids: List of movie IDs to get average ratings for
    
    Returns:
        Dictionary mapping movie_id to average rating
        
    Example:
        >>> means = get_item_means_optimized([1, 2, 3])
        >>> isinstance(means, dict)
        True
    """
    if not movie_ids:
        return {}

    movie_ct_id = _movie_ct().id

    with connection.cursor() as cursor:
        placeholders = ','.join(['%s'] * len(movie_ids))
        cursor.execute(f"""
            SELECT object_id, AVG(rating) as avg_rating
            FROM custom_auth_review
            WHERE content_type_id = %s AND object_id IN ({placeholders})
            GROUP BY object_id
        """, [movie_ct_id] + movie_ids)

        results = cursor.fetchall()

    means = {row[0]: float(row[1]) for row in results}
    
    logger.debug(f"Retrieved average ratings for {len(means)} movies")
    return means
