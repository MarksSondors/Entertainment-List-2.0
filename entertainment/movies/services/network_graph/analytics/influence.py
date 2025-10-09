"""Influence Analysis for Network Graphs.

Identifies and measures the influence of users and movies in the network.
Influence is determined by multiple factors:
- Centrality (how connected a node is)
- Review quality and consistency
- Community leadership
- Recommendation acceptance rate
"""

import logging
from typing import Dict, List, Tuple, Set, Any, Optional
from collections import defaultdict
from datetime import datetime, timedelta
import networkx as nx

from django.db.models import Count, Avg, Q, F
from django.contrib.auth import get_user_model
from custom_auth.models import Review
from movies.models import Movie

from ..constants import (
    INFLUENCE_DECAY_FACTOR,
    MIN_REVIEWS_FOR_INFLUENCE,
    INFLUENCE_TIME_WINDOW_DAYS,
)
from ..utils import _safe_divide
from ..algorithms.centrality import calculate_centrality_measures

logger = logging.getLogger(__name__)
User = get_user_model()


def calculate_influence_scores(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    time_decay: bool = True,
    min_reviews: int = MIN_REVIEWS_FOR_INFLUENCE
) -> Dict[str, float]:
    """Calculate influence scores for all nodes in the graph.
    
    Influence score is a composite metric combining:
    - Network centrality (40%) - How connected is the node
    - Activity level (30%) - How active/reviewed
    - Quality score (20%) - Average rating consistency
    - Recency (10%) - Recent activity boost
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
        time_decay: Whether to apply time decay to older activity
        min_reviews: Minimum reviews required for influence calculation
    
    Returns:
        Dict mapping node_id to influence score (0.0 to 100.0)
    
    Example:
        >>> nodes = [{'id': 'user_1', 'type': 'user', ...}, ...]
        >>> edges = [{'source': 'user_1', 'target': 'movie_1', ...}, ...]
        >>> scores = calculate_influence_scores(nodes, edges)
        >>> scores['user_1']
        85.3
    """
    logger.info(f"Calculating influence scores for {len(nodes)} nodes")
    
    # Build NetworkX graph for centrality calculations
    G = nx.Graph()
    for node in nodes:
        G.add_node(node['id'], **node)
    for edge in edges:
        G.add_edge(edge['source'], edge['target'], **edge)
    
    # Calculate centrality measures (pass nodes and edges, not G)
    centrality_scores = calculate_centrality_measures(nodes, edges)
    
    # Initialize influence scores
    influence_scores = {}
    
    # Process each node
    for node in nodes:
        node_id = node['id']
        node_type = node.get('type', 'unknown')
        
        # Skip nodes with insufficient data
        review_count = node.get('review_count', 0)
        if review_count < min_reviews and node_type == 'user':
            influence_scores[node_id] = 0.0
            continue
        
        # Component 1: Network Centrality (40%)
        centrality_score = 0.0
        if node_id in centrality_scores:
            # Average of available centrality measures
            measures = centrality_scores[node_id]
            centrality_score = sum([
                measures.get('degree', 0) * 0.3,
                measures.get('betweenness', 0) * 0.3,
                measures.get('closeness', 0) * 0.2,
                measures.get('eigenvector', 0) * 0.2,
            ])
        
        # Component 2: Activity Level (30%)
        activity_score = 0.0
        if node_type == 'user':
            # For users: based on review count
            max_reviews = max(n.get('review_count', 1) for n in nodes if n.get('type') == 'user')
            activity_score = min(review_count / max(max_reviews, 1), 1.0)
        elif node_type == 'movie':
            # For movies: based on number of reviews received
            max_reviews = max(n.get('review_count', 1) for n in nodes if n.get('type') == 'movie')
            activity_score = min(review_count / max(max_reviews, 1), 1.0)
        
        # Component 3: Quality Score (20%)
        quality_score = 0.0
        avg_rating = node.get('average_rating', 0)
        rating_variance = node.get('rating_variance', 0)
        
        if avg_rating > 0:
            # Normalize rating to 0-1 scale (assuming 0-10 scale)
            normalized_rating = avg_rating / 10.0
            # Lower variance is better (more consistent)
            consistency = 1.0 - min(rating_variance / 25.0, 1.0)  # 25 = max variance
            quality_score = (normalized_rating * 0.6 + consistency * 0.4)
        
        # Component 4: Recency Boost (10%)
        recency_score = 0.0
        if time_decay:
            last_activity = node.get('last_activity')
            if last_activity:
                if isinstance(last_activity, str):
                    try:
                        last_activity = datetime.fromisoformat(last_activity.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        last_activity = None
                
                if last_activity and isinstance(last_activity, datetime):
                    days_ago = (datetime.now(last_activity.tzinfo) - last_activity).days
                    # Exponential decay: most recent = 1.0, decay over time
                    decay_rate = INFLUENCE_DECAY_FACTOR
                    recency_score = max(0.0, 1.0 - (days_ago / INFLUENCE_TIME_WINDOW_DAYS) ** decay_rate)
        
        # Calculate final influence score (0-100 scale)
        influence = (
            centrality_score * 40.0 +
            activity_score * 30.0 +
            quality_score * 20.0 +
            recency_score * 10.0
        )
        
        influence_scores[node_id] = round(influence, 2)
    
    logger.info(f"Calculated influence scores for {len(influence_scores)} nodes")
    return influence_scores


def get_top_influencers(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    node_type: Optional[str] = None,
    top_n: int = 10
) -> List[Dict[str, Any]]:
    """Get the top N most influential nodes.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
        node_type: Filter by node type ('user', 'movie', etc.), or None for all
        top_n: Number of top influencers to return
    
    Returns:
        List of dicts with node info and influence score, sorted by score descending
    
    Example:
        >>> top_users = get_top_influencers(nodes, edges, node_type='user', top_n=5)
        >>> top_users[0]
        {
            'id': 'user_42',
            'label': 'MovieBuff123',
            'type': 'user',
            'influence_score': 95.2,
            'rank': 1
        }
    """
    logger.info(f"Finding top {top_n} influencers" + (f" of type '{node_type}'" if node_type else ""))
    
    # Calculate influence scores
    scores = calculate_influence_scores(nodes, edges)
    
    # Filter by type if specified
    filtered_nodes = nodes
    if node_type:
        filtered_nodes = [n for n in nodes if n.get('type') == node_type]
    
    # Create result list with scores
    influencers = []
    for node in filtered_nodes:
        node_id = node['id']
        if node_id in scores:
            influencers.append({
                'id': node_id,
                'label': node.get('label', node_id),
                'type': node.get('type', 'unknown'),
                'influence_score': scores[node_id],
                'review_count': node.get('review_count', 0),
                'average_rating': node.get('average_rating', 0),
            })
    
    # Sort by influence score descending
    influencers.sort(key=lambda x: x['influence_score'], reverse=True)
    
    # Add rank and limit to top N
    result = []
    for rank, influencer in enumerate(influencers[:top_n], start=1):
        influencer['rank'] = rank
        result.append(influencer)
    
    logger.info(f"Found {len(result)} top influencers")
    return result


def calculate_influence_propagation(
    graph: nx.Graph,
    source_node: str,
    *,
    max_hops: int = 3,
    decay_factor: float = 0.5
) -> Dict[str, float]:
    """Calculate how influence propagates from a source node through the network.
    
    Uses a spreading activation model where influence decreases with distance.
    Useful for understanding reach and impact of influential nodes.
    
    Args:
        graph: NetworkX graph
        source_node: Starting node ID
        max_hops: Maximum distance to propagate (default: 3)
        decay_factor: How much influence decreases per hop (0-1, default: 0.5)
    
    Returns:
        Dict mapping node_id to propagated influence score
    
    Example:
        >>> G = nx.Graph(edges)
        >>> propagation = calculate_influence_propagation(G, 'user_1', max_hops=3)
        >>> propagation['user_2']  # Direct connection
        0.5
        >>> propagation['user_3']  # 2 hops away
        0.25
    """
    if source_node not in graph:
        logger.warning(f"Source node '{source_node}' not in graph")
        return {}
    
    logger.info(f"Calculating influence propagation from '{source_node}' (max_hops={max_hops})")
    
    # Initialize: source node has influence = 1.0
    influence = {source_node: 1.0}
    visited = {source_node}
    
    # BFS with decay
    current_level = {source_node}
    current_influence = 1.0
    
    for hop in range(max_hops):
        next_level = set()
        current_influence *= decay_factor
        
        if current_influence < 0.01:  # Stop if influence becomes negligible
            break
        
        for node in current_level:
            for neighbor in graph.neighbors(node):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_level.add(neighbor)
                    influence[neighbor] = current_influence
        
        if not next_level:
            break
        
        current_level = next_level
    
    logger.info(f"Influence propagated to {len(influence)} nodes")
    return influence


def score_recommendation_quality(
    user_id: int,
    *,
    time_window_days: int = 90
) -> Dict[str, Any]:
    """Score the quality of recommendations made by/to a user.
    
    Analyzes how well predictions match actual user behavior.
    
    Args:
        user_id: User ID to analyze
        time_window_days: Number of days to look back (default: 90)
    
    Returns:
        Dict with recommendation quality metrics:
        - acceptance_rate: % of recommendations that were watched/rated
        - average_rating_delta: Difference between predicted and actual ratings
        - discovery_score: How many new movies were discovered
        - quality_score: Overall 0-100 quality score
    
    Example:
        >>> quality = score_recommendation_quality(user_id=42, time_window_days=30)
        >>> quality['acceptance_rate']
        0.65  # 65% of recommendations were accepted
    """
    logger.info(f"Scoring recommendation quality for user {user_id}")
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found")
        return {
            'acceptance_rate': 0.0,
            'average_rating_delta': 0.0,
            'discovery_score': 0.0,
            'quality_score': 0.0,
            'error': 'User not found'
        }
    
    # Calculate time window
    cutoff_date = datetime.now() - timedelta(days=time_window_days)
    
    # Get user's reviews in the time window
    from django.contrib.contenttypes.models import ContentType
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    recent_reviews = Review.objects.filter(
        user=user,
        content_type=movie_ct,
        date_added__gte=cutoff_date
    ).select_related('user').values_list('object_id', 'rating', 'date_added')
    
    reviewed_movies = {movie_id: rating for movie_id, rating, _ in recent_reviews}
    
    # Simple quality scoring based on review activity
    # (Full implementation would require prediction tracking)
    review_count = len(reviewed_movies)
    avg_rating = sum(reviewed_movies.values()) / max(len(reviewed_movies), 1)
    
    # Discovery score: number of unique movies reviewed
    discovery_score = min(review_count / 20.0, 1.0)  # 20 movies = perfect score
    
    # Quality score based on activity and rating diversity
    rating_variance = 0.0
    if review_count > 1:
        ratings = list(reviewed_movies.values())
        mean_rating = sum(ratings) / len(ratings)
        rating_variance = sum((r - mean_rating) ** 2 for r in ratings) / len(ratings)
    
    # Higher variance = more diverse ratings = better engagement
    diversity_score = min(rating_variance / 4.0, 1.0)  # variance of 4 = perfect
    
    quality_score = (
        discovery_score * 50.0 +  # Activity weight
        diversity_score * 30.0 +   # Diversity weight
        (avg_rating / 10.0) * 20.0  # Rating weight
    )
    
    result = {
        'acceptance_rate': discovery_score,
        'average_rating_delta': 0.0,  # Placeholder
        'discovery_score': discovery_score,
        'diversity_score': diversity_score,
        'quality_score': round(quality_score, 2),
        'review_count': review_count,
        'average_rating': round(avg_rating, 2),
        'time_window_days': time_window_days
    }
    
    logger.info(f"Recommendation quality score: {quality_score:.2f}")
    return result


def get_movie_influence_metrics(movie_id: int) -> Dict[str, Any]:
    """Get detailed influence metrics for a specific movie.
    
    Args:
        movie_id: Movie ID to analyze
    
    Returns:
        Dict with movie influence metrics:
        - total_reviews: Number of reviews
        - average_rating: Average rating
        - reviewer_influence: Average influence of reviewers
        - community_reach: Number of communities the movie appears in
        - influence_score: Overall influence score
    """
    logger.info(f"Calculating influence metrics for movie {movie_id}")
    
    try:
        movie = Movie.objects.get(id=movie_id)
    except Movie.DoesNotExist:
        logger.error(f"Movie {movie_id} not found")
        return {'error': 'Movie not found'}
    
    # Get movie reviews
    from django.contrib.contenttypes.models import ContentType
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    reviews = Review.objects.filter(
        content_type=movie_ct,
        object_id=movie_id
    ).select_related('user')
    
    total_reviews = reviews.count()
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0.0
    
    # Get unique reviewers
    reviewers = reviews.values_list('user_id', flat=True).distinct()
    reviewer_count = len(reviewers)
    
    # Calculate influence score based on popularity and rating
    popularity_score = min(total_reviews / 50.0, 1.0) * 50  # 50 reviews = max
    quality_score = (avg_rating / 10.0) * 50  # 10 rating = max
    
    influence_score = popularity_score + quality_score
    
    result = {
        'movie_id': movie_id,
        'title': movie.title,
        'total_reviews': total_reviews,
        'average_rating': round(avg_rating, 2),
        'reviewer_count': reviewer_count,
        'influence_score': round(influence_score, 2),
        'popularity_score': round(popularity_score, 2),
        'quality_score': round(quality_score, 2),
    }
    
    logger.info(f"Movie influence score: {influence_score:.2f}")
    return result


def get_user_influence_metrics(user_id: int) -> Dict[str, Any]:
    """Get detailed influence metrics for a specific user.
    
    Args:
        user_id: User ID to analyze
    
    Returns:
        Dict with user influence metrics:
        - total_reviews: Number of reviews written
        - average_rating: User's average rating
        - review_diversity: How varied their ratings are
        - influence_score: Overall influence score
    """
    logger.info(f"Calculating influence metrics for user {user_id}")
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found")
        return {'error': 'User not found'}
    
    # Get user reviews
    from django.contrib.contenttypes.models import ContentType
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    reviews = Review.objects.filter(
        user=user,
        content_type=movie_ct
    )
    
    total_reviews = reviews.count()
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0.0
    
    # Calculate rating diversity (variance)
    ratings = list(reviews.values_list('rating', flat=True))
    rating_variance = 0.0
    if len(ratings) > 1:
        mean_rating = sum(ratings) / len(ratings)
        rating_variance = sum((r - mean_rating) ** 2 for r in ratings) / len(ratings)
    
    # Calculate influence score
    activity_score = min(total_reviews / 100.0, 1.0) * 50  # 100 reviews = max
    diversity_score = min(rating_variance / 4.0, 1.0) * 30  # variance of 4 = max
    consistency_score = (avg_rating / 10.0) * 20  # rating quality
    
    influence_score = activity_score + diversity_score + consistency_score
    
    result = {
        'user_id': user_id,
        'username': user.username,
        'total_reviews': total_reviews,
        'average_rating': round(avg_rating, 2),
        'rating_variance': round(rating_variance, 2),
        'influence_score': round(influence_score, 2),
        'activity_score': round(activity_score, 2),
        'diversity_score': round(diversity_score, 2),
        'consistency_score': round(consistency_score, 2),
    }
    
    logger.info(f"User influence score: {influence_score:.2f}")
    return result
