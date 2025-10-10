"""Force Atlas layout algorithm for network graphs.

This module implements a standard Force Atlas algorithm with community-based
positioning to prevent high-degree nodes from clustering in the center.
"""

import logging
import math
import random
from collections import defaultdict
from typing import Dict, List, Any

from ..types import NodeDict, EdgeDict
from ..algorithms.community import apply_community_edge_properties

logger = logging.getLogger(__name__)


def calculate_multigravity_forces(nodes: List[NodeDict], edges: List[EdgeDict]) -> Dict[str, Any]:
    """Calculate standard Force Atlas layout configuration with community-based positioning.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
    
    Returns:
        Dictionary with nodes, edges, and basic layout_config
    """
    # Calculate node connections for degree-based properties
    node_connections = defaultdict(int)
    
    for edge in edges:
        source_id = edge.get('source', edge.get('from'))
        target_id = edge.get('target', edge.get('to'))
        if source_id:
            node_connections[source_id] += 1
        if target_id:
            node_connections[target_id] += 1
    
    # Calculate graph metrics
    num_nodes = len(nodes)
    num_edges = len(edges)
    density = (2 * num_edges) / (num_nodes * (num_nodes - 1)) if num_nodes > 1 else 0
    
    # Extract communities from nodes (if available from analytics)
    communities = defaultdict(list)
    for node in nodes:
        community_id = node.get('community')
        if community_id:
            communities[community_id].append(node['id'])
    
    # Create community-based positioning hints to spread out the graph
    num_communities = len(communities)
    if num_communities > 0:
        # Calculate positions in a circle to spread communities apart
        radius = 600 + (num_communities * 50)  # Larger radius for more spread (was 300 + 20)
        
        community_positions = {}
        for idx, (comm_id, _) in enumerate(communities.items()):
            angle = (2 * math.pi * idx) / num_communities
            community_positions[comm_id] = {
                'x': radius * math.cos(angle),
                'y': radius * math.sin(angle)
            }
        
        # Add positioning hints to nodes based on their community
        enhanced_nodes = []
        for node in nodes:
            enhanced_node = node.copy()
            community_id = node.get('community')
            degree = node_connections.get(node['id'], 0)
            
            if community_id and community_id in community_positions:
                pos = community_positions[community_id]
                # Add some randomness to prevent exact overlap
                jitter_x = random.uniform(-80, 80)  # Increased jitter (was -50, 50)
                jitter_y = random.uniform(-80, 80)
                
                # High-degree nodes get pushed further from center within their community
                degree_push = min(degree * 15, 150) if degree > 5 else 0  # Increased push (was *10, 100)
                push_angle = random.uniform(0, 2 * math.pi)
                
                enhanced_node['x'] = pos['x'] + jitter_x + (degree_push * math.cos(push_angle))
                enhanced_node['y'] = pos['y'] + jitter_y + (degree_push * math.sin(push_angle))
            
            enhanced_nodes.append(enhanced_node)
    else:
        enhanced_nodes = nodes
    
    # Apply community-based edge properties (shorter/stronger within communities)
    enhanced_edges = apply_community_edge_properties(edges, enhanced_nodes)
    
    # Standard Force Atlas configuration - simple and clean
    config: Dict[str, Any] = {
        'algorithm': 'force_atlas',
        'base_repulsion': -50,
        'spring_constant': 0.08,
        'damping': 0.4,
        'theta': 0.5,
        'max_velocity': 50,
        'min_velocity': 0.1,
        'timestep': 0.5,
        'stabilization_iterations': min(1000, num_nodes * 5),
        'avoid_overlap': 0.5
    }
    
    logger.info(f"Calculated Force Atlas layout for {len(nodes)} nodes, {len(edges)} edges (density: {density:.3f}, {num_communities} communities)")
    
    return {
        'nodes': enhanced_nodes,
        'edges': enhanced_edges,
        'layout_config': config
    }


def enhance_graph_layout(nodes: List[NodeDict], edges: List[EdgeDict]) -> Dict[str, Any]:
    """Apply standard Force Atlas layout.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
    
    Returns:
        Dictionary with nodes, edges, and layout configuration
    """
    return calculate_multigravity_forces(nodes, edges)
