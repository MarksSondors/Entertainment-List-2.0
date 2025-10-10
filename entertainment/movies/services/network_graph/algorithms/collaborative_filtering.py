"""Collaborative filtering algorithms for movie recommendations.

This module implements collaborative filtering methods to predict user ratings
and generate personalized movie recommendations based on similar users.
"""

import logging
from typing import Dict, List, Tuple

from ..constants import CF_TOP_K_USERS, CF_MIN_RATING_THRESHOLD, CF_MAX_RATING_THRESHOLD, CF_MIN_COMMON_ITEMS
from ..types import RatingMatrix, SimilarityMatrix, ItemMeans

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


def generate_recommendations(
    user_id: int,
    rating_matrix: RatingMatrix,
    item_means: ItemMeans,
    top_n: int = 10,
    k: int = CF_TOP_K_USERS,
    similarity_method: str = 'pearson',
    min_common_items: int = CF_MIN_COMMON_ITEMS
) -> List[Dict[str, any]]:
    """Generate top-N movie recommendations for a user.
    
    This is a complete recommendation pipeline that:
    1. Calculates user similarities
    2. Generates predictions for unrated movies
    3. Returns top-N recommendations sorted by predicted rating
    
    Args:
        user_id: ID of the user to generate recommendations for
        rating_matrix: User ratings {user_id: {movie_id: rating}}
        item_means: Average ratings per movie {movie_id: mean_rating}
        top_n: Number of recommendations to return (default: 10)
        k: Number of similar users to consider (default: 10)
        similarity_method: Method to calculate similarity (default: 'pearson')
        min_common_items: Minimum common ratings for similarity (default: 2)
    
    Returns:
        List of recommendation dictionaries with movie_id, predicted_rating, and confidence
        
    Example:
        >>> rating_matrix = {
        ...     1: {101: 8.0, 102: 6.0},
        ...     2: {101: 7.5, 102: 6.5, 103: 9.0},
        ...     3: {101: 8.5, 103: 8.0, 104: 7.0}
        ... }
        >>> item_means = {101: 8.0, 102: 6.25, 103: 8.5, 104: 7.0}
        >>> recommendations = generate_recommendations(
        ...     user_id=1,
        ...     rating_matrix=rating_matrix,
        ...     item_means=item_means,
        ...     top_n=2
        ... )
        >>> len(recommendations) <= 2
        True
        >>> all('movie_id' in rec and 'predicted_rating' in rec for rec in recommendations)
        True
    """
    from .similarity import get_user_similarity_matrix
    
    if user_id not in rating_matrix:
        logger.warning(f"User {user_id} not found in rating matrix")
        return []
    
    user_ratings = rating_matrix[user_id]
    
    # Get all movies that user hasn't rated
    all_movies = set()
    for user_movies in rating_matrix.values():
        all_movies.update(user_movies.keys())
    
    unrated_movies = list(all_movies - set(user_ratings.keys()))
    
    if not unrated_movies:
        logger.info(f"User {user_id} has rated all movies in the system")
        return []
    
    # Calculate similarity matrix
    logger.info(f"Calculating user similarities for user {user_id}")
    similarity_matrix = get_user_similarity_matrix(
        rating_matrix=rating_matrix,
        similarity_method=similarity_method,
        item_means=item_means
    )
    
    # Generate predictions
    logger.info(f"Generating predictions for {len(unrated_movies)} unrated movies")
    predictions = get_collaborative_filtering_predictions(
        user_id=user_id,
        rating_matrix=rating_matrix,
        similarity_matrix=similarity_matrix,
        target_movies=unrated_movies,
        k=k
    )
    
    if not predictions:
        logger.warning(f"No predictions generated for user {user_id}")
        return []
    
    # Calculate confidence based on number of similar users
    recommendations = []
    for movie_id, predicted_rating in predictions.items():
        # Count how many similar users contributed to this prediction
        contributors = 0
        for (u1, u2), similarity in similarity_matrix.items():
            other_user = u2 if u1 == user_id else (u1 if u2 == user_id else None)
            if other_user and other_user in rating_matrix and movie_id in rating_matrix[other_user]:
                contributors += 1
        
        # Determine confidence level
        if contributors >= k * 0.7:
            confidence = 'high'
        elif contributors >= k * 0.4:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        recommendations.append({
            'movie_id': movie_id,
            'predicted_rating': predicted_rating,
            'contributors': contributors,
            'confidence': confidence
        })
    
    # Sort by predicted rating (descending) and return top N
    recommendations.sort(key=lambda x: x['predicted_rating'], reverse=True)
    top_recommendations = recommendations[:top_n]
    
    logger.info(f"Generated {len(top_recommendations)} recommendations for user {user_id}")
    return top_recommendations
