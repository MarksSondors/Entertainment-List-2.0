"""Type definitions for network graph module.

This module contains all TypedDict definitions and type aliases used throughout
the network graph package, providing strong type safety and better IDE support.
"""

from typing import Dict, List, Set, Tuple, Optional, Any, TypedDict, Literal, Union
from datetime import datetime


# Node Types
NodeType = Literal['user', 'movie', 'genre', 'country', 'director', 'actor', 'crew']
EdgeType = Literal[
    'review', 'similarity', 'prediction', 'origin', 'genre', 
    'directed_by', 'acted_in', 'crew', 
    'user_genre_affinity', 'user_country_affinity',
    'actor_genre_affinity', 'director_genre_affinity',
    'actor_country_affinity', 'director_country_affinity'
]


class NodeMetadata(TypedDict, total=False):
    """Metadata for graph nodes."""
    username: str
    title: str
    release_date: str
    rating: float
    review_count: int
    runtime: int
    countries: List[str]
    genres: List[str]
    name: str
    iso_code: str
    movie_count: int


class EdgeMetadata(TypedDict, total=False):
    """Metadata for graph edges."""
    rating: float
    review_text: Optional[str]
    date: str
    similarity: float
    common_movies: int


class EdgeColor(TypedDict, total=False):
    """Edge color configuration."""
    color: str
    opacity: float


class NodeDict(TypedDict, total=False):
    """Complete node structure for graph visualization."""
    id: Union[int, str]
    label: str
    type: NodeType
    group: str
    size: float
    color: str
    metadata: NodeMetadata
    review_count: int
    year: Optional[int]
    tmdb_id: Optional[int]
    movie_count: int
    
    # Community detection
    community: str
    community_size: int
    community_name: str
    
    # Centrality measures
    centrality: 'CentralityMeasures'
    
    # MultiGravity Force Atlas layout
    mass: float
    repulsion: float
    gravity_center_x: float
    gravity_center_y: float
    gravity_strength: float
    degree: int
    node_type: NodeType
    central_gravity: float
    damping_factor: float
    preferred_x: float
    preferred_y: float
    is_super_hub: bool
    
    # Predictions
    predicted_score: float
    predicted_contributors: int
    predicted_confidence: Literal['low', 'medium', 'high']


class EdgeDict(TypedDict, total=False):
    """Complete edge structure for graph visualization."""
    source: Union[int, str]
    target: Union[int, str]
    type: EdgeType
    weight: float
    color: Union[str, EdgeColor]
    width: float
    label: str
    metadata: EdgeMetadata
    length: int
    dashes: bool
    
    # MultiGravity Force Atlas
    strength: float
    edge_type: EdgeType


class CentralityMeasures(TypedDict):
    """Centrality measures for a node."""
    degree_centrality: float
    betweenness_centrality: float
    closeness_centrality: float
    eigenvector_centrality: float
    pagerank: float
    degree: int


class CommunityData(TypedDict):
    """Community information."""
    nodes: List[Union[int, str]]
    size: int
    name: str
    modularity: float
    internal_edges: int
    total_degree: float
    density: float


class CommunityStats(TypedDict):
    """Statistics about community detection."""
    num_communities: int
    modularity: float
    avg_community_size: float
    largest_community: int
    smallest_community: int
    community_size_std: float
    coverage: float
    resolution_used: float
    error: str  # Only present if error occurred


class CommunitiesResult(TypedDict):
    """Result from community detection."""
    communities: Dict[str, CommunityData]
    stats: CommunityStats
    method: Literal['leiden', 'louvain', 'greedy_modularity', 'leiden_failed', 'failed']
    modularity: float


class CentralityStats(TypedDict):
    """Statistics about centrality measures."""
    avg_degree_centrality: float
    max_degree_centrality: float
    avg_betweenness_centrality: float
    max_betweenness_centrality: float
    graph_density: float
    num_nodes: int
    num_edges: int
    error: str  # Only present if error occurred


class CentralityResult(TypedDict):
    """Result from centrality calculations."""
    centrality_measures: Dict[Union[int, str], CentralityMeasures]
    stats: CentralityStats
    method: Literal['networkx', 'failed']


class GravityCenter(TypedDict):
    """Gravity center configuration for MultiGravity Force Atlas."""
    x: float
    y: float
    strength: float


class LayoutConfig(TypedDict):
    """Configuration for MultiGravity Force Atlas layout."""
    algorithm: Literal['multigravity_force_atlas']
    gravity_centers: Dict[NodeType, GravityCenter]
    base_repulsion: float
    spring_constant: float
    damping: float
    theta: float
    gravity_falloff: float
    max_velocity: float
    min_velocity: float
    timestep: float
    stabilization_iterations: int
    avoid_overlap: float
    gravity_balance: float
    type_separation_force: float
    hub_anti_clustering: float
    adaptive_gravity: bool
    density_compensation: float
    size_compensation: float


class RenderingHints(TypedDict):
    """Hints for efficient frontend rendering."""
    use_webgl: bool
    edge_bundling: bool
    lod_threshold: int
    cluster_threshold: int
    physics_enabled: bool
    adaptive_quality: bool


class GraphData(TypedDict):
    """Complete graph data structure."""
    nodes: List[NodeDict]
    edges: List[EdgeDict]


class GraphStats(TypedDict, total=False):
    """Statistics about the graph."""
    total_nodes: int
    total_edges: int
    users: int
    movies: int
    countries: int
    genres: int
    directors: int
    actors: int
    crew: int
    recommendations: int
    
    # Analytics-specific stats
    total_reviews: int
    avg_rating: float
    most_reviewed_movie: Tuple[Any, int]
    most_active_user: Tuple[Any, int]
    
    error: str  # Only present if error occurred


class GraphAnalytics(TypedDict, total=False):
    """Analytics data for the graph."""
    communities: CommunitiesResult
    centrality: CentralityResult
    error: str


class GraphResult(TypedDict):
    """Complete result from graph building."""
    nodes: List[NodeDict]
    edges: List[EdgeDict]
    stats: GraphStats
    layout_config: LayoutConfig
    analytics: GraphAnalytics
    rendering_hints: RenderingHints


class AnalyticsContext(TypedDict):
    """Context for analytics graph."""
    graph_data: GraphData
    stats: GraphStats
    country_stats: List[Dict[str, Any]]
    communities: CommunitiesResult
    centrality: CentralityResult
    analytics_error: str  # Only present if error occurred


# Rating Matrix Types
RatingMatrix = Dict[int, Dict[int, float]]
SimilarityMatrix = Dict[Tuple[int, int], float]
UserPreferences = Dict[int, Set[int]]
MovieStats = Dict[int, Dict[str, Any]]
ItemMeans = Dict[int, float]

# Similarity Methods
SimilarityMethod = Literal['cosine', 'pearson', 'adjusted_cosine', 'jaccard']


class PredictionResult(TypedDict):
    """Result from collaborative filtering predictions."""
    movie_id: int
    predicted_score: float
    contributors: int
    confidence: Literal['low', 'medium', 'high']
