"""Analytics subpackage for network graph analysis.

Provides advanced analytics capabilities including:
- Influence analysis (identify key users/movies)
- Temporal analysis (track changes over time)
- Advanced metrics (community stability, engagement)
"""

from .influence import (
    calculate_influence_scores,
    get_top_influencers,
    calculate_influence_propagation,
    score_recommendation_quality,
    get_movie_influence_metrics,
    get_user_influence_metrics,
)

from .temporal import (
    analyze_graph_evolution,
    detect_trend_changes,
    get_temporal_metrics,
    calculate_growth_rate,
    identify_seasonal_patterns,
)

from .metrics import (
    calculate_graph_density,
    calculate_community_stability,
    calculate_user_engagement,
    calculate_network_health,
    get_comprehensive_metrics,
)

__all__ = [
    # Influence Analysis
    'calculate_influence_scores',
    'get_top_influencers',
    'calculate_influence_propagation',
    'score_recommendation_quality',
    'get_movie_influence_metrics',
    'get_user_influence_metrics',
    
    # Temporal Analysis
    'analyze_graph_evolution',
    'detect_trend_changes',
    'get_temporal_metrics',
    'calculate_growth_rate',
    'identify_seasonal_patterns',
    
    # Advanced Metrics
    'calculate_graph_density',
    'calculate_community_stability',
    'calculate_user_engagement',
    'calculate_network_health',
    'get_comprehensive_metrics',
]
