"""Community detection algorithms for network graph analysis.

This module implements the Leiden algorithm and related community detection
methods for identifying clusters of densely connected nodes in the graph.
"""

import logging
import random
from collections import defaultdict
from typing import List, Set, Dict, Any, Optional, Tuple

import networkx as nx

from ..constants import (
    LEIDEN_MAX_ITERATIONS,
    LEIDEN_TOLERANCE,
    LEIDEN_DEFAULT_RESOLUTION,
    LEIDEN_RANDOM_STATE,
)
from ..types import NodeDict, EdgeDict, CommunitiesResult

logger = logging.getLogger(__name__)


def calculate_community_quality_metrics(community: Set, G: nx.Graph, all_communities: List[Set]) -> Dict[str, float]:
    """Calculate quality metrics for a community.
    
    Metrics:
    - Conductance: Ratio of edges leaving community to total edges touching community (lower is better)
    - Clustering Coefficient: Average clustering of nodes in community (higher is better)
    - Separability: Ratio of internal edges to external edges (higher is better)
    - Quality Score: Combined metric scaled 0-100 (higher is better)
    
    Args:
        community: Set of node IDs in the community
        G: Full NetworkX graph
        all_communities: List of all communities for context
    
    Returns:
        Dict with conductance, clustering_coefficient, separability, quality_score
    """
    if len(community) <= 1:
        # Single-node communities have perfect internal structure
        return {
            'conductance': 0.0,
            'clustering_coefficient': 1.0,
            'separability': 10.0,  # Perfect isolation (no external edges)
            'quality_score': 100.0
        }
    
    # Create node-to-community mapping
    node_to_comm = {}
    for i, comm in enumerate(all_communities):
        for node in comm:
            node_to_comm[node] = i
    
    current_comm_idx = None
    for i, comm in enumerate(all_communities):
        if community == comm:
            current_comm_idx = i
            break
    
    # Count internal and external edges
    internal_edges = 0
    external_edges = 0
    total_edges_touching = 0
    
    for node in community:
        for neighbor in G.neighbors(node):
            edge_weight = G.get_edge_data(node, neighbor, {}).get('weight', 1.0)
            
            if neighbor in community:
                # Internal edge (count once per edge, not twice)
                if node < neighbor:  # Avoid double-counting
                    internal_edges += edge_weight
            else:
                # External edge
                external_edges += edge_weight
            
            total_edges_touching += edge_weight
    
    # ========== CONDUCTANCE ==========
    # Ratio of edges leaving community to total edges touching it
    # Lower is better (well-separated communities have low conductance)
    if total_edges_touching > 0:
        conductance = external_edges / total_edges_touching
    else:
        conductance = 0.0
    
    # ========== CLUSTERING COEFFICIENT ==========
    # Average clustering coefficient of nodes in community
    subgraph = G.subgraph(community)
    try:
        clustering_values = nx.clustering(subgraph, weight='weight').values()
        clustering_coefficient = sum(clustering_values) / len(clustering_values) if clustering_values else 0.0
    except:
        clustering_coefficient = 0.0
    
    # ========== SEPARABILITY ==========
    # Ratio of internal edges to external edges
    # Higher is better (well-separated communities have high separability)
    if external_edges > 0:
        separability = internal_edges / external_edges
    else:
        # Perfect isolation: no external edges
        # Use a high but finite number instead of infinity
        separability = 10.0 if internal_edges > 0 else 1.0
    
    # ========== QUALITY SCORE ==========
    # Combined metric scaled 0-100
    # Components:
    # - Low conductance is good (invert it)
    # - High clustering is good
    # - High separability is good
    
    # Normalize conductance (0-1) → invert for score (1-0)
    conductance_score = 1.0 - min(conductance, 1.0)
    
    # Clustering is already 0-1
    clustering_score = clustering_coefficient
    
    # Normalize separability (0-10 range, cap at 10)
    separability_score = min(separability / 10.0, 1.0)
    
    # Weighted combination (40% conductance, 30% clustering, 30% separability)
    quality_score = (
        conductance_score * 0.40 +
        clustering_score * 0.30 +
        separability_score * 0.30
    ) * 100.0
    
    return {
        'conductance': round(conductance, 4),
        'clustering_coefficient': round(clustering_coefficient, 4),
        'separability': round(separability, 2),
        'quality_score': round(quality_score, 1)
    }


