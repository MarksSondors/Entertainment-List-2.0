"""Advanced Metrics for Network Graphs.

Calculates sophisticated metrics including:
- Graph density and connectivity
- Community stability over time  
- User engagement scoring
- Network health indicators
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict, Counter
import statistics

import networkx as nx
from django.db.models import Count, Avg, Q
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model

from custom_auth.models import Review
from movies.models import Movie

from ..constants import (
    ENGAGEMENT_ACTIVE_DAYS_THRESHOLD,
    COMMUNITY_STABILITY_MIN_SIZE,
)
from ..utils import _safe_divide
from ..algorithms.community import detect_communities
from ..algorithms.centrality import calculate_centrality_measures

logger = logging.getLogger(__name__)
User = get_user_model()


def calculate_graph_density(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Calculate graph density metrics.
    
    Density measures how interconnected the graph is.
    Higher density = more connections between nodes.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
    
    Returns:
        Dict with density metrics:
        - density: Overall graph density (0-1)
        - average_degree: Average connections per node
        - max_degree: Maximum connections for any node
        - clustering_coefficient: How clustered the graph is
        - connected_components: Number of separate sub-graphs
    
    Example:
        >>> density = calculate_graph_density(nodes, edges)
        >>> density['density']
        0.23  # 23% of possible connections exist
    """
    logger.info(f"Calculating graph density for {len(nodes)} nodes, {len(edges)} edges")
    
    if len(nodes) < 2:
        logger.warning("Insufficient nodes for density calculation")
        return {
            'density': 0.0,
            'average_degree': 0.0,
            'max_degree': 0,
            'clustering_coefficient': 0.0,
            'connected_components': 0,
        }
    
    # Build NetworkX graph
    G = nx.Graph()
    for node in nodes:
        G.add_node(node['id'], **node)
    for edge in edges:
        G.add_edge(edge['source'], edge['target'])
    
    # Calculate metrics
    density = nx.density(G)
    degrees = dict(G.degree())
    avg_degree = statistics.mean(degrees.values()) if degrees else 0.0
    max_degree = max(degrees.values()) if degrees else 0
    
    # Clustering coefficient (expensive for large graphs)
    if len(nodes) < 1000:
        clustering = nx.average_clustering(G)
    else:
        # Sample for large graphs
        sample_nodes = list(G.nodes())[:500]
        clustering = nx.average_clustering(G, nodes=sample_nodes)
    
    # Connected components
    num_components = nx.number_connected_components(G)
    
    result = {
        'density': round(density, 4),
        'average_degree': round(avg_degree, 2),
        'max_degree': max_degree,
        'clustering_coefficient': round(clustering, 4),
        'connected_components': num_components,
        'node_count': len(nodes),
        'edge_count': len(edges),
    }
    
    logger.info(f"Graph density: {density:.4f}, avg_degree: {avg_degree:.2f}")
    return result


def calculate_community_stability(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    previous_communities: Optional[Dict[str, int]] = None
) -> Dict[str, Any]:
    """Calculate community stability metrics.
    
    Measures how stable communities are over time.
    Useful for understanding if user groups are stable or constantly changing.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
        previous_communities: Previous community assignments (node_id -> community_id)
    
    Returns:
        Dict with stability metrics:
        - community_count: Number of communities
        - average_community_size: Average nodes per community
        - largest_community_size: Size of biggest community
        - stability_score: How much communities changed (0-1, higher = more stable)
        - communities: Current community assignments
    """
    logger.info(f"Calculating community stability for {len(nodes)} nodes")
    
    if len(nodes) < COMMUNITY_STABILITY_MIN_SIZE:
        logger.warning(f"Insufficient nodes for community analysis: {len(nodes)}")
        return {
            'community_count': 0,
            'average_community_size': 0.0,
            'largest_community_size': 0,
            'stability_score': 0.0,
        }
    
    # Detect communities using the nodes and edges directly
    try:
        community_result = detect_communities(nodes, edges)
        communities = community_result.get('communities', {})
        stats = community_result.get('stats', {})
        
        # Extract node to community mapping for stability tracking
        node_to_community = {}
        for comm_id, comm_data in communities.items():
            for node_id in comm_data.get('nodes', []):
                node_to_community[node_id] = comm_id
                
    except Exception as e:
        logger.error(f"Community detection failed: {e}")
        return {'error': str(e)}
    
    # Calculate stability score if we have previous data
    stability_score = 0.0
    if previous_communities:
        # Count nodes that stayed in the same community
        stable_nodes = 0
        total_comparable = 0
        
        for node_id, current_comm in node_to_community.items():
            if node_id in previous_communities:
                total_comparable += 1
                # Check if most of their previous community members are still together
                # (simplified: just check if node stayed in same numeric ID)
                if previous_communities[node_id] == current_comm:
                    stable_nodes += 1
        
        if total_comparable > 0:
            stability_score = stable_nodes / total_comparable
    
    # Combine all community information
    result = {
        'community_count': stats.get('num_communities', len(communities)),
        'average_community_size': round(stats.get('avg_community_size', 0.0), 2),
        'largest_community_size': stats.get('largest_community', 0),
        'smallest_community_size': stats.get('smallest_community', 0),
        'modularity': round(community_result.get('modularity', 0.0), 4),
        'method': community_result.get('method', 'unknown'),
        'stability_score': round(stability_score, 3) if previous_communities else None,
        'communities': communities,  # Full community data with names, nodes, density, etc.
        'node_to_community': node_to_community,  # Quick lookup: node_id -> community_id
        'stats': stats,  # Additional stats like coverage, community_size_std, resolution_used
    }
    
    stability_str = f"{stability_score:.3f}" if previous_communities else "N/A"
    logger.info(
        f"Communities: count={result['community_count']}, avg_size={result['average_community_size']}, "
        f"method={result['method']}, modularity={result['modularity']}, stability={stability_str}"
    )
    return result


