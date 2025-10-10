"""Core graph builder - Refactored implementation.

This module contains the refactored build_network_graph function that uses
all the modular components instead of legacy monolithic code.
"""

import logging
from typing import Dict, List, Any, Set
from collections import defaultdict
from django.db.models import Count, Q, Avg
from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model

from custom_auth.models import Review
from movies.models import Movie

from ..queries import (
    get_movie_stats_optimized,
    get_user_rating_matrix_optimized,
    get_item_means_optimized,
)
from ..algorithms import (
    get_collaborative_filtering_predictions,
    cosine_similarity,
)
from ..layout import (
    enhance_graph_layout,
    calculate_smart_edge_lengths,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def _movie_ct():
    """Lazy ContentType loading to avoid migration issues."""
    return ContentType.objects.get_for_model(Movie)


def build_network_graph_refactored(
    current_user: User,
    *,
    min_reviews: int = 2,
    rating_threshold: float = 7.0,
    max_nodes: int = 500,
    chaos_mode: bool = False,
    show_countries: bool = True,
    show_genres: bool = True,
    show_directors: bool = True,
    show_predictions: bool = True,
    predictions_limit: int = 10,
    movie_limit: int = 100,
    show_similarity: bool = True,
    show_actors: bool = False,
    show_crew: bool = False
) -> Dict[str, Any]:
    """Build network graph using refactored modular components.
    
    This is a complete rewrite that uses the modular package structure
    instead of the monolithic legacy code.
    
    Args:
        current_user: User requesting the graph
        min_reviews: Minimum reviews for inclusion
        rating_threshold: Minimum rating for connections
        max_nodes: Maximum nodes before sampling
        chaos_mode: Random vs structured layout
        show_countries: Include country nodes
        show_genres: Include genre nodes  
        show_directors: Include director nodes
        show_predictions: Include prediction nodes
        predictions_limit: Max predictions
        movie_limit: Max movies
        show_similarity: Show similarity edges
        show_actors: Include actor nodes
        show_crew: Include crew nodes
    
    Returns:
        Dict with nodes, edges, stats, layout_config
    """
    logger.info(f"Building refactored graph for user {current_user.id}")
    
    nodes = []
    edges = []
    node_ids = set()
    movie_content_type = _movie_ct()
    
    # ========== STEP 1: GATHER USERS ==========
    logger.debug("Step 1: Gathering active users")
    active_users = User.objects.annotate(
        movie_review_count=Count('reviews', filter=Q(reviews__content_type=movie_content_type))
    ).filter(movie_review_count__gte=min_reviews)
    
    # Ensure current user is included
    if current_user and current_user not in active_users:
        active_users = list(active_users)
        active_users.append(current_user)
    
    # Fallback if no users
    if hasattr(active_users, 'exists') and not active_users.exists():
        active_users = User.objects.annotate(
            movie_review_count=Count('reviews', filter=Q(reviews__content_type=movie_content_type))
        ).filter(movie_review_count__gte=1)[:max_nodes//3]
    
    # ========== STEP 2: GATHER MOVIES ==========
    logger.debug("Step 2: Gathering well-reviewed movies")
    good_reviews = Review.objects.filter(
        content_type=movie_content_type,
        user__in=active_users,
        rating__gte=rating_threshold
    ).values('user_id', 'object_id', 'rating')
    
    well_reviewed_movie_ids = {r['object_id'] for r in good_reviews}
    movie_stats = get_movie_stats_optimized(list(well_reviewed_movie_ids))
    
    well_reviewed_movies = list(
        Movie.objects.filter(id__in=movie_stats.keys())
        .prefetch_related('genres', 'countries', 'keywords')
    )[:movie_limit]
    
    # Fallback if no movies
    if not well_reviewed_movies:
        logger.warning("No well-reviewed movies found, using fallback")
        fallback_movie_cap = min(movie_limit, max_nodes // 2)
        all_reviews = Review.objects.filter(content_type=movie_content_type).values('object_id')
        fallback_ids = list({r['object_id'] for r in all_reviews})[:fallback_movie_cap]
        
        fallback_stats = get_movie_stats_optimized(fallback_ids)
        movie_stats.update(fallback_stats)
        
        well_reviewed_movies = list(
            Movie.objects.filter(id__in=fallback_stats.keys())
            .prefetch_related('genres', 'countries', 'keywords')
        )
    
    # ========== STEP 3: BUILD USER NODES ==========
    logger.debug("Step 3: Building user nodes")
    user_nodes = {}
    user_review_counts = {}
    
    for user in list(active_users)[:max_nodes//3]:
        node_id = f"user_{user.id}"
        user_nodes[user.id] = node_id
        node_ids.add(node_id)
        
        user_movie_reviews = getattr(user, 'movie_review_count', None)
        if user_movie_reviews is None:
            user_movie_reviews = Review.objects.filter(
                user=user, 
                content_type=movie_content_type
            ).count()
        user_review_counts[user.id] = user_movie_reviews
        
        # Scale user size based on review count
        user_size = max(15, min(25, 20 + user_movie_reviews * 0.3))
        
        # Get profile picture URL
        profile_picture_url = None
        if hasattr(user, 'get_profile_picture'):
            profile_picture_url = user.get_profile_picture()
        elif hasattr(user, 'profile_picture') and user.profile_picture:
            try:
                profile_picture_url = user.profile_picture.url
            except:
                pass
        
        nodes.append({
            'id': node_id,
            'label': user.username,
            'type': 'user',
            'size': user_size,
            'color': '#4299E1',
            'review_count': user_movie_reviews,
            'profile_picture': profile_picture_url
        })
    
    # ========== STEP 4: BUILD MOVIE NODES ==========
    logger.debug("Step 4: Building movie nodes")
    movie_nodes = {}
    
    for movie in well_reviewed_movies:
        node_id = f"movie_{movie.id}"
        movie_nodes[movie.id] = node_id
        node_ids.add(node_id)
        
        stats_m = movie_stats[movie.id]
        review_count = stats_m.get('review_count', 0)
        avg_rating = stats_m.get('avg_rating', 0)
        
        # Scale movie size based on review count
        movie_size = max(10, min(30, 15 + review_count * 1.5))
        
        # Collect keywords for semantic community naming
        movie_keywords = [
            {'name': kw.name, 'id': kw.tmdb_id} 
            for kw in movie.keywords.all()[:10]  # Limit to top 10 keywords
        ]
        
        # Get collection ID if movie belongs to one
        collection_id = movie.collection_id if movie.collection else None
        
        nodes.append({
            'id': node_id,
            'label': movie.title,
            'type': 'movie',
            'size': movie_size,
            'color': '#F56565',
            'review_count': review_count,
            'rating': round(avg_rating, 1) if avg_rating else None,
            'year': movie.release_date.year if movie.release_date else None,
            'tmdb_id': movie.tmdb_id,
            'keywords': movie_keywords,  # For semantic community naming
            'collection_id': collection_id  # For community detection
        })
    
    # ========== STEP 5: BUILD GENRE/COUNTRY/DIRECTOR NODES ==========
    logger.debug("Step 5: Building attribute nodes")
    genre_counts = defaultdict(int)
    country_counts = defaultdict(int)
    director_counts = defaultdict(int) if show_directors else None
    actor_counts = defaultdict(int) if show_actors else None
    crew_counts = defaultdict(int) if show_crew else None
    
    # Pre-compute all movie relationships to avoid repeated database calls
    movie_directors = []
    movie_cast = []
    movie_crew = []
    
    # Count occurrences
    for movie in well_reviewed_movies:
        for genre in movie.genres.all():
            genre_counts[genre] += 1
        for country in movie.countries.all():
            country_counts[country] += 1
        
        # Handle directors, cast, crew if needed (using model properties)
        if show_directors or show_actors or show_crew:
            if show_directors and hasattr(movie, 'directors'):
                directors = list(movie.directors)
                movie_directors.append((movie.id, directors))
                for director in directors:
                    director_counts[director] += 1
            
            if chaos_mode and (show_actors or show_crew):
                if show_actors and hasattr(movie, 'cast'):
                    # Get cast and deduplicate by person (rare but possible)
                    cast_raw = list(movie.cast)[:16]
                    seen_person_ids = set()
                    cast_deduped = []
                    for mp in cast_raw:
                        person = getattr(mp, 'person', None)
                        if person and person.id not in seen_person_ids:
                            seen_person_ids.add(person.id)
                            cast_deduped.append(mp)
                            actor_counts[person] += 1
                    movie_cast.append((movie.id, cast_deduped[:8]))  # Limit after dedup
                
                if show_crew and hasattr(movie, 'crew'):
                    # Get crew and deduplicate by person (same person may have multiple roles)
                    crew_raw = [mp for mp in movie.crew[:16] if getattr(mp, 'role', '') != 'Director']
                    seen_person_ids = set()
                    crew_deduped = []
                    for mp in crew_raw:
                        person = getattr(mp, 'person', None)
                        if person and person.id not in seen_person_ids:
                            seen_person_ids.add(person.id)
                            crew_deduped.append(mp)
                            crew_counts[person] += 1
                    movie_crew.append((movie.id, crew_deduped[:8]))  # Limit after dedup
    
    # Create nodes
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
                'size': max(15, min(30, 18 + count * 1.2)),
                'color': '#9F7AEA',
                'movie_count': count
            })
    
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
    
    director_nodes = {}
    if show_directors:
        for person, count in director_counts.items():
            if count >= 1:  # Show all directors
                node_id = f"director_{person.id}"
                director_nodes[person.id] = node_id
                node_ids.add(node_id)
                nodes.append({
                    'id': node_id,
                    'label': person.name,
                    'type': 'director',
                    'size': max(12, min(25, 15 + count * 2)),
                    'color': '#ED8936',
                    'movie_count': count
                })
    
    actor_nodes = {}
    if show_actors:
        for person, count in actor_counts.items():
            if count >= 1:  # Show all actors
                node_id = f"actor_{person.id}"
                actor_nodes[person.id] = node_id
                node_ids.add(node_id)
                nodes.append({
                    'id': node_id,
                    'label': person.name,
                    'type': 'actor',
                    'size': max(10, min(20, 12 + count * 1.5)),
                    'color': '#FFD700'
                })
    
    crew_nodes = {}
    if show_crew:
        for person, count in crew_counts.items():
            if count >= 1:  # Show all crew members
                node_id = f"crew_{person.id}"
                crew_nodes[person.id] = node_id
                node_ids.add(node_id)
                nodes.append({
                    'id': node_id,
                    'label': person.name,
                    'type': 'crew',
                    'size': max(10, min(18, 12 + count)),
                    'color': '#00CED1'
                })
    
    # ========== STEP 6: BUILD EDGES ==========
    logger.debug("Step 6: Building edges")
    
    # User-Movie review edges
    reviews_list = list(good_reviews)
    for review in reviews_list:
        user_id = review['user_id']
        movie_id = review['object_id']
        rating = review['rating']
        
        if user_id in user_nodes and movie_id in movie_nodes:
            edges.append({
                'source': user_nodes[user_id],
                'target': movie_nodes[movie_id],
                'type': 'review',
                'weight': rating / 10.0,
                'rating': rating
            })
    
    # Movie-Genre edges
    if show_genres:
        for movie in well_reviewed_movies:
            if movie.id in movie_nodes:
                for genre in movie.genres.all():
                    if genre.id in genre_nodes:
                        edges.append({
                            'source': movie_nodes[movie.id],
                            'target': genre_nodes[genre.id],
                            'type': 'has_genre'
                        })
    
    # Movie-Country edges
    if show_countries:
        for movie in well_reviewed_movies:
            if movie.id in movie_nodes:
                for country in movie.countries.all():
                    if country.id in country_nodes:
                        edges.append({
                            'source': movie_nodes[movie.id],
                            'target': country_nodes[country.id],
                            'type': 'from_country'
                        })
    
    # Movie-Director edges
    if show_directors:
        for movie_id, directors in movie_directors:
            if movie_id in movie_nodes:
                for director in directors:
                    if director.id in director_nodes:
                        edges.append({
                            'source': movie_nodes[movie_id],
                            'target': director_nodes[director.id],
                            'type': 'directed_by',
                            'weight': 1.5  # Directors are strong connections
                        })
    
    # Movie-Actor edges - Create edges for ALL actors that have nodes (deduplicated)
    if show_actors and chaos_mode:
        added_actor_edges = set()  # Track (movie_id, person_id) pairs
        for movie in well_reviewed_movies:
            if movie.id in movie_nodes and hasattr(movie, 'cast'):
                for mp in movie.cast:
                    person = getattr(mp, 'person', None)
                    if person and person.id in actor_nodes:
                        edge_key = (movie.id, person.id)
                        if edge_key not in added_actor_edges:
                            edges.append({
                                'source': movie_nodes[movie.id],
                                'target': actor_nodes[person.id],
                                'type': 'acted_in',
                                'weight': 1.0  # Actors are medium connections
                            })
                            added_actor_edges.add(edge_key)
    
    # Movie-Crew edges - Create edges for ALL crew that have nodes (deduplicated)
    if show_crew and chaos_mode:
        added_crew_edges = set()  # Track (movie_id, person_id) pairs
        for movie in well_reviewed_movies:
            if movie.id in movie_nodes and hasattr(movie, 'crew'):
                for mp in movie.crew:
                    person = getattr(mp, 'person', None)
                    # Skip directors (they have their own edges)
                    if person and person.id in crew_nodes and getattr(mp, 'role', '') != 'Director':
                        edge_key = (movie.id, person.id)
                        if edge_key not in added_crew_edges:
                            edges.append({
                                'source': movie_nodes[movie.id],
                                'target': crew_nodes[person.id],
                                'type': 'crew',
                                'weight': 0.3  # Crew are weak connections (prevents spurious communities)
                            })
                            added_crew_edges.add(edge_key)
    
    # ========== STEP 7: SIMILARITY EDGES (DISABLED) ==========
    # User-to-user similarity edges removed as they clutter the graph without adding value
    # Users are already connected through shared movie ratings
    # if show_similarity and len(user_nodes) > 1:
    #     logger.debug("Step 7: Computing similarity edges")
    #     user_ids_list = list(user_nodes.keys())
    #     rating_matrix = get_user_rating_matrix_optimized(user_ids_list)
    #     
    #     for i, user_id_1 in enumerate(user_ids_list):
    #         for user_id_2 in user_ids_list[i+1:]:
    #             if user_id_1 in rating_matrix and user_id_2 in rating_matrix:
    #                 similarity = cosine_similarity(
    #                     rating_matrix[user_id_1],
    #                     rating_matrix[user_id_2]
    #                 )
    #                 if similarity > 0.3:  # Only show strong similarities
    #                     edges.append({
    #                         'source': user_nodes[user_id_1],
    #                         'target': user_nodes[user_id_2],
    #                         'type': 'similarity',
    #                         'weight': similarity,
    #                         'similarity_score': round(similarity, 2)
    #                     })
    
    # ========== STEP 8: PREDICTIONS ==========
    if show_predictions and current_user and current_user.id in user_nodes:
        logger.debug("Step 8: Generating predictions")
        try:
            user_ids_for_cf = list(user_nodes.keys())
            rating_matrix = get_user_rating_matrix_optimized(user_ids_for_cf)
            item_means = get_item_means_optimized(list(movie_nodes.keys()))
            
            predictions = get_collaborative_filtering_predictions(
                user_id=current_user.id,
                rating_matrix=rating_matrix,
                item_means=item_means,
                top_n=predictions_limit
            )
            
            for pred in predictions[:predictions_limit]:
                movie_id = pred['movie_id']
                if movie_id not in movie_nodes:
                    # Add predicted movie as node
                    try:
                        movie = Movie.objects.get(id=movie_id)
                        pred_node_id = f"prediction_{movie_id}"
                        node_ids.add(pred_node_id)
                        
                        # Collect keywords for predictions too
                        pred_keywords = [
                            {'name': kw.name, 'id': kw.tmdb_id} 
                            for kw in movie.keywords.all()[:10]
                        ]
                        
                        nodes.append({
                            'id': pred_node_id,
                            'label': movie.title,
                            'type': 'movie',
                            'size': 18,
                            'color': '#FFD700',
                            'predicted_score': pred['predicted_rating'],
                            'year': movie.release_date.year if movie.release_date else None,
                            'tmdb_id': movie.tmdb_id,
                            'keywords': pred_keywords  # For semantic community naming
                        })
                        
                        # Add prediction edge
                        edges.append({
                            'source': user_nodes[current_user.id],
                            'target': pred_node_id,
                            'type': 'prediction',
                            'weight': pred['predicted_rating'] / 10.0,
                            'predicted_rating': pred['predicted_rating']
                        })
                    except Movie.DoesNotExist:
                        pass
        except Exception as e:
            logger.warning(f"Failed to generate predictions: {e}")
    
    # ========== STEP 9: ENHANCE LAYOUT ==========
    logger.debug("Step 9: Enhancing layout with MultiGravity Force Atlas")
    result = enhance_graph_layout(nodes, edges)
    
    # Extract the returned values
    nodes = result.get('nodes', nodes)
    edges = result.get('edges', edges)
    layout_config = result.get('layout_config', {})
    
    # ========== STEP 10: CALCULATE STATISTICS ==========
    stats = {
        'total_nodes': len(nodes),
        'total_edges': len(edges),
        'user_count': len(user_nodes),
        'movie_count': len(movie_nodes),
        'genre_count': len(genre_nodes) if show_genres else 0,
        'country_count': len(country_nodes) if show_countries else 0,
        'director_count': len(director_nodes) if show_directors else 0,
        'actor_count': len(actor_nodes) if show_actors else 0,
        'crew_count': len(crew_nodes) if show_crew else 0,
    }
    
    logger.info(
        f"Graph built successfully: {stats['total_nodes']} nodes, "
        f"{stats['total_edges']} edges"
    )
    
    return {
        'nodes': nodes,
        'edges': edges,
        'stats': stats,
        'layout_config': layout_config
    }
