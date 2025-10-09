"""Network Graph Analysis Package for Entertainment List.

This package provides comprehensive network graph analysis and visualization
for movies, users, and their relationships.

Main Functions:
    - build_network_graph: Build complete network graph with Phase 2A optimizations
    - build_movie_analytics_graph_context: Build analytics-focused graph with optimizations
    - detect_communities_leiden: Detect communities using Leiden algorithm
    - calculate_centrality_measures: Calculate node centrality measures

IMPORTANT: Use the optimized functions from graph_builder module for production.
These include caching, memory management, and performance monitoring.

For backward compatibility, all functions from the original network_graph.py
are available directly from this package.
"""

import logging

# Re-export everything for backward compatibility
# This allows existing imports like:
#   from ..services.network_graph import build_network_graph
# to continue working without changes

logger = logging.getLogger(__name__)

# Import utilities and constants
from .constants import *
from .cache import (
    _get_cache_key,
    get_cached,
    set_cached,
    get_or_compute,
    invalidate_graph_caches,
    cached,
    CacheLevel,
    warm_cache_for_user,
)
from .utils import (
    _safe_divide,
    _clamp,
    _batch_process,
    calculate_node_size,
)

# Import algorithms
from .algorithms import (
    # Similarity
    cosine_similarity,
    pearson_correlation,
    jaccard_similarity,
    adjusted_cosine_similarity,
    get_user_similarity_matrix,
    
    # Community Detection
    leiden_communities,
    detect_communities_leiden,
    detect_communities,
    generate_community_name,
    validate_communities,
    
    # Centrality
    calculate_centrality_measures,
    
    # Collaborative Filtering
    get_collaborative_filtering_predictions,
)

# Import layout functions
from .layout import (
    calculate_multigravity_forces,
    enhance_graph_layout,
    calculate_smart_edge_lengths,
)

# Import database queries
from .queries import (
    get_movie_stats_optimized,
    get_item_means_optimized,
    get_user_rating_matrix_optimized,
)

# Import sampling functions
from .sampling import (
    sample_graph,
    sample_nodes_by_importance,
    sample_edges_for_nodes,
    sample_by_communities,
    prune_low_weight_edges,
    prune_redundant_edges,
)

# Import memory management
from .memory import (
    check_available_memory,
    check_graph_memory_feasibility,
    memory_safeguard,
    get_adaptive_graph_limits,
    process_with_memory_management,
)

# Import performance monitoring
from .performance import (
    timed,
    memory_limited,
    get_memory_usage,
    estimate_graph_size,
    get_optimization_strategy,
)

# Import analytics (Phase 2B)
from .analytics import (
    # Influence Analysis
    calculate_influence_scores,
    get_top_influencers,
    calculate_influence_propagation,
    score_recommendation_quality,
    get_movie_influence_metrics,
    get_user_influence_metrics,
    
    # Temporal Analysis
    analyze_graph_evolution,
    detect_trend_changes,
    get_temporal_metrics,
    calculate_growth_rate,
    identify_seasonal_patterns,
    
    # Advanced Metrics
    calculate_graph_density,
    calculate_community_stability,
    calculate_user_engagement,
    calculate_network_health,
    get_comprehensive_metrics,
)

# Import optimized graph builders with Phase 2A enhancements
# These wrap the legacy functions with caching, memory management, and monitoring
from .graph_builder import (
    build_network_graph,
    build_movie_analytics_graph_context,
    build_graph,
)

logger.info("Loaded optimized graph builders with Phase 2A enhancements")
logger.info("Loaded analytics modules (Phase 2B): Influence, Temporal, Metrics")


# Public API - everything that should be importable
__all__ = [
    # Main builders (OPTIMIZED with Phase 2A)
    'build_network_graph',
    'build_movie_analytics_graph_context',
    'build_graph',  # Alias for build_network_graph
    
    # Algorithms - Similarity
    'cosine_similarity',
    'pearson_correlation',
    'jaccard_similarity',
    'adjusted_cosine_similarity',
    'get_user_similarity_matrix',
    
    # Algorithms - Community
    'leiden_communities',
    'detect_communities_leiden',
    'detect_communities',
    'generate_community_name',
    'validate_communities',
    
    # Algorithms - Centrality
    'calculate_centrality_measures',
    
    # Algorithms - Collaborative Filtering
    'get_collaborative_filtering_predictions',
    
    # Layout
    'calculate_multigravity_forces',
    'enhance_graph_layout',
    'calculate_smart_edge_lengths',
    
    # Queries
    'get_movie_stats_optimized',
    'get_item_means_optimized',
    'get_user_rating_matrix_optimized',
    
    # Sampling
    'sample_graph',
    'sample_nodes_by_importance',
    'sample_edges_for_nodes',
    'sample_by_communities',
    'prune_low_weight_edges',
    'prune_redundant_edges',
    
    # Memory Management
    'check_available_memory',
    'check_graph_memory_feasibility',
    'memory_safeguard',
    'get_adaptive_graph_limits',
    'process_with_memory_management',
    
    # Analytics - Influence
    'calculate_influence_scores',
    'get_top_influencers',
    'calculate_influence_propagation',
    'score_recommendation_quality',
    'get_movie_influence_metrics',
    'get_user_influence_metrics',
    
    # Analytics - Temporal
    'analyze_graph_evolution',
    'detect_trend_changes',
    'get_temporal_metrics',
    'calculate_growth_rate',
    'identify_seasonal_patterns',
    
    # Analytics - Metrics
    'calculate_graph_density',
    'calculate_community_stability',
    'calculate_user_engagement',
    'calculate_network_health',
    'get_comprehensive_metrics',
    
    # Performance Monitoring
    'timed',
    'memory_limited',
    'get_memory_usage',
    'estimate_graph_size',
    'get_optimization_strategy',
    
    # Cache
    '_get_cache_key',
    'get_cached',
    'set_cached',
    'get_or_compute',
    'invalidate_graph_caches',
    'cached',
    'CacheLevel',
    'warm_cache_for_user',
    
    # Utils
    '_safe_divide',
    '_clamp',
    '_batch_process',
    'calculate_node_size',
]

logger.info("Network graph package initialized")
