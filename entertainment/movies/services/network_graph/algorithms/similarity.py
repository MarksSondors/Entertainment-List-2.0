"""Similarity algorithms for network graph analysis.

This module contains various similarity calculation methods used for
comparing users, movies, and other entities in the graph.
"""

import logging
from math import sqrt
from typing import Dict, Set, Tuple, Optional

from ..constants import (
    MIN_SIMILARITY_THRESHOLD,
    SIMILARITY_BATCH_SIZE,
    CF_MIN_COMMON_ITEMS,
)
from ..types import RatingMatrix, SimilarityMatrix, SimilarityMethod, ItemMeans

logger = logging.getLogger(__name__)


def cosine_similarity(vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
    """Calculate cosine similarity between two vectors.
    
    Cosine similarity measures the cosine of the angle between two vectors,
    resulting in a value between -1 and 1, where 1 means perfectly similar.
    
    Args:
        vec1: First vector as dictionary {key: value}
        vec2: Second vector as dictionary {key: value}
    
    Returns:
        Similarity score between 0 and 1
        
    Example:
        >>> vec1 = {'movie1': 5.0, 'movie2': 3.0, 'movie3': 4.0}
        >>> vec2 = {'movie1': 4.5, 'movie2': 3.5, 'movie3': 4.0}
        >>> cosine_similarity(vec1, vec2)
        0.995...
    """
    if not vec1 or not vec2:
        return 0.0

    # Get intersection of keys
    common_keys = set(vec1.keys()) & set(vec2.keys())
    if not common_keys:
        return 0.0

    # Calculate dot product
    dot_product = sum(vec1[k] * vec2[k] for k in common_keys)

    # Calculate magnitudes
    norm1 = sqrt(sum(v * v for v in vec1.values()))
    norm2 = sqrt(sum(v * v for v in vec2.values()))

    return dot_product / (norm1 * norm2) if norm1 and norm2 else 0.0


def pearson_correlation(ratings1: Dict[int, float], ratings2: Dict[int, float]) -> float:
    """Calculate Pearson correlation coefficient between two rating dictionaries.
    
    Pearson correlation measures the linear relationship between two variables,
    accounting for the mean rating of each user. Values range from -1 to 1.
    
    Args:
        ratings1: First user's ratings {movie_id: rating}
        ratings2: Second user's ratings {movie_id: rating}
    
    Returns:
        Correlation coefficient between -1 and 1
        
    Example:
        >>> ratings1 = {1: 5.0, 2: 3.0, 3: 4.0}
        >>> ratings2 = {1: 4.5, 2: 3.5, 3: 4.0}
        >>> pearson_correlation(ratings1, ratings2)
        0.866...
    """
    if not ratings1 or not ratings2:
        return 0.0

    # Find common items
    common_items = set(ratings1.keys()) & set(ratings2.keys())
    if len(common_items) < CF_MIN_COMMON_ITEMS:
        return 0.0

    # Calculate means
    mean1 = sum(ratings1[item] for item in common_items) / len(common_items)
    mean2 = sum(ratings2[item] for item in common_items) / len(common_items)

    # Calculate correlation
    numerator = sum((ratings1[item] - mean1) * (ratings2[item] - mean2) for item in common_items)
    sum_sq1 = sum((ratings1[item] - mean1) ** 2 for item in common_items)
    sum_sq2 = sum((ratings2[item] - mean2) ** 2 for item in common_items)

    denominator = sqrt(sum_sq1 * sum_sq2)
    return numerator / denominator if denominator else 0.0


def jaccard_similarity(set1: Set[int], set2: Set[int]) -> float:
    """Calculate Jaccard similarity between two sets.
    
    Jaccard similarity is the size of the intersection divided by the size of
    the union. It measures how similar two sets are, ranging from 0 to 1.
    
    Args:
        set1: First set
        set2: Second set
    
    Returns:
        Similarity score between 0 and 1
        
    Example:
        >>> set1 = {1, 2, 3, 4, 5}
        >>> set2 = {3, 4, 5, 6, 7}
        >>> jaccard_similarity(set1, set2)
        0.428...
    """
    if not set1 or not set2:
        return 0.0

    intersection = len(set1 & set2)
    union = len(set1 | set2)

    return intersection / union if union else 0.0


def adjusted_cosine_similarity(
    ratings1: Dict[int, float], 
    ratings2: Dict[int, float],
    item_means: ItemMeans
) -> float:
    """Calculate adjusted cosine similarity (subtracting item means).
    
    This variant adjusts ratings by subtracting each item's average rating,
    which helps account for items that are universally rated high or low.
    
    Args:
        ratings1: First user's ratings {movie_id: rating}
        ratings2: Second user's ratings {movie_id: rating}
        item_means: Average rating for each item {movie_id: mean_rating}
    
    Returns:
        Similarity score between 0 and 1
        
    Example:
        >>> ratings1 = {1: 5.0, 2: 3.0, 3: 4.0}
        >>> ratings2 = {1: 4.5, 2: 3.5, 3: 4.0}
        >>> item_means = {1: 4.8, 2: 3.2, 3: 4.0}
        >>> adjusted_cosine_similarity(ratings1, ratings2, item_means)
        0.95...
    """
    if not ratings1 or not ratings2:
        return 0.0

    common_items = set(ratings1.keys()) & set(ratings2.keys())
    if len(common_items) < CF_MIN_COMMON_ITEMS:
        return 0.0

    # Calculate adjusted ratings
    adj_ratings1 = {item: ratings1[item] - item_means.get(item, 0) for item in common_items}
    adj_ratings2 = {item: ratings2[item] - item_means.get(item, 0) for item in common_items}

    return cosine_similarity(adj_ratings1, adj_ratings2)


def get_user_similarity_matrix(
    rating_matrix: RatingMatrix,
    similarity_method: SimilarityMethod = 'cosine',
    item_means: Optional[ItemMeans] = None
) -> SimilarityMatrix:
    """Calculate similarity matrix between all users using specified method.
    
    This function compares all pairs of users and calculates their similarity
    using the specified algorithm. Only similarities above the threshold are stored.
    
    Args:
        rating_matrix: User ratings {user_id: {movie_id: rating}}
        similarity_method: Algorithm to use ('cosine', 'pearson', 'adjusted_cosine', 'jaccard')
        item_means: Average ratings per item (required for adjusted_cosine)
    
    Returns:
        Dictionary mapping (user1_id, user2_id) tuples to similarity scores
        
    Example:
        >>> rating_matrix = {
        ...     1: {101: 5.0, 102: 3.0},
        ...     2: {101: 4.5, 102: 3.5},
        ...     3: {103: 4.0, 104: 5.0}
        ... }
        >>> similarities = get_user_similarity_matrix(rating_matrix)
        >>> (1, 2) in similarities
        True
    """
    user_ids = list(rating_matrix.keys())
    similarity_matrix: SimilarityMatrix = {}

    # Process in batches to avoid memory issues
    for i in range(len(user_ids)):
        for j in range(i + 1, min(i + SIMILARITY_BATCH_SIZE + 1, len(user_ids))):
            user1, user2 = user_ids[i], user_ids[j]

            if similarity_method == 'cosine':
                similarity = cosine_similarity(rating_matrix[user1], rating_matrix[user2])
            elif similarity_method == 'pearson':
                similarity = pearson_correlation(rating_matrix[user1], rating_matrix[user2])
            elif similarity_method == 'adjusted_cosine':
                if item_means is None:
                    # Calculate item means if not provided
                    from ..queries import get_item_means_optimized
                    all_items = list(set(rating_matrix[user1].keys()) | set(rating_matrix[user2].keys()))
                    item_means = get_item_means_optimized(all_items)
                similarity = adjusted_cosine_similarity(rating_matrix[user1], rating_matrix[user2], item_means)
            elif similarity_method == 'jaccard':
                # Convert to sets of rated movies
                set1 = set(rating_matrix[user1].keys())
                set2 = set(rating_matrix[user2].keys())
                similarity = jaccard_similarity(set1, set2)
            else:
                # Default to cosine
                similarity = cosine_similarity(rating_matrix[user1], rating_matrix[user2])

            # Only store significant similarities
            if abs(similarity) >= MIN_SIMILARITY_THRESHOLD:
                similarity_matrix[(user1, user2)] = similarity

    logger.info(f"Calculated {len(similarity_matrix)} significant similarities using {similarity_method} method")
    return similarity_matrix
