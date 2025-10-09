"""Edge optimization for network graph layouts.

This module provides functions to calculate optimal edge lengths based on
node types, graph density, and connection patterns.
"""

import logging
from typing import List, Dict

from ..types import NodeDict, EdgeDict

logger = logging.getLogger(__name__)


def calculate_smart_edge_lengths(nodes: List[NodeDict], edges: List[EdgeDict]) -> List[EdgeDict]:
    """Calculate edge lengths based on node types and graph density.
    
    This function adjusts edge lengths to create better graph layouts by:
    - Making same-type connections shorter
    - Spreading out high-degree nodes
    - Lengthening prediction and similarity edges
    - Adapting to graph density
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
    
    Returns:
        List of edge dictionaries with optimized 'length' property
        
    Example:
        >>> nodes = [
        ...     {'id': 'user_1', 'type': 'user', 'degree': 5},
        ...     {'id': 'movie_1', 'type': 'movie', 'degree': 10}
        ... ]
        >>> edges = [
        ...     {'source': 'user_1', 'target': 'movie_1', 'type': 'review'}
        ... ]
        >>> optimized_edges = calculate_smart_edge_lengths(nodes, edges)
        >>> 'length' in optimized_edges[0]
        True
    """
    # Build node lookup
    node_lookup = {node['id']: node for node in nodes}
    
    # Calculate graph density
    num_nodes = len(nodes)
    num_edges = len(edges)
    density = (2 * num_edges) / (num_nodes * (num_nodes - 1)) if num_nodes > 1 else 0
    
    # Base length increases with density to spread out dense graphs
    base_length = 80 + (density * 200)
    
    enhanced_edges = []
    for edge in edges:
        enhanced_edge = edge.copy()
        source_node = node_lookup.get(edge.get('source', edge.get('from')))
        target_node = node_lookup.get(edge.get('target', edge.get('to')))
        
        if not source_node or not target_node:
            enhanced_edges.append(enhanced_edge)
            continue
        
        # Same type connections are shorter
        if source_node['type'] == target_node['type']:
            edge_length = base_length * 0.7
        else:
            edge_length = base_length
        
        # High-degree nodes need longer edges to spread out
        source_connections = source_node.get('degree', 1)
        target_connections = target_node.get('degree', 1)
        connection_factor = 1 + (max(source_connections, target_connections) * 0.02)
        
        edge_length = edge_length * connection_factor
        
        # Prediction and similarity edges should be longer
        edge_type = enhanced_edge.get('type', '')
        if edge_type in ['prediction', 'similarity']:
            edge_length *= 1.5
        
        # Only set length if not already set by other means
        if 'length' not in enhanced_edge:
            enhanced_edge['length'] = edge_length
        
        enhanced_edges.append(enhanced_edge)
    
    logger.debug(f"Optimized edge lengths for {len(enhanced_edges)} edges (density: {density:.3f})")
    return enhanced_edges
