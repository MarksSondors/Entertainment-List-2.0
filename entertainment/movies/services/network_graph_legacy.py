"""Graph building service functions extracted from views.

Provides two main helpers:
  - build_movie_analytics_graph_context(): returns context dict for the analytics template
  - build_network_graph(...): returns dict with nodes, edges, stats for API JSON

The heavy computation is moved here to slim down view functions and allow
future reuse / unit testing.
"""

import logging
from collections import defaultdict
from itertools import groupby
from typing import Dict, List, Set, Tuple, Optional, Any
from math import sqrt, cos, sin, radians
import random
from django.db.models import Avg, Count, Q, F, Case, When, FloatField
from django.db import connection
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
import networkx as nx
import random

from custom_auth.models import CustomUser, Review
from ..models import Movie, Genre, Country  # type: ignore

# Configure logging
logger = logging.getLogger(__name__)

# Constants
CACHE_TIMEOUT = 3600  # 1 hour
MIN_SIMILARITY_THRESHOLD = 0.2
MAX_NODES_DEFAULT = 1000
MOVIE_LIMIT_DEFAULT = 500
SIMILARITY_BATCH_SIZE = 50

# Utility functions
def _get_cache_key(prefix: str, *args) -> str:
    """Generate a cache key from prefix and arguments."""
    return f"network_graph_{prefix}_{'_'.join(str(arg) for arg in args)}"

def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns default value if denominator is zero."""
    return numerator / denominator if denominator != 0 else default

def _clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    return max(min_val, min(value, max_val))

def _batch_process(items: List[Any], batch_size: int = 100):
    """Process items in batches to reduce memory usage."""
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]

# Advanced Similarity Algorithms
def cosine_similarity(vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
    """Calculate cosine similarity between two vectors."""
    if not vec1 or not vec2:
        return 0.0

    # Get intersection of keys
    common_keys = set(vec1.keys()) & set(vec2.keys())
    if not common_keys:
        return 0.0

    # Calculate dot product
    dot_product = sum(vec1[k] * vec2[k] for k in common_keys)

    # Calculate magnitudes
    norm1 = sqrt(sum(v * v for v in vec1.values()))
    norm2 = sqrt(sum(v * v for v in vec2.values()))

    return dot_product / (norm1 * norm2) if norm1 and norm2 else 0.0

def pearson_correlation(ratings1: Dict[int, float], ratings2: Dict[int, float]) -> float:
    """Calculate Pearson correlation coefficient between two rating dictionaries."""
    if not ratings1 or not ratings2:
        return 0.0

    # Find common items
    common_items = set(ratings1.keys()) & set(ratings2.keys())
    if len(common_items) < 2:
        return 0.0

    # Calculate means
    mean1 = sum(ratings1[item] for item in common_items) / len(common_items)
    mean2 = sum(ratings2[item] for item in common_items) / len(common_items)

    # Calculate correlation
    numerator = sum((ratings1[item] - mean1) * (ratings2[item] - mean2) for item in common_items)
    sum_sq1 = sum((ratings1[item] - mean1) ** 2 for item in common_items)
    sum_sq2 = sum((ratings2[item] - mean2) ** 2 for item in common_items)

    denominator = sqrt(sum_sq1 * sum_sq2)
    return numerator / denominator if denominator else 0.0

def jaccard_similarity(set1: Set[int], set2: Set[int]) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 or not set2:
        return 0.0

    intersection = len(set1 & set2)
    union = len(set1 | set2)

    return intersection / union if union else 0.0

def adjusted_cosine_similarity(ratings1: Dict[int, float], ratings2: Dict[int, float],
                              item_means: Dict[int, float]) -> float:
    """Calculate adjusted cosine similarity (subtracting item means)."""
    if not ratings1 or not ratings2:
        return 0.0

    common_items = set(ratings1.keys()) & set(ratings2.keys())
    if len(common_items) < 2:
        return 0.0

    # Calculate adjusted ratings
    adj_ratings1 = {item: ratings1[item] - item_means.get(item, 0) for item in common_items}
    adj_ratings2 = {item: ratings2[item] - item_means.get(item, 0) for item in common_items}

    return cosine_similarity(adj_ratings1, adj_ratings2)

# Database Optimization Functions
def get_movie_stats_optimized(movie_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Get movie statistics using optimized raw SQL query."""
    if not movie_ids:
        return {}

    movie_ct_id = _movie_ct().id

    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT
                object_id,
                AVG(rating) as avg_rating,
                COUNT(*) as review_count,
                STDDEV(rating) as rating_stddev
            FROM custom_auth_review
            WHERE content_type_id = %s AND object_id IN ({','.join(['%s'] * len(movie_ids))})
            GROUP BY object_id
        """, [movie_ct_id] + movie_ids)

        results = cursor.fetchall()

    return {
        row[0]: {
            'avg_rating': float(row[1]) if row[1] else 0.0,
            'review_count': row[2],
            'rating_stddev': float(row[3]) if row[3] else 0.0
        }
        for row in results
    }

def get_user_rating_matrix_optimized(user_ids: List[int]) -> Dict[int, Dict[int, float]]:
    """Get user-movie rating matrix using optimized query."""
    if not user_ids:
        return {}

    movie_ct_id = _movie_ct().id

    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT user_id, object_id, rating
            FROM custom_auth_review
            WHERE content_type_id = %s AND user_id IN ({','.join(['%s'] * len(user_ids))})
            ORDER BY user_id, object_id
        """, [movie_ct_id] + user_ids)

        results = cursor.fetchall()

    # Build rating matrix
    rating_matrix = defaultdict(dict)
    for user_id, movie_id, rating in results:
        rating_matrix[user_id][movie_id] = float(rating)

    return dict(rating_matrix)

def get_item_means_optimized(movie_ids: List[int]) -> Dict[int, float]:
    """Get average rating for each movie using optimized query."""
    if not movie_ids:
        return {}

    movie_ct_id = _movie_ct().id

    with connection.cursor() as cursor:
        cursor.execute(f"""
            SELECT object_id, AVG(rating) as avg_rating
            FROM custom_auth_review
            WHERE content_type_id = %s AND object_id IN ({','.join(['%s'] * len(movie_ids))})
            GROUP BY object_id
        """, [movie_ct_id] + movie_ids)

        results = cursor.fetchall()

    return {row[0]: float(row[1]) for row in results}

def get_user_similarity_matrix(rating_matrix: Dict[int, Dict[int, float]],
                              similarity_method: str = 'cosine',
                              item_means: Optional[Dict[int, float]] = None) -> Dict[Tuple[int, int], float]:
    """Calculate similarity matrix between all users using specified method."""
    user_ids = list(rating_matrix.keys())
    similarity_matrix = {}

    # Process in batches to avoid memory issues
    for i in range(len(user_ids)):
        for j in range(i + 1, min(i + SIMILARITY_BATCH_SIZE + 1, len(user_ids))):
            user1, user2 = user_ids[i], user_ids[j]

            if similarity_method == 'cosine':
                similarity = cosine_similarity(rating_matrix[user1], rating_matrix[user2])
            elif similarity_method == 'pearson':
                similarity = pearson_correlation(rating_matrix[user1], rating_matrix[user2])
            elif similarity_method == 'adjusted_cosine':
                if item_means is None:
                    item_means = get_item_means_optimized(list(set(rating_matrix[user1].keys()) | set(rating_matrix[user2].keys())))
                similarity = adjusted_cosine_similarity(rating_matrix[user1], rating_matrix[user2], item_means)
            elif similarity_method == 'jaccard':
                # Convert to sets of rated movies
                set1 = set(rating_matrix[user1].keys())
                set2 = set(rating_matrix[user2].keys())
                similarity = jaccard_similarity(set1, set2)
            else:
                similarity = cosine_similarity(rating_matrix[user1], rating_matrix[user2])

            if abs(similarity) >= MIN_SIMILARITY_THRESHOLD:
                similarity_matrix[(user1, user2)] = similarity

    return similarity_matrix

def get_collaborative_filtering_predictions(user_id: int,
                                          rating_matrix: Dict[int, Dict[int, float]],
                                          similarity_matrix: Dict[Tuple[int, int], float],
                                          target_movies: List[int],
                                          k: int = 10) -> Dict[int, float]:
    """Generate collaborative filtering predictions for a user."""
    if user_id not in rating_matrix:
        return {}

    user_ratings = rating_matrix[user_id]
    predictions = {}

    for movie_id in target_movies:
        if movie_id in user_ratings:
            continue  # User already rated this movie

        # Find similar users who rated this movie
        similar_users = []
        for (u1, u2), similarity in similarity_matrix.items():
            if u1 == user_id and u2 in rating_matrix and movie_id in rating_matrix[u2]:
                similar_users.append((u2, similarity))
            elif u2 == user_id and u1 in rating_matrix and movie_id in rating_matrix[u1]:
                similar_users.append((u1, similarity))

        if not similar_users:
            continue

        # Sort by similarity and take top k
        similar_users.sort(key=lambda x: abs(x[1]), reverse=True)
        similar_users = similar_users[:k]

        # Calculate weighted average
        numerator = sum(similarity * rating_matrix[other_user][movie_id]
                       for other_user, similarity in similar_users)
        denominator = sum(abs(similarity) for _, similarity in similar_users)

        if denominator > 0:
            prediction = numerator / denominator
            predictions[movie_id] = _clamp(prediction, 1.0, 10.0)

    return predictions

# Cache the ContentType lookup (expensive if repeated) at import time
_MOVIE_CT = None
def _movie_ct():  # lazy to avoid issues during migrations
    global _MOVIE_CT
    if _MOVIE_CT is None:
        _MOVIE_CT = ContentType.objects.get_for_model(Movie)
    return _MOVIE_CT


