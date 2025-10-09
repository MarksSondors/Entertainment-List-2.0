"""Constants used throughout the network graph module."""

# Cache Configuration
CACHE_TIMEOUT = 3600  # 1 hour (default)
CACHE_TIMEOUT_SHORT = 600  # 10 minutes (user-specific data)
CACHE_TIMEOUT_MEDIUM = 3600  # 1 hour (graph data)
CACHE_TIMEOUT_LONG = 86400  # 24 hours (movie metadata)

# Graph Processing
MIN_SIMILARITY_THRESHOLD = 0.2
MAX_NODES_DEFAULT = 1000
MAX_EDGES_DEFAULT = None  # No limit on edges
MOVIE_LIMIT_DEFAULT = 500
SIMILARITY_BATCH_SIZE = 50

# Node Size Configuration (base, min, max)
NODE_SIZE_CONFIG = {
    'user': {'base': 20, 'min': 15, 'max': 25, 'scale': 0.3},
    'movie': {'base': 15, 'min': 10, 'max': 30, 'scale': 1.5},
    'country': {'base': 18, 'min': 15, 'max': 30, 'scale': 1.2},
    'genre': {'base': 20, 'min': 16, 'max': 28, 'scale': 1.0},
    'director': {'base': 13, 'min': 12, 'max': 18, 'scale': 0.5},
    'actor': {'base': 9, 'min': 8, 'max': 12, 'scale': 0.3},
    'crew': {'base': 9, 'min': 8, 'max': 14, 'scale': 0.4},
}

# Node Colors
NODE_COLORS = {
    'user': '#4299E1',      # Blue
    'movie': '#F56565',     # Red
    'country': '#48BB78',   # Green
    'genre': '#9F7AEA',     # Purple
    'director': '#ED8936',  # Orange
    'actor': '#FFD700',     # Gold
    'crew': '#00CED1',      # Turquoise
}

# Edge Colors
EDGE_COLORS = {
    'review': {'color': '#4299E1', 'opacity': 0.6},
    'similarity': {'color': '#38B2AC', 'opacity': 0.5},
    'prediction': {'color': '#FF6B6B', 'opacity': 0.6},
    'origin': {'color': '#48BB78', 'opacity': 0.4},
    'genre': {'color': '#9F7AEA', 'opacity': 0.4},
    'directed_by': {'color': '#ED8936', 'opacity': 0.5},
    'acted_in': {'color': '#FFD700', 'opacity': 0.35},
    'crew': {'color': '#00CED1', 'opacity': 0.35},
    'user_genre_affinity': {'color': '#6B46C1', 'opacity': 0.35},
    'user_country_affinity': {'color': '#2F855A', 'opacity': 0.35},
    'actor_genre_affinity': {'color': '#B794F4', 'opacity': 0.32},
    'director_genre_affinity': {'color': '#D69E2E', 'opacity': 0.32},
    'actor_country_affinity': {'color': '#68D391', 'opacity': 0.30},
    'director_country_affinity': {'color': '#ED8936', 'opacity': 0.30},
}

# Edge Lengths (for MultiGravity Force Atlas)
EDGE_LENGTHS = {
    'prediction': 120,
    'similarity': 100,
    'user_genre_affinity': 180,
    'user_country_affinity': 190,
    'actor_genre_affinity': 160,
    'director_genre_affinity': 160,
    'review': 80,
    'origin': 130,
    'genre': 125,
    'directed_by': 95,
    'acted_in': 100,
    'crew': 105,
    'default': 90,
}

# Edge Strengths (spring constants)
EDGE_STRENGTHS = {
    'prediction': 0.3,
    'similarity': 0.6,
    'affinity': 0.4,
    'review': 0.8,
    'default': 0.5,
}

# MultiGravity Force Atlas Configuration
GRAVITY_CENTERS = {
    'user': {'x': -400, 'y': -300, 'strength': 0.8},
    'movie': {'x': 400, 'y': -300, 'strength': 1.0},
    'genre': {'x': -400, 'y': 400, 'strength': 0.6},
    'country': {'x': 400, 'y': 400, 'strength': 0.6},
    'director': {'x': 0, 'y': -400, 'strength': 0.7},
    'actor': {'x': -200, 'y': 0, 'strength': 0.5},
    'crew': {'x': 200, 'y': 0, 'strength': 0.5},
}