_THEME_KEYWORDS = {
    'Dark': ['murder', 'death', 'violence', 'crime', 'revenge', 'serial killer', 'dark', 'corruption'],
    'Adventure': ['adventure', 'quest', 'journey', 'exploration', 'treasure', 'expedition'],
    'Heartfelt': ['love', 'romance', 'family', 'friendship', 'loss', 'grief', 'coming of age'],
    'Cerebral': ['philosophy', 'science', 'technology', 'conspiracy', 'mystery', 'puzzle'],
    'Action-Packed': ['fight', 'war', 'battle', 'combat', 'martial arts', 'soldier', 'military'],
}


def _quality_descriptor(ratings: List[float]) -> str:
    if not ratings:
        return ""
    avg = sum(ratings) / len(ratings)
    if avg >= 8.0:
        return "Elite"
    if avg >= 7.5:
        return "Premium"
    if avg >= 7.0:
        return "Quality"
    if avg >= 6.5:
        return "Solid"
    if avg >= 5.5:
        return "Mixed"
    return "Cult"


def _era_descriptors(years: List[int]) -> Tuple[str, str]:
    if not years:
        return "", ""
    avg_year = sum(years) / len(years)
    year_range = max(years) - min(years)
    decade = int(avg_year // 10) * 10
    if decade >= 2020:
        era = "Modern"
    elif decade >= 2010:
        era = "Contemporary"
    elif decade >= 2000:
        era = "2000s"
    elif decade >= 1990:
        era = "90s"
    elif decade >= 1980:
        era = "80s"
    elif decade >= 1970:
        era = "70s"
    elif decade >= 1960:
        era = "Golden Age"
    else:
        era = "Classic"
    decade_label = f"{decade}s" if year_range <= 5 else (era if year_range <= 15 else "")
    return era, decade_label


def _theme_descriptor(keyword_counts: Dict[str, int]) -> str:
    if not keyword_counts:
        return ""
    top_keywords = sorted(keyword_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    for kw, _ in top_keywords:
        for theme, matches in _THEME_KEYWORDS.items():
            if any(match in kw for match in matches):
                return theme
    return ""


def _size_descriptor(total_nodes: int) -> str:
    if total_nodes < 5:
        return "Micro"
    if total_nodes < 15:
        return "Small"
    if total_nodes < 40:
        return "Medium"
    if total_nodes < 100:
        return "Large"
    return "Mega"


def _with_suffix(name: str, suffix: str) -> str:
    """Avoid doubling up e.g. 'X Collection Collection' when the DB name already has the word."""
    if name.rstrip().lower().endswith(suffix.lower()):
        return name
    return f"{name} {suffix}"


def generate_community_name(
    community_node_ids: List[Any],
    all_nodes: List[NodeDict],
    all_edges: List[EdgeDict] = None,
) -> str:
    """Generate a community name straight from its movies' own relationship metadata.

    The old approach only really distinguished "same collection" from "mixed
    nodes" because it depended on generic genre/country/director hub nodes to
    infer structure. This version reads directly off each movie's director(s),
    top-billed cast, studio, keywords, rating and year - the exact signals that
    formed the community's edges in the first place - so a director's run, a
    studio's slate, a recurring cast, or a thematic cluster all get named for
    what they actually are instead of falling back to "N nodes".
    """
    if not community_node_ids:
        logger.warning("generate_community_name called with empty community_node_ids")
        return "🔷 Isolated Cluster"

    node_mapping = {node['id']: node for node in all_nodes}
    movies = [
        node_mapping[node_id] for node_id in community_node_ids
        if node_id in node_mapping and node_mapping[node_id].get('type') == 'movie'
    ]
    total_nodes = len(community_node_ids)

    if not movies:
        return f"🔷 Cluster ({total_nodes} nodes)"

    movie_count = len(movies)

    director_counts: Dict[str, int] = defaultdict(int)
    actor_counts: Dict[str, int] = defaultdict(int)
    studio_counts: Dict[str, int] = defaultdict(int)
    genre_counts: Dict[str, int] = defaultdict(int)
    keyword_counts: Dict[str, int] = defaultdict(int)
    collection_counts: Dict[str, int] = defaultdict(int)
    ratings: List[float] = []
    years: List[int] = []

    for movie in movies:
        for name in movie.get('director_names') or []:
            director_counts[name] += 1
        for name in movie.get('top_cast') or []:
            actor_counts[name] += 1
        studio = movie.get('studio')
        if studio:
            studio_counts[studio] += 1
        for genre in movie.get('genres') or []:
            genre_counts[genre] += 1
        for kw in movie.get('keywords') or []:
            keyword_counts[str(kw).lower()] += 1
        collection_name = movie.get('collection_name')
        if collection_name:
            collection_counts[collection_name] += 1
        rating = movie.get('rating')
        if isinstance(rating, (int, float)) and rating:
            ratings.append(float(rating))
        year = movie.get('year')
        if isinstance(year, int):
            years.append(year)

    quality_descriptor = _quality_descriptor(ratings)
    era_descriptor, decade_descriptor = _era_descriptors(years)
    theme_descriptor = _theme_descriptor(keyword_counts)
    size_descriptor = _size_descriptor(total_nodes)

    def _dominant(counts: Dict[str, int]):
        if not counts:
            return None
        name, count = max(counts.items(), key=lambda kv: kv[1])
        return name, count / movie_count

    # PRIORITY 1: complete/near-complete collection
    collection = _dominant(collection_counts)
    if collection and collection[1] >= 0.99 and movie_count >= 2:
        prefix = f"{quality_descriptor} " if quality_descriptor and len(ratings) >= 3 else ""
        suffix = "Universe" if movie_count < total_nodes * 0.3 else "Collection"
        return f"🎬 {prefix}{_with_suffix(collection[0], suffix)}"

    # PRIORITY 2: single dominant director
    director = _dominant(director_counts)
    if director and director[1] >= 0.8 and movie_count >= 2:
        prefix = f"{quality_descriptor} " if quality_descriptor and len(ratings) >= 3 else ""
        if movie_count >= 5:
            suffix = "Filmography"
        elif movie_count >= 3:
            suffix = "Works"
        else:
            suffix = "Films"
        return f"🎥 {prefix}{director[0]}'s {suffix}"

    # PRIORITY 3: partial collection (50%+)
    if collection and collection[1] >= 0.5:
        prefix = f"{quality_descriptor} " if quality_descriptor and len(ratings) >= 3 else ""
        suffix = "Collection" if collection[1] >= 0.8 else "Series"
        return f"🎬 {prefix}{_with_suffix(collection[0], suffix)}"

    # PRIORITY 4: dominant studio slate
    studio = _dominant(studio_counts)
    if studio and studio[1] >= 0.6 and movie_count >= 3:
        prefix_parts = [p for p in (quality_descriptor, decade_descriptor) if p]
        prefix = ' '.join(prefix_parts) + ' ' if prefix_parts else ''
        return f"🏢 {prefix}{studio[0]} Slate ({movie_count} films)"

    # PRIORITY 5: recurring lead actor / ensemble
    actor = _dominant(actor_counts)
    if actor and actor[1] >= 0.5 and movie_count >= 3:
        prefix = f"{era_descriptor} " if era_descriptor and len(years) >= 3 else ""
        return f"⭐ {prefix}{actor[0]}'s Roles ({movie_count} films)"

    # PRIORITY 6: thematic cluster - shared keyword/genre + quality/era
    if theme_descriptor or genre_counts:
        name_parts = [p for p in (quality_descriptor, theme_descriptor, decade_descriptor) if p]
        top_genre = max(genre_counts.items(), key=lambda kv: kv[1])[0] if genre_counts else None
        label = top_genre or "Cinema"
        if name_parts:
            return f"🎬 {' '.join(name_parts)} {label} ({total_nodes} nodes)"
        return f"🎬 {label} Cluster ({total_nodes} nodes)"

    # Fallback
    return f"🌟 {size_descriptor} Film Cluster ({total_nodes} nodes)"


def leiden_communities(
    G: nx.Graph, 
    resolution: float = LEIDEN_DEFAULT_RESOLUTION, 
    random_state: Optional[int] = LEIDEN_RANDOM_STATE,
    max_iterations: int = LEIDEN_MAX_ITERATIONS, 
    tolerance: float = LEIDEN_TOLERANCE
) -> List[Set]:
    """Leiden Algorithm for Community Detection.
    
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
        """Calculate how much modularity improves if we move a node between communities."""
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
        edge_gain = (edges_to_new_community - edges_to_old_community) / total_edge_weight
        degree_penalty = resolution * node_degree * (
            new_community_degree - old_community_degree + node_degree
        ) / (total_edge_weight ** 2)
        
        return edge_gain - degree_penalty
    
    def phase1_move_nodes():
        """Phase 1: Local Moving - Try moving each node to neighboring communities."""
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
        """Phase 2: Refinement - Ensure all communities are well-connected."""
        refined = []
        
        for community in communities_list:
            # Keep single-node communities - they're isolated nodes
            if len(community) <= 1:
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
                    # Keep all parts, including single nodes from the split
                    if connected_part:
                        refined.append(connected_part)
        
        return refined
    
    # ========================================
    # MAIN ALGORITHM
    # ========================================
    
    previous_modularity = -1.0
    communities = [{node} for node in G.nodes()]
    no_improvement_count = 0
    
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
        
        # Check for convergence
        modularity_improvement = current_modularity - previous_modularity
        
        if abs(modularity_improvement) < tolerance:
            no_improvement_count += 1
            if no_improvement_count >= 3:
                logger.info(f"Leiden converged after {iteration + 1} iterations")
                break
        else:
            no_improvement_count = 0
        
        if modularity_improvement < -tolerance:
            logger.info(f"Leiden stopped after {iteration + 1} iterations (modularity decreased)")
            break
        
        previous_modularity = current_modularity
    
    # Finalize
    final_communities = []
    for community in communities:
        if community:
            final_communities.append(set(community) if not isinstance(community, set) else community)
    
    logger.info(f"Leiden completed: {len(final_communities)} communities with modularity {previous_modularity:.4f}")
    
    return final_communities


def detect_communities_leiden(
    nodes: List[NodeDict], 
    edges: List[EdgeDict],
    resolution: float = LEIDEN_DEFAULT_RESOLUTION, 
    random_state: Optional[int] = LEIDEN_RANDOM_STATE
) -> CommunitiesResult:
    """Detect communities using the Leiden algorithm.
    
    Args:
        nodes: List of node dictionaries with 'id' and other properties
        edges: List of edge dictionaries with 'from', 'to', and other properties
        resolution: Resolution parameter for modularity optimization
        random_state: Random seed for reproducibility
    
    Returns:
        CommunitiesResult with communities, stats, method, and modularity
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
        
        # ========== COLLECTION-FIRST APPROACH ==========
        # First, identify all movie collections and create initial communities for them
        # Collections are NEVER split - they form the core of communities
        collection_to_movies = defaultdict(list)
        movie_to_collection = {}
        
        for node in nodes:
            if node.get('type') == 'movie':
                collection_id = node.get('collection_id')
                if collection_id is not None:
                    collection_to_movies[collection_id].append(node['id'])
                    movie_to_collection[node['id']] = collection_id
        
        # Create initial collection-based communities
        initial_communities = []
        collection_community_map = {}  # collection_id -> community_index
        
        for collection_id, movie_ids in collection_to_movies.items():
            if len(movie_ids) >= 2:  # Only collections with 2+ movies
                community_index = len(initial_communities)
                initial_communities.append(set(movie_ids))
                collection_community_map[collection_id] = community_index
                logger.info(f"Created collection community with {len(movie_ids)} movies from collection {collection_id}")
        
        # Now run community detection only on nodes NOT in collections
        # This ensures collection movies stay together
        nodes_in_collections = set()
        for movie_ids in collection_to_movies.values():
            if len(movie_ids) >= 2:
                nodes_in_collections.update(movie_ids)
        
        # Create subgraph excluding collection movies
        non_collection_nodes = [n for n in G.nodes() if n not in nodes_in_collections]
        
        if len(non_collection_nodes) > 1:
            logger.info(f"Running community detection on {len(non_collection_nodes)} non-collection nodes")
            G_non_collection = G.subgraph(non_collection_nodes).copy()
            
            # Run Leiden/Louvain on non-collection nodes
            try:
                non_collection_communities = leiden_communities(G_non_collection, resolution=resolution, random_state=random_state)
            except Exception as e:
                logger.error(f"Error in Leiden: {e}, falling back to Louvain")
                from networkx.algorithms.community import louvain_communities
                non_collection_communities = louvain_communities(G_non_collection, weight='weight', resolution=resolution, seed=random_state)
            
            # Add non-collection communities to initial communities
            for community in non_collection_communities:
                if len(community) >= 2:
                    initial_communities.append(set(community))
        
        # Track which nodes have been assigned to avoid duplicates
        assigned_nodes = set()
        for community in initial_communities:
            assigned_nodes.update(community)
        
        # Now expand collection communities to include connected non-movie nodes
        # (directors, actors, genres, countries that are connected to collection movies)
        for collection_id, movie_ids in collection_to_movies.items():
            if len(movie_ids) >= 2 and collection_id in collection_community_map:
                community_index = collection_community_map[collection_id]
                collection_community = initial_communities[community_index]
                
                # Add all nodes connected to these movies (only if not already assigned)
                for movie_id in movie_ids:
                    if movie_id in G:
                        for neighbor in G.neighbors(movie_id):
                            # Only add if:
                            # 1. Not a movie (movies stay in their original communities)
                            # 2. Not already assigned to another community
                            if neighbor not in assigned_nodes:
                                neighbor_type = next((n.get('type') for n in nodes if n['id'] == neighbor), None)
                                if neighbor_type != 'movie':
                                    collection_community.add(neighbor)
                                    assigned_nodes.add(neighbor)
        
        # Assign remaining nodes to their closest community based on connections
        all_assigned_nodes = assigned_nodes.copy()  # Use the tracked assigned nodes
        
        unassigned_nodes = set(G.nodes()) - all_assigned_nodes
        
        for node in unassigned_nodes:
            # Find community with strongest connection
            best_community = None
            best_weight = 0
            
            for i, community in enumerate(initial_communities):
                total_weight = 0
                for neighbor in G.neighbors(node):
                    if neighbor in community:
                        total_weight += G[node][neighbor].get('weight', 1.0)
                
                if total_weight > best_weight:
                    best_weight = total_weight
                    best_community = i
            
            if best_community is not None:
                initial_communities[best_community].add(node)
            else:
                # Create singleton community
                initial_communities.append({node})
        
        # Keep ALL communities (including single-node ones) for valid partition
        # We'll filter for display purposes later
        communities = [c for c in initial_communities if c]  # Only filter empty sets
        
        logger.info(f"Final: {len(communities)} communities ({len([c for c in collection_to_movies.values() if len(c) >= 2])} collection-based)")
        
        # Validate partition: ensure all nodes are included exactly once
        all_community_nodes = []
        for community in communities:
            all_community_nodes.extend(list(community))
        
        all_graph_nodes = set(G.nodes())
        
        # Check for duplicate nodes across communities
        seen_nodes = set()
        duplicate_nodes = set()
        for node in all_community_nodes:
            if node in seen_nodes:
                duplicate_nodes.add(node)
            seen_nodes.add(node)
        
        if duplicate_nodes:
            logger.error(f"Partition validation: Found {len(duplicate_nodes)} duplicate nodes across communities: {list(duplicate_nodes)[:10]}")
            # Fix duplicates: keep node in first community it appears in, remove from others
            fixed_communities = []
            assigned = set()
            for community in communities:
                fixed_community = set()
                for node in community:
                    if node not in assigned:
                        fixed_community.add(node)
                        assigned.add(node)
                if fixed_community:  # Only add non-empty communities
                    fixed_communities.append(fixed_community)
            communities = fixed_communities
            logger.info(f"Fixed duplicates. Now have {len(communities)} communities with {len(assigned)} unique nodes.")
        
        # Check for missing nodes
        all_community_nodes_set = set()
        for community in communities:
            all_community_nodes_set.update(community)
        
        missing_nodes = all_graph_nodes - all_community_nodes_set
        
        if missing_nodes:
            logger.warning(f"Partition validation: {len(missing_nodes)} nodes missing from communities. Adding them as singletons.")
            for node in missing_nodes:
                communities.append({node})
        
        # Final validation
        final_node_count = sum(len(c) for c in communities)
        if final_node_count != len(all_graph_nodes):
            logger.error(f"Partition validation FAILED: Graph has {len(all_graph_nodes)} nodes, communities have {final_node_count} total nodes.")
        else:
            logger.info(f"Partition validation PASSED: All {len(all_graph_nodes)} nodes are in exactly one community.")
        
        # Calculate modularity (requires complete partition - all nodes must be included)
        try:
            overall_modularity = nx.algorithms.community.modularity(G, communities, weight='weight', resolution=resolution)
        except Exception as e:
            logger.error(f"Modularity calculation failed: {e}")
            overall_modularity = 0.0
        
        # Handle empty or trivial graphs
        if len(communities) == 0:
            if len(G) == 0:
                return CommunitiesResult(
                    communities={},
                    stats={'num_communities': 0, 'modularity': 0.0},
                    method='leiden-collection-first',
                    modularity=0.0
                )
            elif len(G) == 1:
                node_id = list(G.nodes())[0]
                return CommunitiesResult(
                    communities={
                        'community_0': {
                            'nodes': [node_id],
                            'size': 1,
                            'name': 'Single Node',
                            'modularity': 0.0,
                            'internal_edges': 0,
                            'total_degree': 0.0,
                            'density': 1.0
                        }
                    },
                    stats={'num_communities': 1, 'modularity': 0.0},
                    method='leiden-collection-first',
                    modularity=0.0
                )
        
        # Convert to dictionary format
        # Only include communities with 2+ nodes for display purposes
        # (single-node communities exist for partition validity but aren't meaningful clusters)
        community_dict = {}
        display_index = 0  # Separate index for displayed communities
        
        for i, community in enumerate(communities):
            # Skip empty or single-member communities for display
            if not community or len(community) < 2:
                continue
                
            community_id = f"community_{display_index}"
            display_index += 1
            community_nodes = list(community)
            
            # Generate meaningful name
            community_name = generate_community_name(community_nodes, nodes, edges)
            
            # Calculate community metrics
            subgraph = G.subgraph(community)
            internal_edges = subgraph.number_of_edges()
            total_degree = sum(dict(G.degree(weight='weight')).get(node, 0) for node in community)
            
            # Calculate quality metrics
            quality_metrics = calculate_community_quality_metrics(community, G, communities)
            
            community_dict[community_id] = {
                'nodes': community_nodes,
                'size': len(community),
                'name': community_name,
                'modularity': overall_modularity,
                'internal_edges': internal_edges,
                'total_degree': total_degree,
                'density': (2 * internal_edges) / (len(community) * (len(community) - 1)) if len(community) > 1 else 1.0,
                # Quality metrics
                'conductance': quality_metrics['conductance'],
                'clustering_coefficient': quality_metrics['clustering_coefficient'],
                'separability': quality_metrics['separability'],
                'quality_score': quality_metrics['quality_score']
            }
        
        # Calculate statistics
        community_sizes = [len(c) for c in communities]
        avg_size = sum(community_sizes) / len(communities) if communities else 0
        stats = {
            'num_communities': len(communities),
            'modularity': overall_modularity,
            'avg_community_size': avg_size,
            'largest_community': max(community_sizes, default=0),
            'smallest_community': min(community_sizes, default=0),
            'community_size_std': (sum((size - avg_size) ** 2 for size in community_sizes) / len(communities)) ** 0.5 if len(communities) > 1 else 0,
            'coverage': sum(len(c) for c in communities) / len(G) if len(G) > 0 else 0,
            'resolution_used': resolution
        }
        
        logger.info(f"Leiden found {len(communities)} communities with modularity {overall_modularity:.4f}")
        
        return CommunitiesResult(
            communities=community_dict,
            stats=stats,
            method='leiden',
            modularity=overall_modularity
        )
    
    except Exception as e:
        logger.error(f"Error in Leiden community detection: {e}", exc_info=True)
        return CommunitiesResult(
            communities={},
            stats={'error': str(e), 'modularity': 0.0},
            method='leiden_failed',
            modularity=0.0
        )


def detect_communities(nodes: List[NodeDict], edges: List[EdgeDict]) -> CommunitiesResult:
    """Detect communities using Leiden algorithm with fallbacks.
    
    Args:
        nodes: List of node dictionaries
        edges: List of edge dictionaries
    
    Returns:
        CommunitiesResult with communities, stats, and method used
    """
    # Try Leiden first
    try:
        logger.info("Attempting community detection with Leiden algorithm")
        result = detect_communities_leiden(nodes, edges, resolution=LEIDEN_DEFAULT_RESOLUTION, random_state=LEIDEN_RANDOM_STATE)
        if result['stats'].get('num_communities', 0) > 0:
            logger.info(f"Leiden successful: {result['stats']['num_communities']} communities")
            return result
    except Exception as e:
        logger.warning(f"Leiden failed: {e}, trying fallback methods")
    
    # Fallback to other methods
    try:
        G = nx.Graph()
        for node in nodes:
            G.add_node(node['id'], **{k: v for k, v in node.items() if k != 'id'})
        for edge in edges:
            from_id = edge.get('source', edge.get('from'))
            to_id = edge.get('target', edge.get('to'))
            weight = edge.get('weight', 1.0)
            G.add_edge(from_id, to_id, weight=weight)

        communities = None
        method_used = 'unknown'
        
        try:
            from networkx.algorithms.community import louvain_communities
            communities = louvain_communities(G, weight='weight', resolution=1.0)
            method_used = 'louvain'
            logger.info(f"Using Louvain: {len(communities)} communities")
        except ImportError:
            logger.warning("Louvain not available, using greedy modularity")
            from networkx.algorithms.community import greedy_modularity_communities
            communities = greedy_modularity_communities(G, weight='weight')
            method_used = 'greedy_modularity'

        overall_modularity = nx.algorithms.community.modularity(G, communities, weight='weight') if communities else 0.0

        community_dict = {}
        for i, community in enumerate(communities):
            community_id = f"community_{i}"
            community_nodes = list(community)
            community_name = generate_community_name(community_nodes, nodes, edges)
            
            community_dict[community_id] = {
                'nodes': community_nodes,
                'size': len(community),
                'name': community_name,
                'modularity': overall_modularity
            }

        community_sizes = [len(c) for c in communities] if communities else []
        stats = {
            'num_communities': len(communities) if communities else 0,
            'modularity': overall_modularity,
            'avg_community_size': sum(community_sizes) / len(community_sizes) if community_sizes else 0,
            'largest_community': max(community_sizes, default=0),
            'smallest_community': min(community_sizes, default=0)
        }

        return CommunitiesResult(
            communities=community_dict,
            stats=stats,
            method=method_used
        )

    except Exception as e:
        logger.error(f"Error in fallback community detection: {e}")
        return CommunitiesResult(
            communities={},
            stats={'error': str(e), 'modularity': 0.0},
            method='failed'
        )


def validate_communities(communities_result: CommunitiesResult) -> bool:
    """Validate that no empty communities exist.
    
    Args:
        communities_result: Result from community detection
        
    Returns:
        True if valid, False if empty communities found
    """
    if not communities_result or 'communities' not in communities_result:
        logger.warning("No communities in result")
        return True
    
    empty_communities = []
    for comm_id, comm_data in communities_result['communities'].items():
        if not comm_data.get('nodes') or len(comm_data.get('nodes', [])) == 0:
            empty_communities.append(comm_id)
        elif comm_data.get('size', 0) == 0:
            empty_communities.append(comm_id)
    
    if empty_communities:
        logger.error(f"Found {len(empty_communities)} empty communities: {empty_communities}")
        return False
    
    logger.info(f"Validation passed: {len(communities_result['communities'])} valid communities")
    return True


def apply_community_edge_properties(edges: List[EdgeDict], nodes: List[NodeDict]) -> List[EdgeDict]:
    """Apply community-aware physics properties to edges.
    
    Edges within the same community get:
    - Shorter spring length (nodes stick closer)
    - Stronger spring constant (tighter cohesion)
    
    Edges between communities get:
    - Longer spring length (communities spread apart)
    - Weaker spring constant (looser connection)
    
    Args:
        edges: List of edge dictionaries
        nodes: List of node dictionaries (to get community assignments)
    
    Returns:
        List of edges with enhanced spring properties
    """
    # Build node_id -> community_id mapping
    node_to_community = {}
    for node in nodes:
        if 'id' in node and 'community' in node:
            node_to_community[node['id']] = node['community']
    
    if not node_to_community:
        logger.debug("No community assignments found, skipping edge property enhancement")
        return edges
    
    enhanced_edges = []
    intra_community_count = 0
    inter_community_count = 0
    
    for edge in edges:
        enhanced_edge = edge.copy()
        
        source_id = edge.get('source', edge.get('from'))
        target_id = edge.get('target', edge.get('to'))
        
        source_comm = node_to_community.get(source_id)
        target_comm = node_to_community.get(target_id)
        
        # Only apply if both nodes have community assignments
        if source_comm is not None and target_comm is not None:
            if source_comm == target_comm:
                # ========== INTRA-COMMUNITY EDGE ==========
                # Shorter length and stronger spring for tight clusters
                base_length = enhanced_edge.get('length', 90)
                enhanced_edge['length'] = base_length * 0.4  # 60% shorter (36 default)
                enhanced_edge['strength'] = enhanced_edge.get('strength', 0.08) * 2.5  # 2.5x stronger
                intra_community_count += 1
            else:
                # ========== INTER-COMMUNITY EDGE ==========
                # Longer length and weaker spring to spread communities apart
                base_length = enhanced_edge.get('length', 90)
                enhanced_edge['length'] = base_length * 2.5  # 150% longer (225 default)
                enhanced_edge['strength'] = enhanced_edge.get('strength', 0.08) * 0.3  # 70% weaker
                inter_community_count += 1
        
        enhanced_edges.append(enhanced_edge)
    
    logger.info(
        f"Applied community edge properties: "
        f"{intra_community_count} intra-community (shorter/stronger), "
        f"{inter_community_count} inter-community (longer/weaker)"
    )
    
    return enhanced_edges