def calculate_user_engagement(
    user_id: Optional[int] = None,
    *,
    days_back: int = ENGAGEMENT_ACTIVE_DAYS_THRESHOLD
) -> Dict[str, Any]:
    """Calculate user engagement metrics.
    
    Measures how engaged users are with the platform.
    
    Args:
        user_id: Specific user to analyze, or None for all users
        days_back: Days to look back for engagement (default: 30)
    
    Returns:
        Dict with engagement metrics:
        - active_users: Number of active users
        - total_users: Total registered users
        - engagement_rate: % of users who are active
        - average_reviews_per_user: Average reviews per active user
        - average_session_duration: Average time between first/last review
        - engagement_score: Overall 0-100 engagement score
    """
    logger.info(f"Calculating user engagement" + (f" for user {user_id}" if user_id else ""))
    
    cutoff_date = datetime.now() - timedelta(days=days_back)
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    if user_id:
        # Single user engagement
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            logger.error(f"User {user_id} not found")
            return {'error': 'User not found'}
        
        reviews = Review.objects.filter(
            user=user,
            content_type=movie_ct
        )
        
        recent_reviews = reviews.filter(date_added__gte=cutoff_date)
        total_reviews = reviews.count()
        recent_count = recent_reviews.count()
        
        is_active = recent_count > 0
        
        # Calculate session duration
        if total_reviews >= 2:
            dates = reviews.order_by('date_added').values_list('date_added', flat=True)
            first_date = dates[0]
            last_date = dates[total_reviews - 1]
            session_duration_days = (last_date - first_date).days
        else:
            session_duration_days = 0
        
        # Engagement score (0-100)
        activity_score = min(recent_count / 10.0, 1.0) * 40  # 10 reviews = max
        consistency_score = min(total_reviews / 50.0, 1.0) * 30  # 50 reviews = max
        recency_score = 30 if is_active else 0
        
        engagement_score = activity_score + consistency_score + recency_score
        
        result = {
            'user_id': user_id,
            'is_active': is_active,
            'total_reviews': total_reviews,
            'recent_reviews': recent_count,
            'session_duration_days': session_duration_days,
            'engagement_score': round(engagement_score, 2),
            'days_back': days_back,
        }
        
    else:
        # Overall platform engagement
        total_users = User.objects.count()
        
        # Active users (reviewed in last N days)
        active_user_ids = Review.objects.filter(
            content_type=movie_ct,
            date_added__gte=cutoff_date
        ).values_list('user_id', flat=True).distinct()
        
        active_users = len(set(active_user_ids))
        engagement_rate = _safe_divide(active_users, total_users)
        
        # Average reviews per active user
        if active_users > 0:
            recent_reviews = Review.objects.filter(
                content_type=movie_ct,
                date_added__gte=cutoff_date
            )
            total_recent_reviews = recent_reviews.count()
            avg_reviews_per_user = total_recent_reviews / active_users
        else:
            avg_reviews_per_user = 0.0
        
        # Overall engagement score
        participation_score = engagement_rate * 50  # 50% of score
        activity_score = min(avg_reviews_per_user / 5.0, 1.0) * 50  # 5 reviews = max
        
        engagement_score = participation_score + activity_score
        
        result = {
            'active_users': active_users,
            'total_users': total_users,
            'engagement_rate': round(engagement_rate, 3),
            'engagement_percentage': round(engagement_rate * 100, 1),
            'average_reviews_per_user': round(avg_reviews_per_user, 2),
            'engagement_score': round(engagement_score, 2),
            'days_back': days_back,
        }
    
    logger.info(f"Engagement score: {result['engagement_score']:.2f}")
    return result