# Mass multipliers for different node types
MASS_MULTIPLIERS = {
    'user': 1.2,
    'movie': 1.0,
    'genre': 0.8,
    'country': 0.8,
    'director': 1.1,
    'actor': 0.7,
    'crew': 0.6,
}

# Repulsion multipliers for different node types
REPULSION_MULTIPLIERS = {
    'user': 1.3,
    'movie': 1.0,
    'genre': 1.2,
    'country': 1.2,
    'director': 1.1,
    'actor': 0.9,
    'crew': 0.9,
}

# Layout Algorithm Parameters
LAYOUT_PARAMS = {
    'base_repulsion': -80,
    'spring_constant': 0.15,
    'damping': 0.45,
    'theta': 0.4,  # Barnes-Hut approximation
    'gravity_falloff': 2.0,
    'max_velocity': 25,
    'min_velocity': 0.05,
    'timestep': 0.35,
    'gravity_balance': 0.7,
    'type_separation_force': 2.0,
    'hub_anti_clustering': 1.5,
}

# Performance Thresholds
SUPER_HUB_THRESHOLD = 30  # Nodes with >30 connections
HUB_THRESHOLD = 15  # Nodes with >15 connections
LARGE_GRAPH_THRESHOLD = 500  # Graphs with >500 nodes
VERY_LARGE_GRAPH_THRESHOLD = 1000  # Graphs with >1000 nodes

# Rendering Hints Thresholds
WEBGL_THRESHOLD = 500  # Use WebGL for graphs >500 nodes
EDGE_BUNDLING_THRESHOLD = 1000  # Bundle edges for graphs >1000 edges
PHYSICS_THRESHOLD = 1000  # Disable physics for graphs >1000 nodes

# Community Detection
LEIDEN_MAX_ITERATIONS = 100
LEIDEN_TOLERANCE = 1e-6
LEIDEN_DEFAULT_RESOLUTION = 1.0
LEIDEN_RANDOM_STATE = 42

# Collaborative Filtering
CF_TOP_K_USERS = 10  # Use top 10 similar users for predictions
CF_MIN_COMMON_ITEMS = 2  # Minimum common items for similarity
CF_MIN_RATING_THRESHOLD = 1.0
CF_MAX_RATING_THRESHOLD = 10.0

# Affinity Edge Thresholds
MAX_GENRE_AFFINITIES = 3
MAX_COUNTRY_AFFINITIES = 2
MIN_AFFINITY_STRENGTH = 0.2
MIN_COUNTRY_AFFINITY_STRENGTH = 0.25

# User Similarity Thresholds
MIN_COMMON_MOVIES = 3
MIN_SIMILARITY_SCORE = 0.7
SIMILARITY_SHRINKAGE_ALPHA = 2

# Analytics
MIN_NODES_FOR_ANALYTICS = 5  # Minimum nodes to run analytics
MIN_NODES_FOR_CENTRALITY = 10  # Minimum nodes for centrality analysis

# Influence Analysis
INFLUENCE_DECAY_FACTOR = 0.5  # How fast influence decays over time
MIN_REVIEWS_FOR_INFLUENCE = 5  # Minimum reviews to calculate influence
INFLUENCE_TIME_WINDOW_DAYS = 365  # Look back 1 year for influence calculation

# Temporal Analysis
TEMPORAL_ANALYSIS_MIN_DATAPOINTS = 10  # Minimum data points for trend analysis
TEMPORAL_SMOOTHING_WINDOW = 7  # Days for moving average
TREND_CHANGE_THRESHOLD = 0.15  # 15% change to detect trend shift

# Metrics
ENGAGEMENT_ACTIVE_DAYS_THRESHOLD = 30  # Consider user active if reviewed in last 30 days
COMMUNITY_STABILITY_MIN_SIZE = 5  # Minimum community size for stability analysis

# Logging
LOG_BATCH_SIZE = 100  # Log progress every N items
