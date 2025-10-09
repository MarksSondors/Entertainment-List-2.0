"""Graph sampling and compression utilities for large datasets.

This module provides intelligent sampling strategies to handle graphs
that would otherwise be too large to process or visualize efficiently.
"""

import logging
import random
from typing import List, Set, Dict, Any, Tuple
from collections import defaultdict

from .types import NodeDict, EdgeDict
from .constants import MAX_NODES_DEFAULT

logger = logging.getLogger(__name__)


# ========================================
# IMPORTANCE-BASED SAMPLING
# ========================================

def calculate_node_importance(
    node: NodeDict,
    edges: List[EdgeDict],
    node_connections: Dict[Any, int]
) -> float:
    """Calculate importance score for a node.
    
    Args:
        node: Node dictionary
        edges: List of all edges
        node_connections: Dictionary mapping node IDs to connection counts
    
    Returns:
        Importance score (higher = more important)
    """
    node_id = node['id']
    
    # Factors affecting importance:
    # 1. Degree (number of connections)
    degree = node_connections.get(node_id, 0)
    degree_score = min(degree / 10.0, 5.0)  # Cap at 5.0
    
    # 2. Node type importance (some types are more central)
    type_weights = {
        'user': 1.0,
        'movie': 1.2,      # Movies slightly more important
        'genre': 0.8,
        'country': 0.7,
        'director': 0.9,
        'actor': 0.6,
        'crew': 0.5,
    }
    type_score = type_weights.get(node.get('type', 'unknown'), 0.5)
    
    # 3. Metadata indicators
    metadata_score = 0.0
    if 'review_count' in node:
        metadata_score += min(node['review_count'] / 10.0, 2.0)
    if 'movie_count' in node:
        metadata_score += min(node['movie_count'] / 5.0, 1.5)
    
    # 4. Centrality if available
    centrality_score = 0.0
    if 'centrality' in node:
        centrality = node['centrality']
        centrality_score = (
            centrality.get('degree_centrality', 0) * 2.0 +
            centrality.get('betweenness_centrality', 0) * 3.0 +
            centrality.get('pagerank', 0) * 2.0
        )
    
    # Combined score
    importance = degree_score + type_score + metadata_score + centrality_score
    
    return importance


def sample_nodes_by_importance(
    nodes: List[NodeDict],
    edges: List[EdgeDict],
    max_nodes: int = MAX_NODES_DEFAULT,
    preserve_types: bool = True
) -> Tuple[List[NodeDict], Set[Any]]:
    """Sample nodes based on importance scores.
    
    Args:
        nodes: List of all nodes
        edges: List of all edges
        max_nodes: Maximum number of nodes to keep
        preserve_types: Ensure representation from all node types
    
    Returns:
        Tuple of (sampled nodes list, set of kept node IDs)
    """
    if len(nodes) <= max_nodes:
        return nodes, {node['id'] for node in nodes}
    
    logger.info(f"Sampling {len(nodes)} nodes down to {max_nodes}")
    
    # Calculate node connections
    node_connections = defaultdict(int)
    for edge in edges:
        source = edge.get('source', edge.get('from'))
        target = edge.get('target', edge.get('to'))
        if source:
            node_connections[source] += 1
        if target:
            node_connections[target] += 1
    
    # Calculate importance for each node
    node_importance = [
        (node, calculate_node_importance(node, edges, node_connections))
        for node in nodes
    ]
    
    # Sort by importance
    node_importance.sort(key=lambda x: x[1], reverse=True)
    
    if preserve_types:
        # Ensure we keep some nodes of each type
        sampled = []
        sampled_ids = set()
        nodes_by_type = defaultdict(list)
        
        for node, importance in node_importance:
            node_type = node.get('type', 'unknown')
            nodes_by_type[node_type].append((node, importance))
        
        # Reserve slots for each type (proportional to their presence)
        type_counts = {t: len(nodes_list) for t, nodes_list in nodes_by_type.items()}
        total_count = sum(type_counts.values())
        
        for node_type, nodes_list in nodes_by_type.items():
            # Allocate proportional number of slots
            proportion = type_counts[node_type] / total_count
            type_limit = max(1, int(max_nodes * proportion))
            
            # Take top nodes of this type
            for node, importance in nodes_list[:type_limit]:
                if len(sampled) < max_nodes:
                    sampled.append(node)
                    sampled_ids.add(node['id'])
        
        # Fill remaining slots with highest importance nodes
        if len(sampled) < max_nodes:
            for node, importance in node_importance:
                if node['id'] not in sampled_ids:
                    sampled.append(node)
                    sampled_ids.add(node['id'])
                    if len(sampled) >= max_nodes:
                        break
    else:
        # Simple importance-based sampling
        sampled = [node for node, _ in node_importance[:max_nodes]]
        sampled_ids = {node['id'] for node in sampled}
    
    logger.info(f"Sampled {len(sampled)} nodes ({len(sampled)/len(nodes)*100:.1f}% of original)")
    return sampled, sampled_ids