def generate_community_name(community_node_ids: List[int], all_nodes: List[Dict]) -> str:
    """Generate a meaningful name for a community based on its content.
    
    Args:
        community_node_ids: List of node IDs in the community
        all_nodes: List of all node dictionaries
    
    Returns:
        A descriptive name for the community
    """
    # Safety check for empty input
    if not community_node_ids:
        logger.warning("generate_community_name called with empty community_node_ids")
        return "Empty Community"
    
    # Create a mapping of node_id to node data
    node_mapping = {node['id']: node for node in all_nodes}
    
    # Get the actual node data for this community
    community_nodes = [node_mapping[node_id] for node_id in community_node_ids if node_id in node_mapping]
    
    if not community_nodes:
        logger.warning(f"No valid nodes found for community IDs: {community_node_ids[:5]}{'...' if len(community_node_ids) > 5 else ''}")
        return "Empty Community"
    
    # Count node types
    type_counts = {}
    genre_counts = {}
    country_counts = {}
    
    for node in community_nodes:
        node_type = node.get('type', 'unknown')
        type_counts[node_type] = type_counts.get(node_type, 0) + 1
        
        # Extract genre and country info if available
        if node_type == 'genre':
            genre_name = node.get('label', 'Unknown Genre')
            genre_counts[genre_name] = genre_counts.get(genre_name, 0) + 1
        elif node_type == 'country':
            country_name = node.get('label', 'Unknown Country')
            country_counts[country_name] = country_counts.get(country_name, 0) + 1
    
    # Determine the dominant characteristics
    total_nodes = len(community_nodes)
    dominant_type = max(type_counts.items(), key=lambda x: x[1])
    
    # Generate name based on community composition
    if dominant_type[1] / total_nodes >= 0.5:  # If one type dominates (>50%)
        if dominant_type[0] == 'genre' and genre_counts:
            top_genre = max(genre_counts.items(), key=lambda x: x[1])[0]
            return f"{top_genre} Hub"
        elif dominant_type[0] == 'country' and country_counts:
            top_country = max(country_counts.items(), key=lambda x: x[1])[0]
            return f"{top_country} Cinema"
        elif dominant_type[0] == 'user':
            return f"User Community ({total_nodes} members)"
        elif dominant_type[0] == 'movie':
            return f"Movie Cluster ({total_nodes} films)"
        elif dominant_type[0] == 'director':
            return f"Director Network ({total_nodes} directors)"
        elif dominant_type[0] == 'actor':
            return f"Actor Circle ({total_nodes} actors)"
        else:
            return f"{dominant_type[0].title()} Group"
    else:
        # Mixed community - describe by composition
        type_list = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        type_names = [f"{count} {type_name}{'s' if count > 1 else ''}" for type_name, count in type_list]
        
        if len(type_names) == 1:
            return f"Mixed Community ({type_names[0]})"
        elif len(type_names) == 2:
            return f"Mixed Community ({type_names[0]}, {type_names[1]})"
        else:
            return f"Mixed Community ({type_names[0]}, {type_names[1]}, {type_names[2]})"


def leiden_communities(G: nx.Graph, resolution: float = 1.0, random_state: Optional[int] = None, 
                      max_iterations: int = 100, tolerance: float = 1e-6) -> List[Set]:
    """
    Leiden Algorithm for Community Detection
    
    An improved version of the Louvain algorithm that guarantees well-connected communities.
    
    The algorithm works in three phases:
    1. Move nodes to optimize modularity (like Louvain)
    2. Refine communities by splitting disconnected parts
    3. Aggregate the graph for next iteration
    
    Args:
        G: NetworkX graph to analyze
        resolution: Controls community size (higher = smaller communities)
        random_state: Seed for reproducible results
        max_iterations: Stop after this many iterations
        tolerance: Convergence threshold for modularity improvement
    
    Returns:
        List of sets, where each set contains node IDs belonging to one community
    """
    # Set random seed for reproducibility
    if random_state is not None:
        random.seed(random_state)
    
    # Handle edge cases
    if len(G) == 0:
        return []
    
    if len(G) == 1:
        return [set(G.nodes())]
    
    # ========================================
    # INITIALIZATION
    # ========================================
    # Start with each node in its own community
    node_to_community = {node: idx for idx, node in enumerate(G.nodes())}
    community_to_nodes = {idx: {node} for idx, node in enumerate(G.nodes())}
    
    # Pre-calculate graph metrics (these don't change during iterations)
    node_degrees = dict(G.degree(weight='weight'))
    total_edge_weight = sum(data.get('weight', 1.0) for _, _, data in G.edges(data=True)) * 2
    
    if total_edge_weight == 0:
        return [set(G.nodes())]
    
    # ========================================
    # HELPER FUNCTIONS
    # ========================================
    
    def compute_modularity_gain(node_id, from_community, to_community):
        """
        Calculate how much modularity improves if we move a node between communities.
        
        Modularity measures how well the graph is divided into communities.
        Higher modularity = better community structure.
        
        Args:
            node_id: The node we're considering moving
            from_community: Current community ID
            to_community: Candidate community ID
        
        Returns:
            Float representing modularity gain (positive = good move)
        """
        if from_community == to_community:
            return 0.0
        
        # Count edges from this node to each community
        edges_to_old_community = 0.0
        edges_to_new_community = 0.0
        
        for neighbor in G.neighbors(node_id):
            edge_weight = G.get_edge_data(node_id, neighbor, {}).get('weight', 1.0)
            neighbor_community = node_to_community[neighbor]
            
            if neighbor_community == from_community and neighbor != node_id:
                edges_to_old_community += edge_weight
            elif neighbor_community == to_community:
                edges_to_new_community += edge_weight
        
        # Calculate total degree of each community (excluding this node from old community)
        old_community_degree = sum(
            node_degrees[n] for n in community_to_nodes[from_community] if n != node_id
        )
        new_community_degree = sum(
            node_degrees[n] for n in community_to_nodes[to_community]
        )
        
        node_degree = node_degrees[node_id]
        
        # Modularity gain formula
        # Part 1: Change in edge density within communities
        edge_gain = (edges_to_new_community - edges_to_old_community) / total_edge_weight
        
        # Part 2: Expected change (null model correction)
        degree_penalty = resolution * node_degree * (
            new_community_degree - old_community_degree + node_degree
        ) / (total_edge_weight ** 2)
        
        return edge_gain - degree_penalty
    
    def phase1_move_nodes():
        """
        Phase 1: Local Moving
        
        Try moving each node to neighboring communities to improve modularity.
        Keep iterating until no beneficial moves are found.
        
        Returns:
            List of communities (each community is a set of nodes)
        """
        iterations = 0
        nodes_moved = True
        moves_this_iteration = 0
        
        while nodes_moved and iterations < max_iterations:
            nodes_moved = False
            moves_this_iteration = 0
            iterations += 1
            
            # Process nodes in random order to avoid bias
            node_list = list(G.nodes())
            random.shuffle(node_list)
            
            for node_id in node_list:
                current_community = node_to_community[node_id]
                
                # Find all neighboring communities
                candidate_communities = set()
                for neighbor in G.neighbors(node_id):
                    candidate_communities.add(node_to_community[neighbor])
                
                # Try moving to best neighboring community
                best_community = current_community
                best_gain = 0.0
                
                for candidate in candidate_communities:
                    if candidate == current_community:
                        continue
                    
                    gain = compute_modularity_gain(node_id, current_community, candidate)
                    
                    if gain > best_gain + tolerance:
                        best_gain = gain
                        best_community = candidate
                
                # Execute the move if beneficial
                if best_community != current_community:
                    # Remove from old community
                    community_to_nodes[current_community].discard(node_id)
                    if not community_to_nodes[current_community]:
                        del community_to_nodes[current_community]
                    
                    # Add to new community
                    community_to_nodes[best_community].add(node_id)
                    node_to_community[node_id] = best_community
                    
                    nodes_moved = True
                    moves_this_iteration += 1
            
            # Early exit if very few moves were made
            if moves_this_iteration < len(G.nodes()) * 0.01:  # Less than 1% of nodes moved
                logger.debug(f"Phase 1 early exit: only {moves_this_iteration} moves in iteration {iterations}")
                break
        
        # Return non-empty communities
        return [comm_nodes for comm_nodes in community_to_nodes.values() if comm_nodes]
    
    def phase2_refine_communities(communities_list):
        """
        Phase 2: Refinement
        
        Ensure all communities are well-connected (no disconnected sub-parts).
        This is what makes Leiden better than Louvain.
        
        Args:
            communities_list: List of communities to refine
        
        Returns:
            List of refined communities (potentially more communities than input)
        """
        refined = []
        
        for community in communities_list:
            # Single nodes are already well-connected
            if len(community) <= 1:
                if community:  # Only add non-empty
                    refined.append(community)
                continue
            
            # Check if community is a single connected component
            subgraph = G.subgraph(community)
            
            if nx.is_connected(subgraph):
                # Community is well-connected, keep it
                refined.append(community)
            else:
                # Community is disconnected, split it
                for connected_part in nx.connected_components(subgraph):
                    if connected_part:  # Only add non-empty
                        refined.append(connected_part)
        
        return refined
    
    # ========================================
    # MAIN ALGORITHM
    # ========================================
    
    previous_modularity = -1.0
    communities = [{node} for node in G.nodes()]  # Start: each node is its own community
    no_improvement_count = 0  # Track iterations without improvement
    
    for iteration in range(max_iterations):
        # Phase 1: Move nodes to improve modularity
        communities = phase1_move_nodes()
        
        # Phase 2: Split any disconnected communities
        communities = phase2_refine_communities(communities)
        
        # Rebuild mappings for next iteration
        community_to_nodes.clear()
        node_to_community.clear()
        for idx, community in enumerate(communities):
            community_to_nodes[idx] = set(community)
            for node in community:
                node_to_community[node] = idx
        
        # Calculate current modularity score
        current_modularity = nx.algorithms.community.modularity(
            G, communities, weight='weight', resolution=resolution
        )
        
        logger.debug(f"Leiden iteration {iteration}: {len(communities)} communities, modularity={current_modularity:.4f}")
        
        # Check for convergence with multiple criteria
        modularity_improvement = current_modularity - previous_modularity
        
        # Converged if modularity improvement is negligible
        if abs(modularity_improvement) < tolerance:
            no_improvement_count += 1
            if no_improvement_count >= 3:  # Require 3 iterations of no improvement
                logger.info(f"Leiden converged after {iteration + 1} iterations (no improvement)")
                break
        else:
            no_improvement_count = 0
        
        # Also stop if modularity is decreasing (shouldn't happen but safety check)
        if modularity_improvement < -tolerance:
            logger.info(f"Leiden stopped after {iteration + 1} iterations (modularity decreased)")
            break
        
        previous_modularity = current_modularity
    
    # ========================================
    # FINALIZATION
    # ========================================
    
    # Convert to list of sets and filter empty communities
    final_communities = []
    for community in communities:
        if community:
            final_communities.append(set(community) if not isinstance(community, set) else community)
    
    logger.info(f"Leiden completed: {len(final_communities)} communities with modularity {previous_modularity:.4f}")
    
    return final_communities


