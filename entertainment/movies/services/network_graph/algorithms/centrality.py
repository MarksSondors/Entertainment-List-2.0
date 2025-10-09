"""Centrality measure calculations for network graph analysis.

This module provides functions to calculate various centrality measures
that help identify the most important or influential nodes in the network.
"""

import logging
from typing import List

import networkx as nx

from ..types import NodeDict, EdgeDict, CentralityResult

logger = logging.getLogger(__name__)


def calculate_centrality_measures(nodes: List[NodeDict], edges: List[EdgeDict]) -> CentralityResult:
    """Calculate various centrality measures for the graph.

    Centrality measures help identify the most important or influential nodes:
    - Degree Centrality: Number of direct connections (popularity)
    - Betweenness Centrality: How often a node lies on shortest paths (bridge/broker)
    - Closeness Centrality: Average distance to all other nodes (accessibility)
    - Eigenvector Centrality: Connected to other important nodes (influence)
    - PageRank: Similar to eigenvector centrality with damping

    Args:
        nodes: List of node dictionaries with 'id' and other properties
        edges: List of edge dictionaries with 'from', 'to', and other properties

    Returns:
        CentralityResult containing measures, stats, and method used

    Example:
        >>> graph_data = build_network_graph(...)
        >>> centrality = calculate_centrality_measures(graph_data['nodes'], graph_data['edges'])
        >>> # Find most central users
        >>> user_centrality = {node_id: measures 
        ...     for node_id, measures in centrality['centrality_measures'].items()
        ...     if str(node_id).startswith('user_')}
    """
    try:
        # Create NetworkX graph
        G = nx.Graph()

        # Add nodes
        for node in nodes:
            G.add_node(node['id'], **{k: v for k, v in node.items() if k != 'id'})

        # Add edges
        for edge in edges:
            from_id = edge.get('source', edge.get('from'))
            to_id = edge.get('target', edge.get('to'))
            weight = edge.get('weight', 1.0)
            if from_id and to_id:
                G.add_edge(from_id, to_id, weight=weight)

        # Calculate centrality measures
        centrality_measures = {}

        # Degree centrality (normalized)
        degree_centrality = nx.degree_centrality(G)

        # Betweenness centrality (can be expensive for large graphs)
        try:
            betweenness_centrality = nx.betweenness_centrality(G, weight='weight', normalized=True)
        except Exception as e:
            logger.warning(f"Betweenness centrality with weights failed: {e}, trying without weights")
            try:
                betweenness_centrality = nx.betweenness_centrality(G, normalized=True)
            except Exception as e2:
                logger.error(f"Betweenness centrality failed: {e2}")
                betweenness_centrality = {}

        # Closeness centrality
        try:
            closeness_centrality = nx.closeness_centrality(G)
        except Exception as e:
            logger.warning(f"Closeness centrality failed: {e}")
            closeness_centrality = {}

        # Eigenvector centrality
        try:
            eigenvector_centrality = nx.eigenvector_centrality(G, weight='weight', max_iter=1000)
        except Exception as e:
            logger.warning(f"Eigenvector centrality with weights failed: {e}, trying without weights")
            try:
                eigenvector_centrality = nx.eigenvector_centrality(G, max_iter=1000)
            except Exception as e2:
                logger.warning(f"Eigenvector centrality failed: {e2}")
                eigenvector_centrality = {}

        # PageRank (good alternative to eigenvector)
        try:
            pagerank = nx.pagerank(G, weight='weight', max_iter=1000)
        except Exception as e:
            logger.warning(f"PageRank failed: {e}")
            pagerank = {}

        # Combine all measures
        for node_id in G.nodes():
            centrality_measures[node_id] = {
                'degree_centrality': degree_centrality.get(node_id, 0),
                'betweenness_centrality': betweenness_centrality.get(node_id, 0),
                'closeness_centrality': closeness_centrality.get(node_id, 0),
                'eigenvector_centrality': eigenvector_centrality.get(node_id, 0),
                'pagerank': pagerank.get(node_id, 0),
                'degree': G.degree(node_id)
            }

        # Calculate summary statistics
        if centrality_measures:
            degree_values = [m['degree_centrality'] for m in centrality_measures.values()]
            betweenness_values = [m['betweenness_centrality'] for m in centrality_measures.values()]

            stats = {
                'avg_degree_centrality': sum(degree_values) / len(degree_values),
                'max_degree_centrality': max(degree_values),
                'avg_betweenness_centrality': sum(betweenness_values) / len(betweenness_values),
                'max_betweenness_centrality': max(betweenness_values),
                'graph_density': nx.density(G),
                'num_nodes': len(G.nodes()),
                'num_edges': len(G.edges())
            }
        else:
            stats = {}

        logger.info(f"Calculated centrality measures for {len(centrality_measures)} nodes")

        return CentralityResult(
            centrality_measures=centrality_measures,
            stats=stats,
            method='networkx'
        )

    except Exception as e:
        logger.error(f"Error calculating centrality measures: {e}", exc_info=True)
        return CentralityResult(
            centrality_measures={},
            stats={'error': str(e)},
            method='failed'
        )