def sample_edges_for_nodes(
    edges: List[EdgeDict],
    kept_node_ids: Set[Any],
    max_edges: int = None  # None = unlimited
) -> List[EdgeDict]:
    """Keep only edges between sampled nodes.
    
    Args:
        edges: List of all edges
        kept_node_ids: Set of node IDs that were kept
        max_edges: Maximum number of edges to keep (None for unlimited)
    
    Returns:
        List of filtered edges
    """
    # Filter edges to only include those between kept nodes
    filtered_edges = []
    for edge in edges:
        source = edge.get('source', edge.get('from'))
        target = edge.get('target', edge.get('to'))
        
        if source in kept_node_ids and target in kept_node_ids:
            filtered_edges.append(edge)
    
    # If max_edges is set and we have too many edges, prioritize by weight/importance
    if max_edges is not None and len(filtered_edges) > max_edges:
        logger.info(f"Further sampling {len(filtered_edges)} edges down to {max_edges}")
        
        # Sort by weight (if available) or edge type importance
        edge_importance = []
        for edge in filtered_edges:
            importance = edge.get('weight', 0.5)
            
            # Boost certain edge types
            edge_type = edge.get('type', '')
            if edge_type in ['review', 'similarity']:
                importance += 0.3
            elif edge_type in ['prediction', 'directed_by']:
                importance += 0.2
            
            edge_importance.append((edge, importance))
        
        edge_importance.sort(key=lambda x: x[1], reverse=True)
        filtered_edges = [edge for edge, _ in edge_importance[:max_edges]]
    
    logger.info(f"Kept {len(filtered_edges)} edges")
    return filtered_edges


def sample_graph(
    nodes: List[NodeDict],
    edges: List[EdgeDict],
    max_nodes: int = MAX_NODES_DEFAULT,
    max_edges: int = None,  # None = unlimited
    preserve_types: bool = True
) -> Tuple[List[NodeDict], List[EdgeDict], Dict[str, Any]]:
    """Sample a graph to a manageable size.
    
    Args:
        nodes: List of all nodes
        edges: List of all edges
        max_nodes: Maximum number of nodes
        max_edges: Maximum number of edges (None for unlimited)
        preserve_types: Ensure all node types are represented
    
    Returns:
        Tuple of (sampled nodes, sampled edges, sampling stats)
    """
    original_nodes = len(nodes)
    original_edges = len(edges)
    
    # Sample nodes
    sampled_nodes, kept_node_ids = sample_nodes_by_importance(
        nodes, edges, max_nodes, preserve_types
    )
    
    # Sample edges
    sampled_edges = sample_edges_for_nodes(edges, kept_node_ids, max_edges)
    
    # Generate statistics
    stats = {
        'original_nodes': original_nodes,
        'sampled_nodes': len(sampled_nodes),
        'original_edges': original_edges,
        'sampled_edges': len(sampled_edges),
        'node_retention': len(sampled_nodes) / original_nodes if original_nodes > 0 else 0,
        'edge_retention': len(sampled_edges) / original_edges if original_edges > 0 else 0,
        'sampling_applied': True,
    }
    
    logger.info(
        f"Graph sampling complete: "
        f"nodes {original_nodes}→{len(sampled_nodes)} ({stats['node_retention']*100:.1f}%), "
        f"edges {original_edges}→{len(sampled_edges)} ({stats['edge_retention']*100:.1f}%)"
    )
    
    return sampled_nodes, sampled_edges, stats


