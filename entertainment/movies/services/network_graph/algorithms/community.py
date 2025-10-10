"""Community detection algorithms for network graph analysis.

This module implements the Leiden algorithm and related community detection
methods for identifying clusters of densely connected nodes in the graph.
"""

import logging
import random
from collections import defaultdict
from typing import List, Set, Dict, Any, Optional

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


def generate_community_name(community_node_ids: List[int], all_nodes: List[NodeDict]) -> str:
    """Generate a meaningful, semantic name for a community based on rich metadata.
    
    Uses keywords, ratings, release dates, genres, countries, and people to create
    descriptive names that capture the essence of the community.
    
    Args:
        community_node_ids: List of node IDs in the community
        all_nodes: List of all node dictionaries
    
    Returns:
        A descriptive name for the community
    """
    # Safety check for empty input
    if not community_node_ids:
        logger.warning("generate_community_name called with empty community_node_ids")
        return "🔷 Isolated Cluster"
    
    # Create a mapping of node_id to node data
    node_mapping = {node['id']: node for node in all_nodes}
    
    # Get the actual node data for this community
    community_nodes = [node_mapping[node_id] for node_id in community_node_ids if node_id in node_mapping]
    
    if not community_nodes:
        logger.warning(f"No valid nodes found for community IDs: {community_node_ids[:5]}{'...' if len(community_node_ids) > 5 else ''}")
        return "🔷 Isolated Cluster"
    
    # Count node types and collect metadata
    type_counts: Dict[str, int] = {}
    genre_counts: Dict[str, int] = {}
    country_counts: Dict[str, int] = {}
    keyword_counts: Dict[str, int] = {}
    collection_ids: Dict[int, List[str]] = {}  # collection_id -> movie titles
    movie_connectivity: Dict[str, int] = {}  # movie_id -> number of connected people
    director_names: List[str] = []
    actor_names: List[str] = []
    movie_titles: List[str] = []
    movie_id_to_title: Dict[str, str] = {}  # Track movie IDs to titles
    
    # Collect movie-specific semantic data
    movie_ratings: List[float] = []
    movie_years: List[int] = []
    
    for node in community_nodes:
        node_type = node.get('type', 'unknown')
        type_counts[node_type] = type_counts.get(node_type, 0) + 1
        
        # Extract detailed info based on node type
        if node_type == 'genre':
            genre_name = node.get('label', 'Unknown Genre')
            genre_counts[genre_name] = genre_counts.get(genre_name, 0) + 1
        elif node_type == 'country':
            country_name = node.get('label', 'Unknown Country')
            country_counts[country_name] = country_counts.get(country_name, 0) + 1
        elif node_type == 'director' and len(director_names) < 3:
            director_names.append(node.get('label', 'Unknown'))
        elif node_type == 'actor' and len(actor_names) < 3:
            actor_names.append(node.get('label', 'Unknown'))
        elif node_type == 'movie':
            movie_id = node.get('id', '')
            movie_title = node.get('label', 'Unknown')
            
            # Track movie ID to title mapping
            if movie_id:
                movie_id_to_title[movie_id] = movie_title
                movie_connectivity[movie_id] = 0  # Initialize connectivity counter
            
            if len(movie_titles) < 3:
                movie_titles.append(movie_title)
            
            # Track movie collections
            collection_id = node.get('collection_id')
            if collection_id is not None:
                if collection_id not in collection_ids:
                    collection_ids[collection_id] = []
                collection_ids[collection_id].append(movie_title)
            
            # Collect semantic metadata from movies
            rating = node.get('rating') or node.get('avg_rating')
            if rating and isinstance(rating, (int, float)):
                movie_ratings.append(float(rating))
            
            year = node.get('year')
            if year and isinstance(year, (int, str)):
                try:
                    movie_years.append(int(year))
                except (ValueError, TypeError):
                    pass
            
            # Extract keywords (if available in node metadata)
            keywords = node.get('keywords', [])
            if isinstance(keywords, list):
                for keyword in keywords[:10]:  # Limit to top 10 keywords
                    if isinstance(keyword, dict):
                        kw_name = keyword.get('name', '').strip()
                    else:
                        kw_name = str(keyword).strip()
                    
                    if kw_name:
                        keyword_counts[kw_name] = keyword_counts.get(kw_name, 0) + 1
                    if kw_name:
                        keyword_counts[kw_name] = keyword_counts.get(kw_name, 0) + 1
    
    # ========== ANALYZE MOVIE CONNECTIVITY ==========
    # For actor/crew-heavy communities, find movies with most people
    # This helps identify "hub" movies that connect the community
    actor_count = type_counts.get('actor', 0)
    director_count = type_counts.get('director', 0)
    crew_count = type_counts.get('crew', 0)
    movie_count = type_counts.get('movie', 0)
    people_count = actor_count + director_count + crew_count
    
    most_connected_movie = None
    most_connected_movie_title = None
    
    # If lots of people but few movies, the movies are "hubs"
    if people_count > 10 and movie_count > 0 and movie_count <= 5:
        # Estimate connectivity: people / movies ratio
        # The movie(s) are likely the central connection point
        if movie_titles:
            most_connected_movie_title = movie_titles[0]  # Use first movie as representative
    
    # ========== SEMANTIC ANALYSIS ==========
    # Analyze temporal patterns
    decade_descriptor = ""
    era_descriptor = ""
    if movie_years:
        avg_year = sum(movie_years) / len(movie_years)
        min_year = min(movie_years)
        max_year = max(movie_years)
        year_range = max_year - min_year
        
        # Determine decade/era
        decade = int(avg_year // 10) * 10
        if decade >= 2020:
            era_descriptor = "Modern"
        elif decade >= 2010:
            era_descriptor = "Contemporary"
        elif decade >= 2000:
            era_descriptor = "2000s"
        elif decade >= 1990:
            era_descriptor = "90s"
        elif decade >= 1980:
            era_descriptor = "80s"
        elif decade >= 1970:
            era_descriptor = "70s"
        elif decade >= 1960:
            era_descriptor = "Golden Age"
        else:
            era_descriptor = "Classic"
        
        # If tight clustering in time
        if year_range <= 5:
            decade_descriptor = f"{decade}s"
        elif year_range <= 15:
            decade_descriptor = era_descriptor
    
    # Analyze quality/rating patterns
    quality_descriptor = ""
    if movie_ratings:
        avg_rating = sum(movie_ratings) / len(movie_ratings)
        if avg_rating >= 8.0:
            quality_descriptor = "Elite"
        elif avg_rating >= 7.5:
            quality_descriptor = "Premium"
        elif avg_rating >= 7.0:
            quality_descriptor = "Quality"
        elif avg_rating >= 6.5:
            quality_descriptor = "Solid"
        elif avg_rating >= 5.5:
            quality_descriptor = "Mixed"
        else:
            quality_descriptor = "Cult"
    
    # Find dominant keywords (themes)
    top_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:3]
    theme_descriptor = ""
    
    # Map keywords to thematic categories
    if top_keywords:
        dominant_keyword = top_keywords[0][0].lower()
        
        # Thematic mappings
        dark_themes = ['murder', 'death', 'violence', 'crime', 'revenge', 'serial killer', 'dark', 'corruption']
        adventure_themes = ['adventure', 'quest', 'journey', 'exploration', 'treasure', 'expedition']
        emotional_themes = ['love', 'romance', 'family', 'friendship', 'loss', 'grief', 'coming of age']
        intellectual_themes = ['philosophy', 'science', 'technology', 'conspiracy', 'mystery', 'puzzle']
        action_themes = ['fight', 'war', 'battle', 'combat', 'martial arts', 'soldier', 'military']
        
        for kw, count in top_keywords:
            kw_lower = kw.lower()
            if any(theme in kw_lower for theme in dark_themes):
                theme_descriptor = "Dark"
                break
            elif any(theme in kw_lower for theme in adventure_themes):
                theme_descriptor = "Adventure"
                break
            elif any(theme in kw_lower for theme in emotional_themes):
                theme_descriptor = "Heartfelt"
                break
            elif any(theme in kw_lower for theme in intellectual_themes):
                theme_descriptor = "Cerebral"
                break
            elif any(theme in kw_lower for theme in action_themes):
                theme_descriptor = "Action-Packed"
                break
    
    # Determine the total nodes and dominant characteristics (needed for collection check)
    total_nodes = len(community_nodes)
    dominant_type = max(type_counts.items(), key=lambda x: x[1])
    dominant_percentage = (dominant_type[1] / total_nodes) * 100
    
    # Check if community is dominated by a movie collection
    dominant_collection = None
    dominant_collection_name = None
    collection_movie_percentage = 0.0
    
    if collection_ids:
        # Find the collection with the most movies in this community
        largest_collection = max(collection_ids.items(), key=lambda x: len(x[1]))
        collection_id, collection_movies = largest_collection
        
        # Calculate what percentage of ALL movies in the community belong to this collection
        movie_count_in_community = type_counts.get('movie', 0)
        if movie_count_in_community > 0:
            collection_movie_percentage = len(collection_movies) / movie_count_in_community
        
        # Collection is dominant if:
        # - Has 2+ movies AND represents 50%+ of all movies in the community
        # - OR has 3+ movies (always significant)
        if (len(collection_movies) >= 2 and collection_movie_percentage >= 0.5) or len(collection_movies) >= 3:
            dominant_collection = collection_id
            # Fetch collection name from database
            try:
                from movies.models import Collection
                collection_obj = Collection.objects.filter(id=collection_id).first()
                if collection_obj:
                    dominant_collection_name = collection_obj.name
            except Exception as e:
                logger.warning(f"Failed to fetch collection name for ID {collection_id}: {e}")
    
    # Size categories for more descriptive names
    size_descriptor = ""
    if total_nodes < 5:
        size_descriptor = "Micro"
    elif total_nodes < 15:
        size_descriptor = "Small"
    elif total_nodes < 40:
        size_descriptor = "Medium"
    elif total_nodes < 100:
        size_descriptor = "Large"
    else:
        size_descriptor = "Mega"
    
    # Generate name based on community composition
    # PRIORITY 1: If dominated by a movie collection, use collection name
    if dominant_collection_name:
        # Use collection name as the primary identifier
        name_parts = []
        if quality_descriptor and len(movie_ratings) >= 3:
            name_parts.append(quality_descriptor)
        name_parts.append(dominant_collection_name)
        
        movie_count = type_counts.get('movie', 0)
        
        # Determine suffix based on composition
        # If collection represents 80%+ of all movies, it's the core of the community
        if collection_movie_percentage >= 0.8:
            # Pure collection community
            return f"🎬 {' '.join(name_parts)} Collection"
        elif movie_count < total_nodes * 0.3:
            # Movies are minority but from same collection - it's the universe (cast/crew dominant)
            return f"🎬 {' '.join(name_parts)} Universe"
        else:
            # Collection is significant but mixed with other movies
            return f"🎬 {' '.join(name_parts)} Series"
    
    # PRIORITY 2: Very homogeneous community (80%+)
    if dominant_percentage >= 80:
        if dominant_type[0] == 'genre' and genre_counts:
            top_genre = max(genre_counts.items(), key=lambda x: x[1])[0]
            
            # Build semantic name with quality, era, and theme
            name_parts = []
            if quality_descriptor and len(movie_ratings) >= 3:
                name_parts.append(quality_descriptor)
            if theme_descriptor:
                name_parts.append(theme_descriptor)
            if decade_descriptor and len(movie_years) >= 3:
                name_parts.append(decade_descriptor)
            
            if name_parts:
                return f"🎬 {' '.join(name_parts)} {top_genre} ({total_nodes} nodes)"
            return f"🎬 {top_genre} Universe ({total_nodes} nodes)"
            
        elif dominant_type[0] == 'country' and country_counts:
            top_country = max(country_counts.items(), key=lambda x: x[1])[0]
            
            # Add era and quality for country-based communities
            name_parts = []
            if decade_descriptor and len(movie_years) >= 3:
                name_parts.append(decade_descriptor)
            if quality_descriptor and len(movie_ratings) >= 3:
                name_parts.append(quality_descriptor)
            
            if name_parts:
                return f"🌍 {' '.join(name_parts)} {top_country} Cinema ({total_nodes} nodes)"
            return f"🌍 {top_country} Cinema ({total_nodes} nodes)"
            
        elif dominant_type[0] == 'director':
            if director_names:
                lead_director = director_names[0]
                if quality_descriptor and len(movie_ratings) >= 2:
                    return f"🎥 {lead_director}'s {quality_descriptor} Works ({total_nodes} nodes)"
                return f"🎥 {lead_director}'s Circle ({total_nodes} collaborators)"
            return f"🎥 {size_descriptor} Director Network ({total_nodes} directors)"
            
        elif dominant_type[0] == 'actor':
            # For actor-dominated communities, check if there's a central movie
            # that connects most of the actors (hub movie pattern)
            if most_connected_movie_title and people_count > 10 and movie_count <= 3:
                # This is likely a movie + its cast/crew
                movie_name = most_connected_movie_title
                # Truncate long movie names
                if len(movie_name) > 30:
                    movie_name = movie_name[:27] + "..."
                return f"🎬 {movie_name} Production ({total_nodes} nodes)"
            
            # For actor-dominated communities, try to use genre/theme if available
            # to avoid generic "Actor-Centric" names
            if genre_counts and len(movie_titles) >= 2:
                top_genre = max(genre_counts.items(), key=lambda x: x[1])[0]
                name_parts = []
                if decade_descriptor:
                    name_parts.append(decade_descriptor)
                if quality_descriptor:
                    name_parts.append(quality_descriptor)
                name_parts.append(top_genre)
                return f"🎬 {' '.join(name_parts)} Ensemble ({total_nodes} nodes)"
            elif actor_names:
                lead_actor = actor_names[0]
                if decade_descriptor and len(movie_years) >= 2:
                    return f"⭐ {lead_actor}'s {decade_descriptor} Era ({total_nodes} nodes)"
                return f"⭐ {lead_actor}'s Circle ({total_nodes} connections)"
            elif movie_titles:
                # Use movie-based naming if we have movies
                name_parts = []
                if decade_descriptor:
                    name_parts.append(decade_descriptor)
                if theme_descriptor:
                    name_parts.append(theme_descriptor)
                if name_parts:
                    return f"🎬 {' '.join(name_parts)} Cast Network ({total_nodes} nodes)"
            return f"⭐ {size_descriptor} Actor Ensemble ({total_nodes} actors)"
            
        elif dominant_type[0] == 'movie':
            # Movie-heavy communities with semantic enrichment
            name_parts = []
            if quality_descriptor and len(movie_ratings) >= 5:
                name_parts.append(quality_descriptor)
            if theme_descriptor and top_keywords:
                name_parts.append(theme_descriptor)
            if decade_descriptor and len(movie_years) >= 5:
                name_parts.append(decade_descriptor)
            
            if name_parts:
                return f"🎞️ {' '.join(name_parts)} Collection ({total_nodes} movies)"
            return f"🎞️ {size_descriptor} Film Collection ({total_nodes} movies)"
            
        elif dominant_type[0] == 'user':
            return f"👥 {size_descriptor} User Group ({total_nodes} members)"
        else:
            return f"🔹 {dominant_type[0].title()} Cluster ({total_nodes} nodes)"
    
    elif dominant_percentage >= 50:  # Moderately homogeneous (50-80%)
        if dominant_type[0] == 'genre' and genre_counts:
            top_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:2]
            
            # Add semantic richness to fusion names
            prefix_parts = []
            if quality_descriptor and len(movie_ratings) >= 3:
                prefix_parts.append(quality_descriptor)
            if decade_descriptor and len(movie_years) >= 3:
                prefix_parts.append(decade_descriptor)
            
            prefix = ' '.join(prefix_parts) + ' ' if prefix_parts else ''
            
            if len(top_genres) == 2:
                return f"🎬 {prefix}{top_genres[0][0]}-{top_genres[1][0]} Fusion ({total_nodes} nodes)"
            return f"🎬 {prefix}{top_genres[0][0]}-Centric Network ({total_nodes} nodes)"
            
        elif dominant_type[0] == 'country' and country_counts:
            top_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)[:2]
            
            # Add era to international collaborations
            prefix = f"{era_descriptor} " if era_descriptor and len(movie_years) >= 3 else ""
            
            if len(top_countries) == 2:
                return f"🌍 {prefix}{top_countries[0][0]}-{top_countries[1][0]} Films ({total_nodes} nodes)"
            return f"🌍 {prefix}{top_countries[0][0]}-Led Cinema ({total_nodes} nodes)"
            
        elif dominant_type[0] == 'director':
            if theme_descriptor:
                return f"🎥 {theme_descriptor} Director Collaborative ({total_nodes} nodes)"
            return f"🎥 Director-Led Collaborative ({total_nodes} nodes)"
            
        elif dominant_type[0] == 'actor':
            # Check if there's a central movie connecting the actors
            if most_connected_movie_title and people_count > 8 and movie_count <= 3:
                movie_name = most_connected_movie_title
                if len(movie_name) > 30:
                    movie_name = movie_name[:27] + "..."
                return f"🎬 {movie_name} Cast ({total_nodes} nodes)"
            
            # Try to use genre/theme for more meaningful names
            if genre_counts and len(movie_titles) >= 2:
                top_genre = max(genre_counts.items(), key=lambda x: x[1])[0]
                prefix_parts = []
                if decade_descriptor and len(movie_years) >= 3:
                    prefix_parts.append(decade_descriptor)
                if quality_descriptor and len(movie_ratings) >= 2:
                    prefix_parts.append(quality_descriptor)
                prefix = ' '.join(prefix_parts) + ' ' if prefix_parts else ''
                return f"🎬 {prefix}{top_genre} Cast ({total_nodes} nodes)"
            elif theme_descriptor and len(movie_titles) >= 2:
                prefix = f"{decade_descriptor} " if decade_descriptor else ""
                return f"🎬 {prefix}{theme_descriptor} Cast Network ({total_nodes} nodes)"
            elif decade_descriptor and len(movie_years) >= 3:
                return f"⭐ {decade_descriptor} Actor Network ({total_nodes} nodes)"
            else:
                # Last resort - use movie titles if available
                if len(movie_titles) >= 2:
                    return f"🎬 Mixed Cast Network ({total_nodes} nodes)"
                return f"⭐ Actor Network ({total_nodes} nodes)"
            
        elif dominant_type[0] == 'movie':
            # Use keyword themes for movie clusters
            if theme_descriptor and top_keywords:
                keyword_hint = top_keywords[0][0].title()
                return f"🎞️ {theme_descriptor} Cinema: {keyword_hint} ({total_nodes} nodes)"
            return f"🎞️ Movie-Heavy Cluster ({total_nodes} nodes)"
        else:
            return f"🔹 {dominant_type[0].title()}-Dominant Mix ({total_nodes} nodes)"
    
    else:  # Highly mixed community (<50% dominant)
        # Get top 3 types
        type_list = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # Use semantic descriptors for mixed communities
        descriptor_parts = []
        
        # Add quality if movies are present
        if 'movie' in type_counts and quality_descriptor and len(movie_ratings) >= 3:
            descriptor_parts.append(quality_descriptor)
        
        # Add era if temporal clustering exists
        if 'movie' in type_counts and era_descriptor and len(movie_years) >= 3:
            descriptor_parts.append(era_descriptor)
        
        # Add theme from keywords
        if theme_descriptor and top_keywords:
            descriptor_parts.append(theme_descriptor)
        
        # Build the name
        prefix = ' '.join(descriptor_parts) + ' ' if descriptor_parts else ''
        
        # Create descriptive mix based on actual composition
        if len(type_list) >= 3:
            types_str = f"{type_list[0][0]}s, {type_list[1][0]}s & {type_list[2][0]}s"
            
            # If we have strong keywords, use them
            if top_keywords and len(top_keywords) >= 2:
                kw1, kw2 = top_keywords[0][0].title(), top_keywords[1][0].title()
                return f"🌟 {prefix}Diverse: {kw1} & {kw2} ({total_nodes} nodes)"
            
            return f"🌟 {prefix}Diverse Network: {types_str} ({total_nodes} nodes)"
            
        elif len(type_list) == 2:
            types_str = f"{type_list[0][0]}s & {type_list[1][0]}s"
            
            # Use top keyword if available
            if top_keywords:
                keyword_hint = top_keywords[0][0].title()
                return f"🌟 {prefix}Mixed: {types_str} - {keyword_hint} ({total_nodes} nodes)"
            
            return f"🌟 {prefix}Mixed Community: {types_str} ({total_nodes} nodes)"
        else:
            return f"🌟 {prefix}Varied Network ({total_nodes} nodes)"



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
            # Skip single-node communities - they're not meaningful clusters
            if len(community) <= 1:
                continue
            
            # Check if community is a single connected component
            subgraph = G.subgraph(community)
            
            if nx.is_connected(subgraph):
                # Community is well-connected, keep it
                refined.append(community)
            else:
                # Community is disconnected, split it
                for connected_part in nx.connected_components(subgraph):
                    # Only keep parts with at least 2 nodes
                    if connected_part and len(connected_part) >= 2:
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
        
        # Add virtual edges between movies in the same collection
        # This encourages movies from the same collection to be in the same community
        collection_groups = defaultdict(list)
        for node in nodes:
            collection_id = node.get('collection_id')
            if collection_id is not None and node.get('type') == 'movie':
                collection_groups[collection_id].append(node['id'])
        
        collection_edges_added = 0
        for collection_id, movie_ids in collection_groups.items():
            if len(movie_ids) > 1:  # Only if collection has multiple movies
                # Add strong virtual edges between all movies in this collection
                for i, movie_id_1 in enumerate(movie_ids):
                    for movie_id_2 in movie_ids[i+1:]:
                        if movie_id_1 in G and movie_id_2 in G:
                            # Use high weight (5.0) to strongly encourage same community
                            # If edge already exists, increase its weight
                            if G.has_edge(movie_id_1, movie_id_2):
                                G[movie_id_1][movie_id_2]['weight'] += 5.0
                            else:
                                G.add_edge(movie_id_1, movie_id_2, weight=5.0)
                            collection_edges_added += 1
        
        if collection_edges_added > 0:
            logger.info(f"Added {collection_edges_added} virtual edges for {len(collection_groups)} movie collections")
        
        # Handle empty or trivial graphs
        if len(G) == 0:
            return CommunitiesResult(
                communities={},
                stats={'num_communities': 0, 'modularity': 0.0},
                method='leiden',
                modularity=0.0
            )
        
        if len(G) == 1:
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
                method='leiden',
                modularity=0.0
            )
        
        # Run Leiden algorithm
        logger.info(f"Running Leiden algorithm on graph with {len(G)} nodes and {len(G.edges())} edges")
        communities = leiden_communities(G, resolution=resolution, random_state=random_state)
        
        # Filter out empty and single-member communities (communities must have at least 2 members)
        original_count = len(communities)
        communities = [community for community in communities if community and len(community) >= 2]
        filtered_count = len(communities)
        
        if original_count != filtered_count:
            logger.info(f"Filtered out {original_count - filtered_count} single-member or empty communities (kept {filtered_count} valid communities)")
        
        # Calculate overall modularity
        overall_modularity = nx.algorithms.community.modularity(G, communities, weight='weight', resolution=resolution)
        
        # Convert to dictionary format
        community_dict = {}
        for i, community in enumerate(communities):
            # Skip empty or single-member communities
            if not community or len(community) < 2:
                continue
                
            community_id = f"community_{i}"
            community_nodes = list(community)
            
            # Generate meaningful name
            community_name = generate_community_name(community_nodes, nodes)
            
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
            community_name = generate_community_name(community_nodes, nodes)
            
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
