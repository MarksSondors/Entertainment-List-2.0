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
    current_user: User,
    *,
    min_reviews: int = 2,
    rating_threshold: float = 7.0,
    max_nodes: int = 500,
    chaos_mode: bool = False,
    show_countries: bool = True,
    show_genres: bool = True,
    show_directors: bool = True,
    show_predictions: bool = True,
    predictions_limit: int = 10,
    movie_limit: int = 100,
    show_similarity: bool = True,
    show_actors: bool = False,
    show_crew: bool = False
) -> Dict[str, Any]:
    """Build network graph with all Phase 2A performance optimizations.
    
    This is a drop-in replacement for the legacy build_network_graph function
    that adds:
    - Execution timing and logging
    - 1-hour result caching
    - Memory tracking and management
    - Automatic graph sampling for large datasets
    - Performance statistics in response
    
    Args:
        current_user: The user requesting the graph
        min_reviews: Minimum reviews for user/movie inclusion (default: 2)
        rating_threshold: Minimum rating for connections (default: 7.0)
        max_nodes: Maximum nodes before sampling kicks in (default: 500)
        chaos_mode: Random layout vs structured (default: False)
        show_countries: Include country nodes (default: True)
        show_genres: Include genre nodes (default: True)
        show_directors: Include director nodes (default: True)
        show_predictions: Include prediction nodes (default: True)
        predictions_limit: Max predictions to show (default: 10)
        movie_limit: Max movies to include (default: 100)
        show_similarity: Show similarity edges (default: True)
        show_actors: Include actor nodes (default: False)
        show_crew: Include crew nodes (default: False)
    
    Returns:
        Dict containing:
        - nodes: List of node dicts
        - edges: List of edge dicts
        - stats: Graph statistics
        - performance: Performance metrics (memory, timing, sampling)
        - layout_config: MultiGravity Force Atlas configuration
    
    Performance Notes:
        - Results cached for 1 hour per user/parameter combination
        - Memory usage tracked and logged
        - Auto-sampling applied if graph exceeds memory limits
        - Execution time logged for monitoring
    """
    # Track initial memory
    initial_memory = get_memory_usage()
    logger.info(
        f"Starting build_network_graph for user={current_user.id} "
        f"(memory: {initial_memory:.1f}MB, max_nodes: {max_nodes})"
    )
    
    # Call refactored graph builder
    result = build_network_graph_refactored(
        current_user,
        min_reviews=min_reviews,
        rating_threshold=rating_threshold,
        max_nodes=max_nodes,
        chaos_mode=chaos_mode,
        show_countries=show_countries,
        show_genres=show_genres,
        show_directors=show_directors,
        show_predictions=show_predictions,
        predictions_limit=predictions_limit,
        movie_limit=movie_limit,
        show_similarity=show_similarity,
        show_actors=show_actors,
        show_crew=show_crew
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