# ========================================
# EDGE PRUNING
# ========================================

def prune_low_weight_edges(
    edges: List[EdgeDict],
    min_weight: float = 0.1
) -> List[EdgeDict]:
    """Remove edges with very low weights.
    
    Args:
        edges: List of edges
        min_weight: Minimum weight to keep
    
    Returns:
        List of pruned edges
    """
    pruned = [edge for edge in edges if edge.get('weight', 1.0) >= min_weight]
    removed = len(edges) - len(pruned)
    
    if removed > 0:
        logger.info(f"Pruned {removed} low-weight edges (threshold: {min_weight})")
    
    return pruned


def prune_redundant_edges(
    edges: List[EdgeDict],
    keep_strongest: bool = True
) -> List[EdgeDict]:
    """Remove redundant edges between the same node pairs.
    
    Args:
        edges: List of edges
        keep_strongest: Keep edge with highest weight
    
    Returns:
        List of unique edges
    """
    # Group edges by node pair
    edge_groups = defaultdict(list)
    for edge in edges:
        source = edge.get('source', edge.get('from'))
        target = edge.get('target', edge.get('to'))
        
        # Normalize edge direction (undirected)
        pair = tuple(sorted([source, target]))
        edge_groups[pair].append(edge)
    
    # Keep one edge per pair
    pruned = []
    for pair, group_edges in edge_groups.items():
        if keep_strongest:
            # Keep edge with highest weight
            best_edge = max(group_edges, key=lambda e: e.get('weight', 0))
        else:
            # Keep first edge
            best_edge = group_edges[0]
        
        pruned.append(best_edge)
    
    removed = len(edges) - len(pruned)
    if removed > 0:
        logger.info(f"Removed {removed} redundant edges")
    
    return pruned


# ========================================
# COMMUNITY-BASED SAMPLING
# ========================================

def sample_by_communities(
    nodes: List[NodeDict],
    edges: List[EdgeDict],
    max_nodes: int = MAX_NODES_DEFAULT,
    nodes_per_community: int = 50
) -> Tuple[List[NodeDict], List[EdgeDict]]:
    """Sample nodes proportionally from each community.
    
    Args:
        nodes: List of nodes (should have 'community' attribute)
        edges: List of edges
        max_nodes: Maximum number of nodes
        nodes_per_community: Target nodes per community
    
    Returns:
        Tuple of (sampled nodes, sampled edges)
    """
    # Group nodes by community
    communities = defaultdict(list)
    nodes_without_community = []
    
    for node in nodes:
        if 'community' in node:
            communities[node['community']].append(node)
        else:
            nodes_without_community.append(node)
    
    if not communities:
        logger.warning("No community information available for sampling")
        return sample_graph(nodes, edges, max_nodes)[:2]
    
    # Sample from each community
    sampled = []
    for community_id, community_nodes in communities.items():
        sample_size = min(nodes_per_community, len(community_nodes))
        sampled.extend(random.sample(community_nodes, sample_size))
        
        if len(sampled) >= max_nodes:
            break
    
    # Add some nodes without communities
    if len(sampled) < max_nodes and nodes_without_community:
        remaining = max_nodes - len(sampled)
        sample_size = min(remaining, len(nodes_without_community))
        sampled.extend(random.sample(nodes_without_community, sample_size))
    
    # Get edges for sampled nodes
    sampled_ids = {node['id'] for node in sampled}
    sampled_edges = sample_edges_for_nodes(edges, sampled_ids)
    
    logger.info(f"Community-based sampling: {len(sampled)} nodes from {len(communities)} communities")
    
    return sampled, sampled_edges