def detect_communities_leiden(nodes: List[Dict], edges: List[Dict], 
                            resolution: float = 1.0, random_state: Optional[int] = None) -> Dict[str, Any]:
    """
    Detect communities using the Leiden algorithm - an improved version of Louvain.
    
    The Leiden algorithm provides better quality communities and guarantees that
    communities are well-connected, addressing some limitations of the Louvain algorithm.
    
    Args:
        nodes: List of node dictionaries with 'id' and other properties
        edges: List of edge dictionaries with 'from', 'to', and other properties
        resolution: Resolution parameter for modularity optimization (default: 1.0)
        random_state: Random seed for reproducibility
    
    Returns:
        Dict containing:
        - communities: Dict mapping community_id to community info
        - stats: Basic statistics about the communities
        - method: The detection method used
        - modularity: Overall modularity score
    
    Example:
        graph_data = build_network_graph(...)
        communities = detect_communities_leiden(graph_data['nodes'], graph_data['edges'])
        
        # Access community information
        for comm_id, comm_data in communities['communities'].items():
            print(f"Community {comm_id} ({comm_data['name']}): {comm_data['size']} nodes")
            print(f"Modularity contribution: {comm_data.get('modularity', 'N/A')}")
    """
    try:
        # Create NetworkX graph
        G = nx.Graph()
        
        # Add nodes with attributes
        for node in nodes:
            G.add_node(node['id'], **{k: v for k, v in node.items() if k != 'id'})
        
        # Add edges with weights
        for edge in edges:
            from_id = edge.get('source', edge.get('from'))
            to_id = edge.get('target', edge.get('to'))
            weight = edge.get('weight', 1.0)
            
            if from_id and to_id and from_id in G and to_id in G:
                G.add_edge(from_id, to_id, weight=weight)
        
        # Handle empty or trivial graphs
        if len(G) == 0:
            return {
                'communities': {},
                'stats': {'num_communities': 0, 'modularity': 0.0},
                'method': 'leiden',
                'modularity': 0.0
            }
        
        if len(G) == 1:
            node_id = list(G.nodes())[0]
            return {
                'communities': {
                    'community_0': {
                        'nodes': [node_id],
                        'size': 1,
                        'name': 'Single Node',
                        'modularity': 0.0
                    }
                },
                'stats': {'num_communities': 1, 'modularity': 0.0},
                'method': 'leiden',
                'modularity': 0.0
            }
        
        # Run Leiden algorithm
        logger.info(f"Running Leiden algorithm on graph with {len(G)} nodes and {len(G.edges())} edges")
        communities = leiden_communities(G, resolution=resolution, random_state=random_state)
        
        # Filter out any empty communities that might have slipped through
        original_count = len(communities)
        communities = [community for community in communities if community and len(community) > 0]
        filtered_count = len(communities)
        
        if original_count != filtered_count:
            logger.warning(f"Filtered out {original_count - filtered_count} empty communities from Leiden result")
        
        # Calculate overall modularity
        overall_modularity = nx.algorithms.community.modularity(G, communities, weight='weight', resolution=resolution)
        
        # Convert to dictionary format with meaningful names
        community_dict = {}
        for i, community in enumerate(communities):
            if not community or len(community) == 0:  # Additional safety check
                continue
                
            community_id = f"community_{i}"
            community_nodes = list(community)
            
            # Generate meaningful name based on community content
            community_name = generate_community_name(community_nodes, nodes)
            
            # Calculate community-specific metrics
            subgraph = G.subgraph(community)
            internal_edges = subgraph.number_of_edges()
            total_degree = sum(dict(G.degree(weight='weight')).get(node, 0) for node in community)
            
            community_dict[community_id] = {
                'nodes': community_nodes,
                'size': len(community),
                'name': community_name,
                'modularity': overall_modularity,  # Individual modularity would need more complex calculation
                'internal_edges': internal_edges,
                'total_degree': total_degree,
                'density': (2 * internal_edges) / (len(community) * (len(community) - 1)) if len(community) > 1 else 1.0
            }
        
        # Calculate comprehensive statistics
        community_sizes = [len(c) for c in communities]
        stats = {
            'num_communities': len(communities),
            'modularity': overall_modularity,
            'avg_community_size': sum(community_sizes) / len(communities) if communities else 0,
            'largest_community': max(community_sizes, default=0),
            'smallest_community': min(community_sizes, default=0),
            'community_size_std': (sum((size - (sum(community_sizes) / len(communities))) ** 2 
                                     for size in community_sizes) / len(communities)) ** 0.5 if len(communities) > 1 else 0,
            'coverage': sum(len(c) for c in communities) / len(G) if len(G) > 0 else 0,
            'resolution_used': resolution
        }
        
        logger.info(f"Leiden algorithm found {len(communities)} communities with modularity {overall_modularity:.4f}")
        
        return {
            'communities': community_dict,
            'stats': stats,
            'method': 'leiden',
            'modularity': overall_modularity
        }
    
    except Exception as e:
        logger.error(f"Error in Leiden community detection: {e}", exc_info=True)
        return {
            'communities': {},
            'stats': {'error': str(e), 'modularity': 0.0},
            'method': 'leiden_failed',
            'modularity': 0.0
        }


def detect_communities(nodes: List[Dict], edges: List[Dict]) -> Dict[str, List]:
    """Detect communities using Leiden algorithm (preferred) or fallback methods.

    This function identifies groups of densely connected nodes in the graph,
    which can help identify clusters of users with similar movie preferences,
    groups of movies that are often watched together, etc.

    The function prioritizes the Leiden algorithm for better quality communities,
    with fallbacks to Louvain and greedy modularity methods.

    Args:
        nodes: List of node dictionaries with 'id' and other properties
        edges: List of edge dictionaries with 'from', 'to', and other properties

    Returns:
        Dict containing:
        - communities: Dict mapping community_id to community info
        - stats: Basic statistics about the communities
        - method: The detection method used

    Example:
        graph_data = build_network_graph(...)
        communities = detect_communities(graph_data['nodes'], graph_data['edges'])

        # Access community information
        for comm_id, comm_data in communities['communities'].items():
            print(f"Community {comm_id}: {comm_data['size']} nodes")
    """
    # First, try the Leiden algorithm (best quality)
    try:
        logger.info("Attempting community detection with Leiden algorithm")
        result = detect_communities_leiden(nodes, edges, resolution=1.0, random_state=42)
        if result['stats'].get('num_communities', 0) > 0:
            logger.info(f"Leiden algorithm successful: {result['stats']['num_communities']} communities found")
            return result
    except Exception as e:
        logger.warning(f"Leiden algorithm failed: {e}, falling back to alternative methods")
    
    # Fallback to traditional methods
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
            G.add_edge(from_id, to_id, weight=weight)

        # Try Louvain method first
        communities = None
        method_used = 'unknown'
        
        try:
            from networkx.algorithms.community import louvain_communities
            communities = louvain_communities(G, weight='weight', resolution=1.0)
            method_used = 'louvain'
            logger.info(f"Using Louvain method: {len(communities)} communities found")
        except ImportError:
            logger.warning("Louvain method not available, using greedy modularity")
            # Fallback to greedy modularity if Louvain not available
            from networkx.algorithms.community import greedy_modularity_communities
            communities = greedy_modularity_communities(G, weight='weight')
            method_used = 'greedy_modularity'

        # Calculate modularity for the found communities
        overall_modularity = nx.algorithms.community.modularity(G, communities, weight='weight') if communities else 0.0

        # Convert to dictionary format with meaningful names
        community_dict = {}
        for i, community in enumerate(communities):
            community_id = f"community_{i}"
            community_nodes = list(community)
            
            # Generate meaningful name based on community content
            community_name = generate_community_name(community_nodes, nodes)
            
            community_dict[community_id] = {
                'nodes': community_nodes,
                'size': len(community),
                'name': community_name,
                'modularity': overall_modularity
            }

        # Calculate basic statistics
        community_sizes = [len(c) for c in communities] if communities else []
        stats = {
            'num_communities': len(communities) if communities else 0,
            'modularity': overall_modularity,
            'avg_community_size': sum(community_sizes) / len(community_sizes) if community_sizes else 0,
            'largest_community': max(community_sizes, default=0),
            'smallest_community': min(community_sizes, default=0)
        }

        return {
            'communities': community_dict,
            'stats': stats,
            'method': method_used
        }

    except Exception as e:
        logger.error(f"Error in fallback community detection: {e}")
        return {
            'communities': {},
            'stats': {'error': str(e), 'modularity': 0.0},
            'method': 'failed'
        }