def calculate_network_health(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Calculate overall network health score.
    
    Combines multiple metrics to give an overall health assessment.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
    
    Returns:
        Dict with health metrics:
        - overall_health: 0-100 health score
        - connectivity_health: Graph connectivity score
        - engagement_health: User engagement score
        - growth_health: Growth trend score
        - issues: List of detected issues
        - recommendations: List of improvement suggestions
    """
    logger.info("Calculating network health")
    
    health_issues = []
    recommendations = []
    
    # 1. Connectivity Health (30 points)
    density_metrics = calculate_graph_density(nodes, edges)
    density = density_metrics['density']
    avg_degree = density_metrics['average_degree']
    
    # Good density: 0.1-0.5, Good avg_degree: >5
    if density < 0.05:
        connectivity_health = density * 200  # Scale low densities
        health_issues.append("Low graph density - users may not be well connected")
        recommendations.append("Encourage more user interactions and reviews")
    elif density > 0.7:
        connectivity_health = 30  # Too dense is also not ideal
        health_issues.append("Very high density - may indicate small user base")
    else:
        connectivity_health = min(30, density * 60)
    
    # 2. Engagement Health (40 points)
    engagement = calculate_user_engagement()
    engagement_score = engagement.get('engagement_score', 0)
    engagement_health = min(40, engagement_score * 0.4)
    
    if engagement.get('engagement_rate', 0) < 0.2:
        health_issues.append("Low user engagement - less than 20% of users active")
        recommendations.append("Send engagement emails or notifications")
    
    # 3. Growth Health (30 points)
    # Check if we have recent activity
    movie_ct = ContentType.objects.get_for_model(Movie)
    recent_reviews = Review.objects.filter(
        content_type=movie_ct,
        date_added__gte=datetime.now() - timedelta(days=7)
    ).count()
    
    older_reviews = Review.objects.filter(
        content_type=movie_ct,
        date_added__gte=datetime.now() - timedelta(days=14),
        date_added__lt=datetime.now() - timedelta(days=7)
    ).count()
    
    if older_reviews > 0:
        growth_rate = (recent_reviews - older_reviews) / older_reviews
        if growth_rate > 0.1:
            growth_health = 30
        elif growth_rate > 0:
            growth_health = 20
        elif growth_rate > -0.2:
            growth_health = 10
            health_issues.append("Activity is declining")
            recommendations.append("Add new features or content to re-engage users")
        else:
            growth_health = 0
            health_issues.append("Significant decline in activity")
            recommendations.append("Urgent: investigate why users are leaving")
    else:
        growth_health = 15  # No data
    
    # Calculate overall health
    overall_health = connectivity_health + engagement_health + growth_health
    
    # Health status
    if overall_health >= 80:
        status = 'excellent'
    elif overall_health >= 60:
        status = 'good'
    elif overall_health >= 40:
        status = 'fair'
    else:
        status = 'poor'
    
    result = {
        'overall_health': round(overall_health, 2),
        'status': status,
        'connectivity_health': round(connectivity_health, 2),
        'engagement_health': round(engagement_health, 2),
        'growth_health': round(growth_health, 2),
        'issues': health_issues,
        'recommendations': recommendations,
        'metrics': {
            'density': density_metrics['density'],
            'average_degree': density_metrics['average_degree'],
            'engagement_rate': engagement.get('engagement_rate', 0),
            'recent_reviews': recent_reviews,
        }
    }
    
    logger.info(f"Network health: {overall_health:.2f} ({status})")
    return result


def get_comprehensive_metrics(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Get all metrics in one comprehensive report.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
    
    Returns:
        Dict with all metric categories:
        - density: Graph density metrics
        - communities: Community stability metrics
        - centrality: Node centrality measures
        - engagement: User engagement metrics
        - health: Network health assessment
        - summary: Quick overview
    """
    logger.info("Generating comprehensive metrics report")
    
    # Calculate all metrics
    density = calculate_graph_density(nodes, edges)
    communities = calculate_community_stability(nodes, edges)
    centrality = calculate_centrality_measures(nodes, edges)
    engagement = calculate_user_engagement()
    health = calculate_network_health(nodes, edges)
    
    # Create summary
    summary = {
        'node_count': len(nodes),
        'edge_count': len(edges),
        'density': density['density'],
        'community_count': communities.get('community_count', 0),
        'engagement_rate': engagement.get('engagement_rate', 0),
        'health_status': health['status'],
        'overall_health': health['overall_health'],
    }
    
    result = {
        'density': density,
        'communities': communities,
        'centrality': centrality,
        'engagement': engagement,
        'health': health,
        'summary': summary,
        'generated_at': datetime.now().isoformat(),
    }
    
    logger.info("Comprehensive metrics report generated")
    return result
