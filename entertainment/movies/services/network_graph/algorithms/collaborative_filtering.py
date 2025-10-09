"""Collaborative filtering algorithms for movie recommendations.

This module implements collaborative filtering methods to predict user ratings
and generate personalized movie recommendations based on similar users.
"""

import logging
from typing import Dict, List, Tuple

from ..constants import CF_TOP_K_USERS, CF_MIN_RATING_THRESHOLD, CF_MAX_RATING_THRESHOLD
from ..types import RatingMatrix, SimilarityMatrix

logger = logging.getLogger(__name__)


def _clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(value, max_val))


def get_collaborative_filtering_predictions(
    user_id: int,
    rating_matrix: RatingMatrix,
    similarity_matrix: SimilarityMatrix,
    target_movies: List[int],
    k: int = CF_TOP_K_USERS
) -> Dict[int, float]:
    """Generate collaborative filtering predictions for a user.
    
    This function uses k-nearest neighbors approach to predict ratings for
    movies the user hasn't seen yet, based on ratings from similar users.
    
    Args:
        user_id: ID of the user to generate predictions for
        rating_matrix: User ratings {user_id: {movie_id: rating}}
        similarity_matrix: Pairwise similarities {(user1, user2): similarity}
        target_movies: List of movie IDs to predict ratings for
        k: Number of similar users to consider (default: 10)
    
    Returns:
        Dictionary mapping movie_id to predicted rating
        
    Example:
        >>> rating_matrix = {
        ...     1: {101: 8.0, 102: 6.0},
        ...     2: {101: 7.5, 102: 6.5, 103: 9.0},
        ...     3: {101: 8.5, 103: 8.0}
        ... }
        >>> similarity_matrix = {(1, 2): 0.85, (1, 3): 0.90, (2, 3): 0.75}
        >>> predictions = get_collaborative_filtering_predictions(
        ...     user_id=1,
        ...     rating_matrix=rating_matrix,
        ...     similarity_matrix=similarity_matrix,
        ...     target_movies=[103],
        ...     k=2
        ... )
        >>> 103 in predictions
        True
        >>> 7.0 < predictions[103] < 9.0  # Should be weighted average of 9.0 and 8.0
        True
    """
    if user_id not in rating_matrix:
        logger.warning(f"User {user_id} not found in rating matrix")
        return {}

    user_ratings = rating_matrix[user_id]
    predictions = {}

    for movie_id in target_movies:
        # Skip if user already rated this movie
        if movie_id in user_ratings:
            continue

        # Find similar users who rated this movie
        similar_users: List[Tuple[int, float]] = []
        
        for (u1, u2), similarity in similarity_matrix.items():
            # Check if this similarity pair involves our target user
            if u1 == user_id and u2 in rating_matrix and movie_id in rating_matrix[u2]:
                similar_users.append((u2, similarity))
            elif u2 == user_id and u1 in rating_matrix and movie_id in rating_matrix[u1]:
                similar_users.append((u1, similarity))

        if not similar_users:
            continue

        # Sort by similarity (absolute value) and take top k
        similar_users.sort(key=lambda x: abs(x[1]), reverse=True)
        similar_users = similar_users[:k]

        # Calculate weighted average of ratings from similar users
        numerator = sum(
            similarity * rating_matrix[other_user][movie_id]
            for other_user, similarity in similar_users
        )
        denominator = sum(abs(similarity) for _, similarity in similar_users)

        if denominator > 0:
            # Predict rating and clamp to valid range
            prediction = numerator / denominator
            predictions[movie_id] = _clamp(
                prediction, 
                CF_MIN_RATING_THRESHOLD, 
                CF_MAX_RATING_THRESHOLD
            )

    logger.info(f"Generated {len(predictions)} predictions for user {user_id} using top {k} similar users")
    return predictions
