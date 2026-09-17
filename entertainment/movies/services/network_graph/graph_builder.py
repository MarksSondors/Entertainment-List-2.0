"""High-level graph builder with integrated Phase 2A performance optimizations.

This module provides the main public API for building network graphs with all
optimizations applied:
- Multi-level caching
- Memory management and auto-sampling
- Performance monitoring and logging
- Intelligent graph reduction

Use build_network_graph() as a drop-in replacement for the legacy function.
"""

import logging
from typing import Dict, List, Any, Optional
from django.contrib.auth import get_user_model

from .cache import cached, CacheLevel
from .performance import timed, get_memory_usage
from .memory import process_with_memory_management
from .analytics import (
    get_top_influencers,
    get_temporal_metrics,
    calculate_network_health,
    calculate_user_engagement,
    get_comprehensive_metrics,
)
from .builders.core import build_network_graph_refactored

logger = logging.getLogger(__name__)
User = get_user_model()


@timed
@cached(timeout=CacheLevel.MEDIUM, key_prefix='network_graph')
def build_network_graph(
    current_user: Optional[User],
    *,
    rating_threshold: float = 0.0,
    movie_limit: int = 0,
    max_nodes: int = 20000,
    show_people: bool = False,
    include_social_layer: bool = False,
    min_reviews: int = 2,
    show_predictions: bool = True,
    predictions_limit: int = 10,
    seed_tmdb_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Build a movie-centric network graph with all Phase 2A performance optimizations.

    Movies are the primary nodes, connected by explainable relationships (shared
    director/cast, keyword themes, studio, collection). The user review/prediction
    layer is an opt-in overlay via ``include_social_layer``, not the default lens.

    Adds on top of the raw graph:
    - Execution timing and logging
    - 1-hour result caching
    - Memory tracking and management
    - Automatic graph sampling for large datasets
    - Performance statistics in response

    Args:
        current_user: The user requesting the graph (only used by the social layer)
        rating_threshold: Minimum TMDB rating for seed movies (default: 6.0)
        movie_limit: Max movies to seed the graph with (default: 150)
        max_nodes: Maximum nodes before sampling kicks in (default: 500)
        show_people: Include director/actor nodes as a display-only overlay
        include_social_layer: Include user review/prediction edges as an overlay
        min_reviews: Minimum reviews for social-layer user inclusion
        show_predictions: Include collaborative-filtering prediction edges
        predictions_limit: Max predictions to show
        seed_tmdb_ids: Explicit TMDB ids to seed with (used for progressive expand)

    Returns:
        Dict containing:
        - nodes: List of node dicts
        - edges: List of edge dicts
        - compound_groups: Collections, for the frontend to render as nested boxes
        - stats: Graph statistics
        - performance: Performance metrics (memory, timing, sampling)

    Performance Notes:
        - Results cached for 1 hour per user/parameter combination
        - Memory usage tracked and logged
        - Auto-sampling applied if graph exceeds memory limits
        - Execution time logged for monitoring
    """
    # Track initial memory
    initial_memory = get_memory_usage()
    logger.info(
        f"Starting build_network_graph (movie_limit: {movie_limit}, max_nodes: {max_nodes})"
    )

    # Call refactored graph builder
    result = build_network_graph_refactored(
        current_user,
        rating_threshold=rating_threshold,
        movie_limit=movie_limit,
        max_nodes=max_nodes,
        show_people=show_people,
        include_social_layer=include_social_layer,
        min_reviews=min_reviews,
        show_predictions=show_predictions,
        predictions_limit=predictions_limit,
        seed_tmdb_ids=seed_tmdb_ids,
    )
    
    nodes = result['nodes']
    edges = result['edges']
    
    # Log pre-optimization size
    logger.info(f"Pre-optimization: {len(nodes)} nodes, {len(edges)} edges")
    
    # Apply memory-aware processing with automatic sampling
    nodes, edges, sampling_stats = process_with_memory_management(
        nodes,
        edges,
        max_nodes=max_nodes,
        max_edges=max_nodes * 2,  # Allow 2x edges relative to nodes
        auto_sample=True
    )
    
    # Log post-optimization size
    logger.info(
        f"Post-optimization: {len(nodes)} nodes, {len(edges)} edges "
        f"(sampling: {sampling_stats.get('sampling_applied', False)})"
    )
    
    # Update result with optimized data
    result['nodes'] = nodes
    result['edges'] = edges
    
    # Track final memory
    final_memory = get_memory_usage()
    
    # Add performance statistics
    result['performance'] = {
        **sampling_stats,
        'initial_memory_mb': initial_memory,
        'final_memory_mb': final_memory,
        'memory_delta_mb': final_memory - initial_memory,
        'nodes_count': len(nodes),
        'edges_count': len(edges),
    }
    
    # ========== Phase 2B: Add Analytics ==========
    logger.info("Calculating analytics metrics...")
    
    try:
        # Get comprehensive metrics (includes communities, centrality, etc.)
        comprehensive = get_comprehensive_metrics(nodes, edges)
        result['analytics'] = comprehensive
        logger.info(f"Analytics calculated: {comprehensive.get('summary', {})}")
        
        # Calculate network health
        health = calculate_network_health(nodes, edges)
        result['health'] = health
        logger.info(f"Network health: {health.get('overall_health', 'N/A')}/100 ({health.get('status', 'Unknown')})")
        
        # Get top influencers
        top_users = get_top_influencers(nodes, edges, node_type='user', top_n=10)
        top_movies = get_top_influencers(nodes, edges, node_type='movie', top_n=10)
        result['top_influencers'] = {
            'users': top_users,
            'movies': top_movies,
        }
        logger.info(f"Top influencers: {len(top_users)} users, {len(top_movies)} movies")
        
        # Get temporal metrics (last 90 days)
        temporal = get_temporal_metrics(days_back=90, include_forecasts=True)
        result['temporal_metrics'] = temporal
        logger.info(f"Temporal trend: {temporal.get('trend', 'Unknown')}")
        
        # Get user engagement (platform-wide, not node-based)
        engagement = calculate_user_engagement(days_back=30)
        result['engagement'] = engagement
        logger.info(f"User engagement: {engagement.get('engagement_score', 'N/A')}/100")
        
    except Exception as e:
        logger.error(f"Error calculating analytics: {e}", exc_info=True)
        # Don't fail the whole request if analytics fail
        result['analytics_error'] = str(e)
    
    logger.info(
        f"Completed build_network_graph: {len(nodes)} nodes, {len(edges)} edges, "
        f"memory Δ: {final_memory - initial_memory:.1f}MB"
    )
    
    return result


@timed
@cached(timeout=CacheLevel.SHORT, key_prefix='analytics_graph')
def build_movie_analytics_graph_context(max_nodes: int = 300) -> Dict[str, Any]:
    """Build analytics graph context with performance optimizations.
    
    This is an optimized wrapper around the legacy analytics graph builder
    that adds:
    - Execution timing
    - 10-minute result caching
    - Memory tracking
    - Automatic graph sampling
    
    Args:
        max_nodes: Maximum nodes before sampling (default: 300)
    
    Returns:
        Dict containing:
        - nodes: List of node dicts
        - edges: List of edge dicts  
        - movies: Movie data
        - users: User data
        - performance: Performance metrics
    
    Performance Notes:
        - Results cached for 10 minutes (shorter due to analytics)
        - Memory usage tracked
        - Auto-sampling applied if needed
    """
    # Track initial memory
    initial_memory = get_memory_usage()
    logger.info(
        f"Starting build_movie_analytics_graph_context "
        f"(memory: {initial_memory:.1f}MB)"
    )
    
    # TODO: Implement refactored version
    # For now, return empty result as this is an analytics-only function
    logger.warning("build_movie_analytics_graph_context not yet refactored - returning empty result")
    result = {
        'graph_data': {'nodes': [], 'edges': []},
        'stats': {},
        'country_stats': []
    }
    
    nodes = result.get('nodes', [])
    edges = result.get('edges', [])
    
    # Log pre-optimization size
    logger.info(f"Pre-optimization: {len(nodes)} nodes, {len(edges)} edges")
    
    # Apply memory management if needed
    if len(nodes) > max_nodes or len(edges) > max_nodes * 2:
        nodes, edges, sampling_stats = process_with_memory_management(
            nodes,
            edges,
            max_nodes=max_nodes,
            max_edges=max_nodes * 2,
            auto_sample=True
        )
        
        logger.info(
            f"Post-optimization: {len(nodes)} nodes, {len(edges)} edges "
            f"(sampling: {sampling_stats.get('sampling_applied', False)})"
        )
        
        # Update result
        result['nodes'] = nodes
        result['edges'] = edges
    else:
        sampling_stats = {'sampling_applied': False}
    
    # Track final memory
    final_memory = get_memory_usage()
    
    # Add performance statistics
    result['performance'] = {
        **sampling_stats,
        'initial_memory_mb': initial_memory,
        'final_memory_mb': final_memory,
        'memory_delta_mb': final_memory - initial_memory,
        'nodes_count': len(nodes),
        'edges_count': len(edges),
    }
    
    # ========== Phase 2B: Add Analytics ==========
    logger.info("Calculating analytics metrics for analytics graph...")
    
    try:
        # Get comprehensive metrics for analytics dashboard
        comprehensive = get_comprehensive_metrics(nodes, edges)
        result['comprehensive_metrics'] = comprehensive
        
        # Extract key metrics for quick access
        result['health'] = comprehensive.get('health', {})
        result['engagement'] = comprehensive.get('engagement', {})
        result['top_influencers'] = {
            'users': get_top_influencers(nodes, edges, node_type='user', top_n=15),
            'movies': get_top_influencers(nodes, edges, node_type='movie', top_n=15),
        }
        
        # Get temporal data for charts
        result['temporal_metrics'] = get_temporal_metrics(days_back=180, include_forecasts=True)
        
        logger.info(
            f"Analytics calculated - Health: {result['health'].get('overall_health', 'N/A')}/100, "
            f"Engagement: {result['engagement'].get('engagement_score', 'N/A')}/100"
        )
        
    except Exception as e:
        logger.error(f"Error calculating analytics: {e}", exc_info=True)
        result['analytics_error'] = str(e)
    
    logger.info(
        f"Completed build_movie_analytics_graph_context: {len(nodes)} nodes, "
        f"{len(edges)} edges, memory Δ: {final_memory - initial_memory:.1f}MB"
    )
    
    return result


# Convenience aliases for backwards compatibility
build_graph = build_network_graph

# Note: build_analytics alias removed since analytics function signature is different