def calculate_centrality_measures(nodes: List[Dict], edges: List[Dict]) -> Dict:
    """Calculate various centrality measures for the graph.

    Centrality measures help identify the most important or influential nodes in the network:
    - Degree Centrality: Number of direct connections (popularity)
    - Betweenness Centrality: How often a node lies on shortest paths (bridge/broker)
    - Closeness Centrality: Average distance to all other nodes (accessibility)
    - Eigenvector Centrality: Connected to other important nodes (influence)
    - PageRank: Similar to eigenvector centrality with damping

    Args:
        nodes: List of node dictionaries with 'id' and other properties
        edges: List of edge dictionaries with 'from', 'to', and other properties

    Returns:
        Dict containing:
        - centrality_measures: Dict mapping node_id to centrality scores
        - stats: Summary statistics for the graph
        - method: The calculation method used

    Example:
        graph_data = build_network_graph(...)
        centrality = calculate_centrality_measures(graph_data['nodes'], graph_data['edges'])

        # Find most central users
        user_centrality = {node_id: measures for node_id, measures in centrality['centrality_measures'].items()
                          if node_id.startswith('user_')}

        # Sort by betweenness centrality (most influential connectors)
        sorted_users = sorted(user_centrality.items(),
                            key=lambda x: x[1]['betweenness_centrality'], reverse=True)
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
            G.add_edge(from_id, to_id, weight=weight)

        # Calculate centrality measures
        centrality_measures = {}

        # Degree centrality (normalized)
        degree_centrality = nx.degree_centrality(G)

        # Betweenness centrality (can be expensive for large graphs)
        try:
            betweenness_centrality = nx.betweenness_centrality(G, weight='weight', normalized=True)
        except:
            # Fallback without weights if too slow
            betweenness_centrality = nx.betweenness_centrality(G, normalized=True)

        # Closeness centrality
        try:
            closeness_centrality = nx.closeness_centrality(G)
        except:
            closeness_centrality = {}

        # Eigenvector centrality
        try:
            eigenvector_centrality = nx.eigenvector_centrality(G, weight='weight', max_iter=1000)
        except:
            try:
                # Fallback without weights
                eigenvector_centrality = nx.eigenvector_centrality(G, max_iter=1000)
            except:
                eigenvector_centrality = {}

        # PageRank (good alternative to eigenvector)
        try:
            pagerank = nx.pagerank(G, weight='weight', max_iter=1000)
        except:
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

        return {
            'centrality_measures': centrality_measures,
            'stats': stats,
            'method': 'networkx'
        }

    except Exception as e:
        logger.error(f"Error calculating centrality measures: {e}")
        return {
            'centrality_measures': {},
            'stats': {'error': str(e)},
            'method': 'failed'
        }


def build_movie_analytics_graph_context():
    """Replicates the prior logic inside movie_analytics_graph view.

    Returns:
        dict: template context with graph_data, stats, country_stats (top 10)
    """
    try:
        logger.info("Building movie analytics graph context")
        movie_ct = _movie_ct()

        # Check cache first
        cache_key = _get_cache_key("analytics_context")
        cached_result = cache.get(cache_key)
        if cached_result:
            logger.info("Returning cached analytics context")
            return cached_result

        # Prefetch related objects needed for building graph - SINGLE QUERY
        movies = list(
            Movie.objects.select_related()
            .prefetch_related('countries', 'genres', 'production_companies')
            .all()
        )

        if not movies:
            logger.warning("No movies found in database")
            return {'graph_data': {'nodes': [], 'edges': []}, 'stats': {}, 'country_stats': []}

        # Materialize reviews once (avoid per-movie .filter calls) and group in-memory
        reviews_qs = (
            Review.objects.select_related('user')
            .filter(content_type=movie_ct)
            .only('id', 'object_id', 'rating', 'review_text', 'date_added', 'user__id', 'user__username')
        )
        reviews_list = list(reviews_qs)

        logger.info(f"Processing {len(movies)} movies and {len(reviews_list)} reviews")

        # Users that actually reviewed movies (distinct over review.user_id)
        user_ids = sorted({r.user_id for r in reviews_list})
        users = list(CustomUser.objects.filter(id__in=user_ids))

        # Build mapping movie_id -> list of reviews (already in memory)
        reviews_by_movie = defaultdict(list)
        for rv in reviews_list:
            reviews_by_movie[rv.object_id].append(rv)
        # Build mapping user_id -> review count (movie specific)
        user_review_counts = defaultdict(int)
        for rv in reviews_list:
            user_review_counts[rv.user_id] += 1

        # PRE-COMPUTE all movie relationships to avoid repeated .all() calls
        movie_countries = defaultdict(list)
        movie_genres = defaultdict(list)
        country_movies = defaultdict(list)
        genre_movies = defaultdict(list)

        for movie in movies:
            movie_id = movie.id
            countries = list(movie.countries.all())
            genres = list(movie.genres.all())

            movie_countries[movie_id] = countries
            movie_genres[movie_id] = genres

            for country in countries:
                country_movies[country.id].append(movie)
            for genre in genres:
                genre_movies[genre.id].append(movie)

        nodes = []
        edges = []
        node_id_counter = 0
        node_mapping = {}

        # Users
        for user in users:  # all users already constrained to those with movie reviews
            user_id = f"user_{user.id}"
            node_mapping[user_id] = node_id_counter
            user_review_count = user_review_counts.get(user.id, 0)
            # Scale user size based on review count (base: 25, range: 20-35)
            user_size = max(20, min(35, 25 + user_review_count * 0.5))
            nodes.append({
                'id': node_id_counter,
                'label': user.username,
                'type': 'user',
                'group': 'users',
                'size': user_size,
                'color': '#FF6B6B',
                'metadata': {
                    'username': user.username,
                    'review_count': user_review_count
                }
            })
            node_id_counter += 1

        # Movies
        for movie in movies:
            movie_id = f"movie_{movie.id}"
            node_mapping[movie_id] = node_id_counter
            movie_reviews = reviews_by_movie.get(movie.id, [])
            if movie_reviews:
                avg_rating = sum(r.rating for r in movie_reviews) / len(movie_reviews)
            else:
                avg_rating = 0
            # Scale movie size based on review count (base: 18, range: 15-40)
            movie_size = max(15, min(40, 18 + len(movie_reviews) * 2))
            nodes.append({
                'id': node_id_counter,
                'label': movie.title,
                'type': 'movie',
                'group': 'movies',
                'size': movie_size,
                'color': '#4ECDC4',
                'metadata': {
                    'title': movie.title,
                    'release_date': movie.release_date.strftime('%Y') if movie.release_date else 'Unknown',
                    'rating': round(avg_rating, 1),
                    'review_count': len(movie_reviews),
                    'runtime': movie.runtime,
                    'countries': [c.name for c in movie_countries[movie.id]],
                    'genres': [g.name for g in movie_genres[movie.id]]
                }
            })
            node_id_counter += 1

        # Countries - use pre-computed data
        countries = list(country_movies.keys())
        for country_id in countries:
            country = country_movies[country_id][0].countries.filter(id=country_id).first()  # Get country object
            if not country:
                continue
            cid = f"country_{country_id}"
            node_mapping[cid] = node_id_counter
            country_movie_list = country_movies[country_id]
            # Country nodes smaller but scaled by movie count (base: 13, range: 12-20)
            country_size = max(8, min(20, 9 + len(country_movie_list) * 0.8))
            nodes.append({
                'id': node_id_counter,
                'label': country.name,
                'type': 'country',
                'group': 'countries',
                'size': country_size,
                'color': '#95E1D3',
                'metadata': {
                    'name': country.name,
                    'iso_code': country.iso_3166_1,
                    'movie_count': len(country_movie_list)
                }
            })
            node_id_counter += 1

        # Genres - use pre-computed data
        genres = list(genre_movies.keys())
        for genre_id in genres:
            genre = genre_movies[genre_id][0].genres.filter(id=genre_id).first()  # Get genre object
            if not genre:
                continue
            gid = f"genre_{genre_id}"
            node_mapping[gid] = node_id_counter
            genre_movie_list = genre_movies[genre_id]
            # Genre nodes medium size (base: 14, range: 12-22)
            genre_size = max(12, min(22, 14 + len(genre_movie_list) * 0.6))
            nodes.append({
                'id': node_id_counter,
                'label': genre.name,
                'type': 'genre',
                'group': 'genres',
                'size': genre_size,
                'color': '#F38BA8',
                'metadata': {
                    'name': genre.name,
                    'movie_count': len(genre_movie_list)
                }
            })
            node_id_counter += 1

        # Review edges (user -> movie)
        for review in reviews_list:
            uid = f"user_{review.user.id}"
            mid = f"movie_{review.object_id}"
            if uid in node_mapping and mid in node_mapping:
                if review.rating >= 8:
                    edge_color = '#2ECC71'
                elif review.rating >= 6:
                    edge_color = '#F39C12'
                else:
                    edge_color = '#E74C3C'
                edges.append({
                    'source': node_mapping[uid],
                    'target': node_mapping[mid],
                    'type': 'review',
                    'weight': review.rating / 10,
                    'color': edge_color,
                    'metadata': {
                        'rating': review.rating,
                        'review_text': review.review_text[:100] if review.review_text else None,
                        'date': review.date_added.strftime('%Y-%m-%d')
                    }
                })

        # Movie -> country - use pre-computed data
        for movie in movies:
            mid = f"movie_{movie.id}"
            for country in movie_countries[movie.id]:
                cid = f"country_{country.id}"
                if mid in node_mapping and cid in node_mapping:
                    edges.append({
                        'source': node_mapping[mid],
                        'target': node_mapping[cid],
                        'type': 'origin',
                        'weight': 0.5,
                        'color': '#BDC3C7'
                    })

        # Movie -> genre - use pre-computed data
        for movie in movies:
            mid = f"movie_{movie.id}"
            for genre in movie_genres[movie.id]:
                gid = f"genre_{genre.id}"
                if mid in node_mapping and gid in node_mapping:
                    edges.append({
                        'source': node_mapping[mid],
                        'target': node_mapping[gid],
                        'type': 'genre',
                        'weight': 0.3,
                        'color': '#9B59B6'
                    })

        # User similarity edges - OPTIMIZED VERSION
        user_ratings = defaultdict(dict)
        for review in reviews_list:
            user_ratings[review.user.id][review.object_id] = review.rating

        user_list = list(user_ratings.keys())
        # Pre-compute all pairwise similarities more efficiently
        similarity_candidates = []
        for i, u1 in enumerate(user_list):
            for u2 in user_list[i+1:]:
                common = set(user_ratings[u1]) & set(user_ratings[u2])
                if len(common) >= 3:
                    diffs = [abs(user_ratings[u1][m] - user_ratings[u2][m]) for m in common]
                    avg_diff = sum(diffs) / len(diffs)
                    similarity = 1 - (avg_diff / 10)
                    if similarity > 0.7:
                        similarity_candidates.append((u1, u2, similarity))

        # Add similarity edges
        for u1, u2, similarity in similarity_candidates:
            n1 = f"user_{u1}"
            n2 = f"user_{u2}"
            if n1 in node_mapping and n2 in node_mapping:
                edges.append({
                    'source': node_mapping[n1],
                    'target': node_mapping[n2],
                    'type': 'similarity',
                    'weight': similarity,
                    'color': '#FFD93D',
                    'metadata': {
                        'similarity': round(similarity, 2),
                        'common_movies': len(set(user_ratings[u1]) & set(user_ratings[u2]))
                    }
                })

        # Precompute movie review counts (already grouped) & global avg
        total_reviews = len(reviews_list)
        global_avg = round(sum(r.rating for r in reviews_list) / total_reviews, 2) if total_reviews else 0
        most_reviewed_movie = None
        max_movie_reviews = -1
        for m in movies:
            cnt = len(reviews_by_movie.get(m.id, []))
            if cnt > max_movie_reviews:
                max_movie_reviews = cnt
                most_reviewed_movie = m
        most_active_user = None
        max_user_reviews = -1
        for u in users:
            cnt = user_review_counts.get(u.id, 0)
            if cnt > max_user_reviews:
                max_user_reviews = cnt
                most_active_user = u
        stats = {
            'total_movies': len(movies),
            'total_users': len(users),
            'total_reviews': total_reviews,
            'total_countries': len(countries),
            'total_genres': len(genres),
            'avg_rating': global_avg,
            'most_reviewed_movie': (most_reviewed_movie, max_movie_reviews) if most_reviewed_movie else (None, 0),
            'most_active_user': (most_active_user, max_user_reviews) if most_active_user else (None, 0),
        }

        # Country stats - use pre-computed data
        country_stats = []
        for country_id in countries:
            country = country_movies[country_id][0].countries.filter(id=country_id).first()
            if not country:
                continue
            country_movie_list = country_movies[country_id]
            # Collect reviews belonging to movies from this country via pre-grouped mapping
            relevant_reviews = []
            for mv in country_movie_list:
                relevant_reviews.extend(reviews_by_movie.get(mv.id, []))
            if relevant_reviews:
                avg_rating = sum(r.rating for r in relevant_reviews) / len(relevant_reviews)
            else:
                avg_rating = 0
            country_stats.append({
                'name': country.name,
                'movie_count': len(country_movie_list),
                'avg_rating': round(avg_rating, 2)
            })
        country_stats.sort(key=lambda x: x['movie_count'], reverse=True)

        result = {
            'graph_data': {
                'nodes': nodes,
                'edges': edges
            },
            'stats': stats,
            'country_stats': country_stats[:10]
        }

        # Add advanced analytics if we have enough nodes
        if len(nodes) >= 5:
            try:
                logger.info("Running community detection and centrality analysis for analytics context")

                # Detect communities
                communities_result = detect_communities(nodes, edges)
                result['communities'] = communities_result

                # Calculate centrality measures
                centrality_result = calculate_centrality_measures(nodes, edges)
                result['centrality'] = centrality_result

                # Add community and centrality data to nodes
                for node in nodes:
                    node_id = node['id']

                    # Add community information
                    if communities_result['communities']:
                        for comm_id, comm_data in communities_result['communities'].items():
                            if node_id in comm_data['nodes']:
                                node['community'] = comm_id
                                node['community_size'] = comm_data['size']
                                break

                    # Add centrality measures
                    if node_id in centrality_result['centrality_measures']:
                        node['centrality'] = centrality_result['centrality_measures'][node_id]

                logger.info(f"Analytics completed for context: {communities_result['stats']['num_communities']} communities detected")

            except Exception as e:
                logger.error(f"Error running analytics in context: {e}")
                result['analytics_error'] = str(e)

        # Cache the result
        cache.set(cache_key, result, CACHE_TIMEOUT)
        logger.info(f"Analytics context built successfully with {len(nodes)} nodes and {len(edges)} edges")
        return result
    except Exception as e:
        logger.error(f"Error building analytics context: {str(e)}", exc_info=True)
        return {
            'graph_data': {'nodes': [], 'edges': []},
            'stats': {'error': str(e)},
            'country_stats': []
        }


def build_network_graph(
    current_user,
    *,
    min_reviews: int,
    rating_threshold: float,
    max_nodes: int,
    chaos_mode: bool,
    show_countries: bool,
    show_genres: bool,
    show_directors: bool,
    show_predictions: bool,
    predictions_limit: int,
    movie_limit: int,
    show_similarity: bool,
    show_actors: bool,
    show_crew: bool
):
    """Full network graph computation using MultiGravity Force Atlas layout."""
    import math

    nodes = []
    edges = []
    node_ids = set()
    movie_content_type = _movie_ct()

    # Active users
    # Users pre-annotated with movie review counts (avoids per-user .count())
    active_users = CustomUser.objects.annotate(
        movie_review_count=Count('reviews', filter=Q(reviews__content_type=movie_content_type))
    ).filter(movie_review_count__gte=min_reviews)
    if current_user and current_user not in active_users:
        active_users = list(active_users)
        active_users.append(current_user)
    if hasattr(active_users, 'exists') and not active_users.exists():
        active_users = CustomUser.objects.annotate(
            movie_review_count=Count('reviews', filter=Q(reviews__content_type=movie_content_type))
        ).filter(movie_review_count__gte=1)[:max_nodes//3]

    # Single query for all candidate reviews by active users above threshold
    good_reviews = (
        Review.objects.filter(
            content_type=movie_content_type,
            user__in=active_users,
            rating__gte=rating_threshold
        ).values('user_id', 'object_id', 'rating')
    )
    well_reviewed_movie_ids = {r['object_id'] for r in good_reviews}

    # Use optimized raw SQL for movie stats
    movie_stats = get_movie_stats_optimized(list(well_reviewed_movie_ids))

    well_reviewed_movies = list(
        Movie.objects.filter(id__in=movie_stats.keys())
        .prefetch_related('genres', 'countries', 'keywords')
    )[:movie_limit]

    if not well_reviewed_movies:
        # Fallback: sample movies referenced in any review (limit) with aggregated stats
        fallback_movie_cap = min(movie_limit, max_nodes // 2)
        all_movie_reviews = Review.objects.filter(content_type=movie_content_type).values('object_id', 'rating', 'user_id')
        fallback_ids = list({r['object_id'] for r in all_movie_reviews})[:fallback_movie_cap]

        fallback_stats = get_movie_stats_optimized(fallback_ids)
        movie_stats.update(fallback_stats)

        well_reviewed_movies = list(
            Movie.objects.filter(id__in=fallback_stats.keys())
            .prefetch_related('genres', 'countries', 'keywords')
        )

    # User nodes with proper sizes
    user_nodes = {}
    user_review_counts = {}
    for user in list(active_users)[:max_nodes//3]:
        node_id = f"user_{user.id}"
        user_nodes[user.id] = node_id
        node_ids.add(node_id)
        user_movie_reviews = getattr(user, 'movie_review_count', None)
        if user_movie_reviews is None:
            # fallback if annotation missing
            user_movie_reviews = Review.objects.filter(user=user, content_type=movie_content_type).count()
        user_review_counts[user.id] = user_movie_reviews
        # Scale user size based on review count (base: 20, range: 15-25)
        user_size = max(15, min(25, 20 + user_movie_reviews * 0.3))
        
        # Get profile picture URL if available
        profile_picture_url = None
        if hasattr(user, 'get_profile_picture'):
            profile_picture_url = user.get_profile_picture()
        elif hasattr(user, 'profile_picture') and user.profile_picture:
            try:
                profile_picture_url = user.profile_picture.url
            except:
                profile_picture_url = None
        
        nodes.append({
            'id': node_id,
            'label': user.username,
            'type': 'user',
            'size': user_size,
            'color': '#4299E1',
            'review_count': user_movie_reviews,
            'profile_picture': profile_picture_url
        })

    # Movie nodes with proper sizes
    movie_nodes = {}
    for movie in well_reviewed_movies:
        node_id = f"movie_{movie.id}"
        movie_nodes[movie.id] = node_id
        node_ids.add(node_id)
        stats_m = movie_stats[movie.id]
        review_count = stats_m.get('review_count', 0)
        # Scale movie size based on review count (base: 15, range: 10-30)
        movie_size = max(10, min(30, 15 + review_count * 1.5))
        nodes.append({
            'id': node_id,
            'label': movie.title,
            'type': 'movie',
            'size': movie_size,
            'color': '#F56565',
            'rating': round(stats_m['avg_rating'], 1),
            'year': movie.release_date.year if movie.release_date else None,
            'tmdb_id': movie.tmdb_id
        })

    # PRE-COMPUTE all movie relationships to avoid repeated database calls
    movie_genres = defaultdict(list)
    movie_countries = defaultdict(list)
    movie_directors = []
    movie_cast = []
    movie_crew = []

    # Build efficient lookup dictionaries
    genre_counts = defaultdict(int)
    country_counts = defaultdict(int)
    director_counts = defaultdict(int)
    actor_counts = defaultdict(int)
    crew_counts = defaultdict(int)

    for movie in well_reviewed_movies:
        movie_id = movie.id
        genres = list(movie.genres.all())
        countries = list(movie.countries.all())

        movie_genres[movie_id] = genres
        movie_countries[movie_id] = countries

        for genre in genres:
            genre_counts[genre] += 1
        for country in countries:
            country_counts[country] += 1

        # Handle directors, cast, crew if needed
        if show_directors or show_actors or show_crew:
            if hasattr(movie, 'directors'):
                directors = list(movie.directors)
                movie_directors.append((movie_id, directors))
                for director in directors:
                    director_counts[director] += 1

            if chaos_mode and (show_actors or show_crew):
                if hasattr(movie, 'cast'):
                    cast = list(movie.cast)[:8]
                    movie_cast.append((movie_id, cast))
                    for mp in cast:
                        person = getattr(mp, 'person', None)
                        if person:
                            actor_counts[person] += 1

                if hasattr(movie, 'crew'):
                    crew = [mp for mp in movie.crew[:8] if getattr(mp, 'role', '') != 'Director']
                    movie_crew.append((movie_id, crew))
                    for mp in crew:
                        person = getattr(mp, 'person', None)
                        if person:
                            crew_counts[person] += 1

    # Country nodes - use pre-computed data
    country_nodes = {}
    if show_countries:
        for country, count in country_counts.items():
            node_id = f"country_{country.id}"
            country_nodes[country.id] = node_id
            node_ids.add(node_id)
            nodes.append({
                'id': node_id,
                'label': country.name,
                'type': 'country',
                'size': max(15, min(30, 18 + count * 1.2)),
                'color': '#48BB78',
                'movie_count': count
            })

    # Genre nodes - use pre-computed data
    genre_nodes = {}
    if show_genres:
        for genre, count in genre_counts.items():
            node_id = f"genre_{genre.id}"
            genre_nodes[genre.id] = node_id
            node_ids.add(node_id)
            nodes.append({
                'id': node_id,
                'label': genre.name,
                'type': 'genre',
                'size': max(16, min(28, 20 + count * 1.0)),
                'color': '#9F7AEA',
                'movie_count': count
            })

    # Director nodes - use pre-computed data
    director_nodes = {}
    if show_directors:
        for director, count in director_counts.items():
            node_id = f"director_{director.id}"
            director_nodes[director.id] = node_id
            node_ids.add(node_id)
            # Director nodes smaller (base: 13, range: 10-18)
            director_size = max(12, min(18, 13 + count * 0.5))
            nodes.append({
                'id': node_id,
                'label': director.name,
                'type': 'director',
                'size': director_size,
                'color': '#ED8936',
                'movie_count': count
            })

    # Chaos mode actors/crew - use pre-computed data
    actor_nodes = {}
    crew_nodes = {}
    if chaos_mode and (show_actors or show_crew):
        max_actor_nodes = max_nodes // 2
        max_crew_nodes = max_nodes // 3
        sorted_actors = sorted(actor_counts.items(), key=lambda x: x[1], reverse=True)[:max_actor_nodes]
        sorted_crew = sorted(crew_counts.items(), key=lambda x: x[1], reverse=True)[:max_crew_nodes]
        if show_actors:
            for person, count in sorted_actors:
                if person.id in director_nodes or person.id in actor_nodes or person.id in crew_nodes:
                    continue
                node_id = f"actor_{person.id}"
                actor_nodes[person.id] = node_id
                # Actor nodes smaller (base: 9, range: 8-12)
                actor_size = max(8, min(12, 9 + count * 0.3))
                nodes.append({
                    'id': node_id,
                    'label': person.name,
                    'type': 'actor',
                    'size': actor_size,
                    'color': '#FFD700',
                    'movie_count': count
                })
        if show_crew:
            for person, count in sorted_crew:
                if person.id in director_nodes or person.id in actor_nodes or person.id in crew_nodes:
                    continue
                node_id = f"crew_{person.id}"
                crew_nodes[person.id] = node_id
                # Crew nodes smallest (base: 9, range: 8-14)
                crew_size = max(8, min(14, 9 + count * 0.4))
                nodes.append({
                    'id': node_id,
                    'label': person.name,
                    'type': 'crew',
                    'size': crew_size,
                    'color': '#00CED1',
                    'movie_count': count
                })

    # User -> Movie edges
    if user_review_counts:
        sorted_counts = sorted(user_review_counts.values())
        median_count = sorted_counts[len(sorted_counts)//2]
        max_count = sorted_counts[-1]
        hub_user_flag = max_count >= max(20, median_count * 3)
    else:
        median_count = 0
        max_count = 0
        hub_user_flag = False
    # Group the good reviews by user to avoid per-user queries
    good_reviews_by_user = defaultdict(list)
    for gr in good_reviews:
        good_reviews_by_user[gr['user_id']].append(gr)
    for user in active_users:
        uid = user.id
        if uid not in user_nodes:
            continue
        for review_dict in good_reviews_by_user.get(uid, []):
            movie_id = review_dict['object_id']
            if movie_id in movie_nodes:
                rc = user_review_counts.get(uid, 1)
                dynamic_length = int(160 + 28 * math.log10(rc + 9))
                if hub_user_flag and rc == max_count:
                    dynamic_length += 80
                rating_val = review_dict['rating']
                edges.append({
                    'source': user_nodes[uid],
                    'target': movie_nodes[movie_id],
                    'type': 'review',
                    'weight': rating_val / 10.0,
                    'color': {'color': '#4299E1', 'opacity': 0.6},
                    'width': 2,
                    'label': f"{rating_val}/10"
                })

    # Movie -> Country - use pre-computed data
    if show_countries:
        for movie in well_reviewed_movies:
            if movie.id not in movie_nodes:
                continue
            for country in movie_countries[movie.id]:
                if country.id in country_nodes:
                    edges.append({
                        'source': movie_nodes[movie.id],
                        'target': country_nodes[country.id],
                        'type': 'origin',
                        'color': {'color': '#48BB78', 'opacity': 0.4},
                        'width': 2,
                        'length': 110
                    })

    # Movie -> Genre - use pre-computed data
    if show_genres:
        for movie in well_reviewed_movies:
            if movie.id not in movie_nodes:
                continue
            for genre in movie_genres[movie.id]:
                if genre.id in genre_nodes:
                    edges.append({
                        'source': movie_nodes[movie.id],
                        'target': genre_nodes[genre.id],
                        'type': 'genre',
                        'color': {'color': '#9F7AEA', 'opacity': 0.4},
                        'width': 1.5,
                        'length': 85
                    })

    # Movie -> Director - use pre-computed data
    if show_directors:
        for movie_id, directors in movie_directors:
            if movie_id not in movie_nodes:
                continue
            for director in directors:
                if director.id in director_nodes:
                    edges.append({
                        'source': movie_nodes[movie_id],
                        'target': director_nodes[director.id],
                        'type': 'directed_by',
                        'color': {'color': '#ED8936', 'opacity': 0.5},
                        'width': 2
                    })

    # Chaos Movie -> Actor / Crew - use pre-computed data
    if chaos_mode:
        if show_actors:
            for movie_id, cast in movie_cast:
                if movie_id not in movie_nodes:
                    continue
                for mp in cast:
                    person = getattr(mp, 'person', None)
                    if not person or person.id not in actor_nodes:
                        continue
                    edges.append({
                        'source': movie_nodes[movie_id],
                        'target': actor_nodes[person.id],
                        'type': 'acted_in',
                        'color': {'color': '#FFD700', 'opacity': 0.35},
                        'width': 1
                    })
        if show_crew:
            for movie_id, crew in movie_crew:
                if movie_id not in movie_nodes:
                    continue
                for mp in crew:
                    if getattr(mp, 'role', '') == 'Director':
                        continue
                    person = getattr(mp, 'person', None)
                    if not person or person.id not in crew_nodes:
                        continue
                    edges.append({
                        'source': movie_nodes[movie_id],
                        'target': crew_nodes[person.id],
                        'type': 'crew',
                        'color': {'color': '#00CED1', 'opacity': 0.35},
                        'width': 1
                    })

    # Affinity edges for people to genre/country - OPTIMIZED VERSION
    if (show_genres or show_countries) and (genre_nodes or country_nodes):
        # Pre-compute person -> movie mappings for O(1) lookups
        person_movies = defaultdict(set)

        # Build director -> movies mapping
        for movie_id, directors in movie_directors:
            for director in directors:
                person_movies[director.id].add(movie_id)

        # Build actor -> movies mapping
        for movie_id, cast in movie_cast:
            for mp in cast:
                person = getattr(mp, 'person', None)
                if person:
                    person_movies[person.id].add(movie_id)

        def _add_affinity_edges_optimized(node_map, person_type, max_genres=3, max_countries=2):
            for person_id, node_id in node_map.items():
                if person_id not in person_movies:
                    continue

                genre_counter = defaultdict(int)
                country_counter = defaultdict(int)
                person_movie_ids = person_movies[person_id]

                for movie_id in person_movie_ids:
                    if movie_id not in movie_genres or movie_id not in movie_countries:
                        continue

                    if show_genres:
                        for genre in movie_genres[movie_id]:
                            if genre.id in genre_nodes:
                                genre_counter[genre.id] += 1
                    if show_countries:
                        for country in movie_countries[movie_id]:
                            if country.id in country_nodes:
                                country_counter[country.id] += 1

                total_movies = len(person_movie_ids)
                if total_movies == 0:
                    continue

                if show_genres and genre_counter:
                    genre_items = sorted(genre_counter.items(), key=lambda x: x[1], reverse=True)[:max_genres]
                    max_g = max(v for _, v in genre_items) or 1
                    for gid, cnt in genre_items:
                        strength = cnt / max_g
                        if strength < 0.2:
                            continue
                        edges.append({
                            'source': node_id,
                            'target': genre_nodes[gid],
                            'type': f'{person_type}_genre_affinity',
                            'color': {'color': '#B794F4' if person_type=='actor' else '#D69E2E', 'opacity': 0.32},
                            'width': 0.8 + strength * 2.2,
                            'dashes': True,
                            'length': int(270 - strength * 140),
                            'label': f"{int(strength*100)}%"
                        })

                if show_countries and country_counter:
                    country_items = sorted(country_counter.items(), key=lambda x: x[1], reverse=True)[:max_countries]
                    max_c = max(v for _, v in country_items) or 1
                    for cid, cnt in country_items:
                        strength = cnt / max_c
                        if strength < 0.25:
                            continue
                        edges.append({
                            'source': node_id,
                            'target': country_nodes[cid],
                            'type': f'{person_type}_country_affinity',
                            'color': {'color': '#68D391' if person_type=='actor' else '#ED8936', 'opacity': 0.30},
                            'width': 0.8 + strength * 2.0,
                            'dashes': True,
                            'length': int(280 - strength * 150),
                            'label': f"{int(strength*100)}%"
                        })

        if show_actors and actor_nodes:
            _add_affinity_edges_optimized(actor_nodes, 'actor')
        if show_directors and director_nodes:
            _add_affinity_edges_optimized(director_nodes, 'director')

    # User similarity & preferences - ADVANCED ALGORITHMS
    # Build user -> liked movie set from grouped reviews (fast, no extra queries)
    user_movie_preferences = {uid: {r['object_id'] for r in rs} for uid, rs in good_reviews_by_user.items() if uid in user_nodes}

    # Get optimized rating matrix for advanced similarity calculations
    active_user_ids = list(user_nodes.keys())
    rating_matrix = get_user_rating_matrix_optimized(active_user_ids)

    user_list = list(user_movie_preferences.keys())
    current_user_similarities = {}

    if show_similarity or show_predictions:
        # Use advanced similarity algorithms
        logger.info(f"Calculating similarities for {len(user_list)} users using advanced algorithms")

        # Calculate similarity matrix using cosine similarity (can be changed to other methods)
        similarity_matrix = get_user_similarity_matrix(
            rating_matrix,
            similarity_method='cosine',  # Options: 'cosine', 'pearson', 'adjusted_cosine', 'jaccard'
            item_means=None  # Will be calculated if needed for adjusted_cosine
        )

        # Create similarity edges
        for (user1_id, user2_id), similarity in similarity_matrix.items():
            if user1_id in user_nodes and user2_id in user_nodes:
                if show_similarity:
                    edges.append({
                        'source': user_nodes[user1_id],
                        'target': user_nodes[user2_id],
                        'type': 'similarity',
                        'weight': similarity,
                        'color': {'color': '#38B2AC', 'opacity': 0.5},
                        'width': max(1, abs(similarity) * 4),
                        'label': f"{int(abs(similarity)*100)}% similar",
                        'dashes': True
                    })

                # Track similarities for current user
                if current_user and current_user.id in (user1_id, user2_id):
                    other_id = user2_id if current_user.id == user1_id else user1_id
                    current_user_similarities[other_id] = similarity

    current_user_overlap_counts = {}
    if current_user and current_user.id in user_movie_preferences:
        base_movies = user_movie_preferences[current_user.id]
        for other_id, other_movies in user_movie_preferences.items():
            if other_id == current_user.id:
                continue
            common = base_movies & other_movies
            overlap = len(common)
            if overlap == 0:
                continue
            raw_sim = overlap / max(len(base_movies), len(other_movies)) if max(len(base_movies), len(other_movies)) else 0
            alpha = 2
            shrunk_sim = raw_sim * (overlap / (overlap + alpha))
            if shrunk_sim > 0:
                prev = current_user_similarities.get(other_id, 0)
                if shrunk_sim > prev:
                    current_user_similarities[other_id] = shrunk_sim
                    current_user_overlap_counts[other_id] = overlap

    # User -> genre / country affinity edges - OPTIMIZED VERSION
    if show_genres or show_countries:
        for uid, liked_ids in user_movie_preferences.items():
            if uid not in user_nodes or not liked_ids:
                continue
            genre_counter = defaultdict(int)
            country_counter = defaultdict(int)
            for mid in liked_ids:
                if mid not in movie_genres or mid not in movie_countries:
                    continue
                if show_genres:
                    for g in movie_genres[mid]:
                        if g.id in genre_nodes:
                            genre_counter[g.id] += 1
                if show_countries:
                    for c in movie_countries[mid]:
                        if c.id in country_nodes:
                            country_counter[c.id] += 1
            if genre_counter and show_genres:
                max_g = max(genre_counter.values()) or 1
                for gid, cnt in genre_counter.items():
                    strength = cnt / max_g
                    edges.append({
                        'source': user_nodes[uid],
                        'target': genre_nodes[gid],
                        'type': 'user_genre_affinity',
                        'color': {'color': '#6B46C1', 'opacity': 0.35},
                        'width': 1 + strength * 2.5,
                        'dashes': True,
                        'length': int(250 - strength * 120),
                        'label': f"{int(strength*100)}%"
                    })
            if country_counter and show_countries:
                max_c = max(country_counter.values()) or 1
                for cid, cnt in country_counter.items():
                    strength = cnt / max_c
                    edges.append({
                        'source': user_nodes[uid],
                        'target': country_nodes[cid],
                        'type': 'user_country_affinity',
                        'color': {'color': '#2F855A', 'opacity': 0.35},
                        'width': 1 + strength * 2.5,
                        'dashes': True,
                        'length': int(260 - strength * 130),
                        'label': f"{int(strength*100)}%"
                    })

    # Predictions - OPTIMIZED COLLABORATIVE FILTERING
    if show_predictions and current_user and current_user.id in user_nodes:
        logger.info("Generating collaborative filtering predictions")

        # Get unrated movies for current user
        rated_movie_ids = set(rating_matrix.get(current_user.id, {}).keys())
        unrated_movies = [m.id for m in well_reviewed_movies if m.id not in rated_movie_ids and m.id in movie_nodes]

        if unrated_movies and similarity_matrix:
            # Generate predictions using collaborative filtering
            predictions = get_collaborative_filtering_predictions(
                current_user.id,
                rating_matrix,
                similarity_matrix,
                unrated_movies,
                k=10  # Use top 10 similar users
            )

            # Sort predictions by score
            sorted_predictions = sorted(predictions.items(), key=lambda x: x[1], reverse=True)
            if predictions_limit == 0:
                top_predictions = []
            elif predictions_limit is None:
                top_predictions = sorted_predictions
            else:
                top_predictions = sorted_predictions[:predictions_limit]

            # Add prediction edges and update movie nodes
            for movie_id, predicted_score in top_predictions:
                if movie_id in movie_nodes:
                    # Update movie node with prediction data
                    for node in nodes:
                        if node['id'] == movie_nodes[movie_id]:
                            node['predicted_score'] = round(predicted_score, 2)
                            node['predicted_contributors'] = len([u for u in rating_matrix.keys()
                                                                if u != current_user.id and movie_id in rating_matrix.get(u, {})])
                            node['predicted_confidence'] = 'high' if node['predicted_contributors'] >= 5 else 'medium'
                            break

                    # Add prediction edge with enhanced styling
                    length = max(120, 400 - int(predicted_score * 25))
                    confidence_color = '#FFD700'  # Gold for high confidence
                    if node['predicted_confidence'] == 'low':
                        confidence_color = '#FFA500'  # Orange for low confidence
                    elif node['predicted_confidence'] == 'medium':
                        confidence_color = '#32CD32'  # Green for medium confidence

                    edges.append({
                        'source': user_nodes[current_user.id],
                        'target': movie_nodes[movie_id],
                        'type': 'prediction',
                        'color': {'color': '#FF6B6B', 'opacity': 0.6},
                        'width': 2,
                        'label': f"{predicted_score:.1f}",
                        'length': 150
                    })

            logger.info(f"Generated {len(top_predictions)} predictions for user {current_user.id}")

    stats = {
        'total_nodes': len(nodes),
        'total_edges': len(edges),
        'users': len([n for n in nodes if n['type'] == 'user']),
        'movies': len([n for n in nodes if n['type'] == 'movie']),
        'countries': len([n for n in nodes if n['type'] == 'country']),
        'genres': len([n for n in nodes if n['type'] == 'genre']),
        'directors': len([n for n in nodes if n['type'] == 'director']),
        'actors': len([n for n in nodes if n['type'] == 'actor']),
        'crew': len([n for n in nodes if n['type'] == 'crew']),
        'recommendations': len([e for e in edges if e.get('type') == 'prediction']),
    }

    # Add advanced analytics if we have enough nodes
    analytics = {}
    if len(nodes) >= 5:  # Only run analytics on reasonably sized graphs
        try:
            logger.info("Running community detection and centrality analysis")

            # Detect communities
            communities_result = detect_communities(nodes, edges)
            
            # Validate communities to ensure no empty ones exist
            if not validate_communities(communities_result):
                logger.warning("Community validation failed, but continuing with available communities")
            
            analytics['communities'] = communities_result

            # Calculate centrality measures
            centrality_result = calculate_centrality_measures(nodes, edges)
            analytics['centrality'] = centrality_result

            # Add community and centrality data to nodes
            for node in nodes:
                node_id = node['id']

                # Add community information
                if communities_result['communities']:
                    for comm_id, comm_data in communities_result['communities'].items():
                        if node_id in comm_data['nodes']:
                            node['community'] = comm_id
                            node['community_size'] = comm_data['size']
                            node['community_name'] = comm_data.get('name', f'Community {comm_id.split("_")[-1]}')
                            break

                # Add centrality measures
                if node_id in centrality_result['centrality_measures']:
                    node['centrality'] = centrality_result['centrality_measures'][node_id]

            logger.info(f"Analytics completed: {communities_result['stats']['num_communities']} communities detected")

        except Exception as e:
            logger.error(f"Error running analytics: {e}")
            analytics['error'] = str(e)

    # Apply MultiGravity Force Atlas layout (only layout supported)
    enhanced_result = enhance_graph_layout(nodes, edges)
    nodes, edges = enhanced_result['nodes'], enhanced_result['edges']
    
    # Apply smart edge length calculations
    edges = calculate_smart_edge_lengths(nodes, edges)

    result = {'nodes': nodes, 'edges': edges, 'stats': stats}
    
    # Add layout configuration to result
    if 'layout_config' in enhanced_result:
        result['layout_config'] = enhanced_result['layout_config']
    
    if analytics:
        result['analytics'] = analytics

    return result


def calculate_multigravity_forces(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Any]:
    """Calculate MultiGravity Force Atlas layout configuration.
    
    MultiGravity Force Atlas uses different gravitational forces for different node types,
    allowing for better separation and clustering of related nodes while preventing
    center clustering common in traditional Force Atlas algorithms.
    """
    # Calculate node connections for degree-based properties
    node_connections = defaultdict(int)
    node_types = defaultdict(list)
    
    for edge in edges:
        source_id = edge.get('source', edge.get('from'))
        target_id = edge.get('target', edge.get('to'))
        if source_id:
            node_connections[source_id] += 1
        if target_id:
            node_connections[target_id] += 1
    
    # Group nodes by type for type-specific calculations
    for node in nodes:
        node_types[node['type']].append(node)
    
    # Calculate graph metrics for adaptive parameters
    num_nodes = len(nodes)
    num_edges = len(edges)
    density = (2 * num_edges) / (num_nodes * (num_nodes - 1)) if num_nodes > 1 else 0
    
    # MultiGravity configuration - different gravity centers for each node type
    gravity_centers = {
        'user': {'x': -400, 'y': -300, 'strength': 0.8},      # Top-left quadrant
        'movie': {'x': 400, 'y': -300, 'strength': 1.0},      # Top-right quadrant
        'genre': {'x': -400, 'y': 400, 'strength': 0.6},      # Bottom-left quadrant
        'country': {'x': 400, 'y': 400, 'strength': 0.6},     # Bottom-right quadrant
        'director': {'x': 0, 'y': -400, 'strength': 0.7},     # Top center
        'actor': {'x': -200, 'y': 0, 'strength': 0.5},        # Left center
        'crew': {'x': 200, 'y': 0, 'strength': 0.5}           # Right center
    }
    
    # Process nodes with MultiGravity properties
    enhanced_nodes = []
    for node in nodes:
        node_id = node['id']
        node_type = node['type']
        connections = node_connections[node_id]
        
        enhanced_node = node.copy()
        
        # Base mass calculation
        base_mass = max(1.0, node.get('size', 15) / 10)
        
        # Type-specific mass adjustments for MultiGravity
        mass_multipliers = {
            'user': 1.2,      # Users have more mass for stability
            'movie': 1.0,     # Movies are the base mass
            'genre': 0.8,     # Genres are lighter, more mobile
            'country': 0.8,   # Countries are lighter
            'director': 1.1,  # Directors have good mass
            'actor': 0.7,     # Actors are lighter
            'crew': 0.6       # Crew are lightest
        }
        
        mass = base_mass * mass_multipliers.get(node_type, 1.0)
        
        # Repulsion force based on degree and type
        repulsion_base = -50 - (connections * 5)  # More connected = more repulsive
        type_repulsion_multipliers = {
            'user': 1.3,      # Users repel more (prevent clustering)
            'movie': 1.0,     # Movies have base repulsion
            'genre': 1.2,     # Genres need stronger repulsion to stay separated
            'country': 1.2,   # Countries need stronger repulsion to stay separated
            'director': 1.1,  # Directors have good repulsion
            'actor': 0.9,     # Actors can be somewhat close
            'crew': 0.9       # Crew can be somewhat close
        }
        
        repulsion = repulsion_base * type_repulsion_multipliers.get(node_type, 1.0)
        
        # MultiGravity center assignment
        gravity_config = gravity_centers.get(node_type, gravity_centers['movie'])
        
        # Enhanced node properties for MultiGravity Force Atlas
        enhanced_node.update({
            'mass': mass,
            'repulsion': repulsion,
            'gravity_center_x': gravity_config['x'],
            'gravity_center_y': gravity_config['y'],
            'gravity_strength': gravity_config['strength'] * (1.0 - density * 0.5),  # Reduce gravity in dense graphs
            'degree': connections,
            'node_type': node_type,
            
            # Anti-hub measures for highly connected nodes
            'central_gravity': 0.001 if connections > 15 else 0.005,
            'damping_factor': 0.5 + min(0.3, connections * 0.01),  # More damping for hubs
            
            # Initial positioning hints (will be used by frontend)
            'preferred_x': gravity_config['x'] + random.uniform(-50, 50),
            'preferred_y': gravity_config['y'] + random.uniform(-50, 50)
        })
        
        # Special handling for super-hubs (>30 connections)
        if connections > 30:
            enhanced_node.update({
                'repulsion': repulsion * 1.5,  # Extra repulsion
                'gravity_strength': gravity_config['strength'] * 0.3,  # Much less gravity
                'mass': mass * 1.3,  # More mass for stability
                'is_super_hub': True
            })
        
        enhanced_nodes.append(enhanced_node)
    
    # Process edges with type-specific lengths and strengths
    enhanced_edges = []
    for edge in edges:
        enhanced_edge = edge.copy()
        edge_type = edge.get('type', 'default')
        
        # MultiGravity edge length configuration
        edge_lengths = {
            'prediction': 120,           # Prediction edges longer
            'similarity': 100,           # User similarity medium
            'user_genre_affinity': 180,  # Affinity edges longer
            'user_country_affinity': 190,
            'actor_genre_affinity': 160,
            'director_genre_affinity': 160,
            'review': 80,               # Review edges shorter
            'origin': 130,              # Movie-country connections - increased to spread countries
            'genre': 125,               # Movie-genre connections - increased to spread genres
            'directed_by': 95,          # Movie-director connections
            'acted_in': 100,            # Movie-actor connections
            'crew': 105,                # Movie-crew connections
            'default': 90
        }
        
        # Edge strength affects spring constant
        edge_strengths = {
            'prediction': 0.3,      # Weaker springs for predictions
            'similarity': 0.6,      # Medium strength for similarity
            'affinity': 0.4,        # Weaker for affinity edges
            'review': 0.8,          # Strong for review connections
            'default': 0.5
        }
        
        # Determine strength category
        strength_category = 'default'
        if 'affinity' in edge_type:
            strength_category = 'affinity'
        elif edge_type in edge_strengths:
            strength_category = edge_type
        
        enhanced_edge.update({
            'length': edge_lengths.get(edge_type, 90),
            'strength': edge_strengths.get(strength_category, 0.5),
            'edge_type': edge_type
        })
        
        enhanced_edges.append(enhanced_edge)
    
    # MultiGravity Force Atlas configuration
    config = {
        'algorithm': 'multigravity_force_atlas',
        'gravity_centers': gravity_centers,
        'base_repulsion': -80 - (density * 100),  # Stronger repulsion for dense graphs
        'spring_constant': max(0.05, 0.15 - (density * 0.08)),
        'damping': 0.45,
        'theta': 0.4,  # Barnes-Hut approximation parameter
        'gravity_falloff': 2.0,  # How quickly gravity falls off with distance
        'max_velocity': 25,
        'min_velocity': 0.05,
        'timestep': 0.35,
        'stabilization_iterations': min(1500, num_nodes * 5),
        'avoid_overlap': min(0.9, 0.4 + (density * 0.5)),
        
        # MultiGravity specific parameters
        'gravity_balance': 0.7,  # Balance between type gravity and central gravity
        'type_separation_force': 2.0,  # Strong force to separate different node types (increased from 1.2)
        'hub_anti_clustering': 1.5,  # Extra measures against hub clustering
        
        # Adaptive parameters based on graph size and density
        'adaptive_gravity': True,
        'density_compensation': density,
        'size_compensation': min(2.0, num_nodes / 100)
    }
    
    return {
        'nodes': enhanced_nodes,
        'edges': enhanced_edges,
        'layout_config': config
    }


def enhance_graph_layout(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Any]:
    """Apply MultiGravity Force Atlas layout - this is now the only layout algorithm."""
    return calculate_multigravity_forces(nodes, edges)


def calculate_smart_edge_lengths(nodes: List[Dict], edges: List[Dict]) -> List[Dict]:
    """Calculate edge lengths based on node types and graph density."""
    
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
            enhanced_edge['length'] = base_length * 0.7
        else:
            enhanced_edge['length'] = base_length
        
        # High-degree nodes need longer edges to spread out
        source_connections = source_node.get('degree', 1)
        target_connections = target_node.get('degree', 1)
        connection_factor = 1 + (max(source_connections, target_connections) * 0.02)
        
        enhanced_edge['length'] = enhanced_edge.get('length', base_length) * connection_factor
        
        # Prediction and similarity edges should be longer
        if enhanced_edge.get('type') in ['prediction', 'similarity']:
            enhanced_edge['length'] *= 1.5
        
        enhanced_edges.append(enhanced_edge)
    
    return enhanced_edges


def validate_communities(communities_result: Dict[str, Any]) -> bool:
    """Validate that no empty communities exist in the result.
    
    Args:
        communities_result: Result from detect_communities or detect_communities_leiden
        
    Returns:
        True if validation passes, False if empty communities found
    """
    if not communities_result or 'communities' not in communities_result:
        logger.warning("No communities found in result")
        return True  # Not an error per se
    
    empty_communities = []
    for comm_id, comm_data in communities_result['communities'].items():
        if not comm_data.get('nodes') or len(comm_data.get('nodes', [])) == 0:
            empty_communities.append(comm_id)
        elif comm_data.get('size', 0) == 0:
            empty_communities.append(comm_id)
    
    if empty_communities:
        logger.error(f"Found {len(empty_communities)} empty communities: {empty_communities}")
        return False
    
    logger.info(f"Community validation passed: {len(communities_result['communities'])} valid communities")
    return True


__all__ = [
    'build_movie_analytics_graph_context',
    'build_network_graph',
    'detect_communities_leiden',
    'leiden_communities',
    'validate_communities',
    'calculate_multigravity_forces',
    'enhance_graph_layout',
    'calculate_smart_edge_lengths'
]
