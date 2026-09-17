"""Movie-centric network graph builder (v2).

Movies are the primary nodes. Edges are concrete, explainable relationships -
shared director, shared lead cast, thematic keyword overlap, shared studio,
same collection - built via inverted-index co-occurrence instead of routing
everything through generic genre/country hub nodes (which mostly added visual
noise without meaningful clustering). Collections come back as
``compound_groups`` for the frontend to render as nested boxes rather than as
graph nodes. The social layer (user review/prediction overlay) is opt-in via
``include_social_layer`` and reuses the existing collaborative-filtering logic
unchanged - it's a toggleable overlay, not the primary lens anymore.
"""

import logging
import math
from collections import defaultdict
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q

from custom_auth.models import MediaPerson, Review
from movies.models import Movie

from .. import constants as const
from ..enrichment import load_movie_enrichment
from ..queries import (
    get_movie_stats_optimized,
    get_user_rating_matrix_optimized,
    get_item_means_optimized,
)
from ..algorithms import (
    get_user_similarity_matrix,
    get_collaborative_filtering_predictions,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def _movie_ct():
    """Lazy ContentType loading to avoid migration issues."""
    return ContentType.objects.get_for_model(Movie)


def _movie_node_id(movie_id: int) -> str:
    return f"movie_{movie_id}"


def _fetch_seed_movies(*, rating_threshold: float, movie_limit: int, seed_tmdb_ids: Optional[List[int]]):
    base_qs = (
        Movie.objects.exclude(release_date__isnull=True)
        .select_related('collection')
        .prefetch_related('genres', 'keywords', 'production_companies')
    )
    # movie_limit <= 0 means "no limit" - the graph is meant to show every movie, unfiltered
    limit_slice = slice(None) if movie_limit <= 0 else slice(movie_limit)

    if seed_tmdb_ids:
        return list(base_qs.filter(tmdb_id__in=seed_tmdb_ids)[limit_slice])

    movies = list(base_qs.filter(rating__gte=rating_threshold).order_by('-rating', '-release_date')[limit_slice])
    if not movies:
        logger.warning("No movies met rating_threshold=%s; falling back to top-rated movies", rating_threshold)
        movies = list(base_qs.order_by('-rating', '-release_date')[limit_slice])
    return movies


def _fetch_credits(movies) -> Tuple[Dict[int, list], Dict[int, list]]:
    """Bulk-fetch directors & top-billed cast for a set of movies in one query."""
    movie_ids = [m.id for m in movies]
    if not movie_ids:
        return {}, {}

    media_persons = (
        MediaPerson.objects.filter(
            content_type=_movie_ct(),
            object_id__in=movie_ids,
            role__in=['Director', 'Actor'],
        )
        .select_related('person')
        .order_by('object_id', 'order')
    )

    directors_by_movie = defaultdict(list)
    cast_by_movie = defaultdict(list)
    for mp in media_persons:
        if mp.role == 'Director':
            directors_by_movie[mp.object_id].append(mp.person)
        elif mp.role == 'Actor' and (mp.order or 0) < const.TOP_CAST_BILLING_LIMIT:
            cast_by_movie[mp.object_id].append(mp.person)
    return directors_by_movie, cast_by_movie


def _cooccurrence_edges(
    groups: Dict[Any, List[str]],
    *,
    edge_type: str,
    base_weight: float,
    max_group_size: int,
    min_group_size: int = 2,
) -> Dict[Tuple[str, str], Dict[str, Dict[str, Any]]]:
    """Weighted movie<->movie edge contributions from a shared-attribute grouping.

    Rarer groups (fewer shared movies) contribute more weight per pair than
    common ones since weight is divided by group size - this is what keeps
    resulting clusters thematically meaningful instead of noisy.
    """
    contributions: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    for key, movie_ids in groups.items():
        unique_ids = sorted(set(movie_ids))
        size = len(unique_ids)
        if size < min_group_size or size > max_group_size:
            continue
        weight = base_weight / size
        for a, b in combinations(unique_ids, 2):
            entry = contributions.setdefault((a, b), {})
            type_entry = entry.setdefault(edge_type, {'weight': 0.0, 'keys': []})
            type_entry['weight'] += weight
            type_entry['keys'].append(key)
    return contributions


def _merge_contributions(*contribution_maps) -> Dict[Tuple[str, str], Dict[str, Dict[str, Any]]]:
    merged: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}
    for contributions in contribution_maps:
        for pair, by_type in contributions.items():
            entry = merged.setdefault(pair, {})
            for edge_type, data in by_type.items():
                type_entry = entry.setdefault(edge_type, {'weight': 0.0, 'keys': []})
                type_entry['weight'] += data['weight']
                type_entry['keys'].extend(data['keys'])
    return merged


def _reason_text(edge_type: str, keys: List[Any], label_map: Dict[Any, str]) -> Optional[str]:
    labels = [label_map.get(k, str(k)) for k in keys]
    labels = [label for label in labels if label]
    if not labels:
        return None
    if edge_type == 'shared_director':
        return f"Directed by {', '.join(labels)}"
    if edge_type == 'shared_actor':
        if len(labels) == 1:
            return f"Co-starring {labels[0]}"
        shown = ', '.join(labels[:3]) + ('…' if len(labels) > 3 else '')
        return f"{len(labels)} shared cast: {shown}"
    if edge_type == 'keyword_similarity':
        shown = ', '.join(labels[:3]) + ('…' if len(labels) > 3 else '')
        return f"Shared themes: {shown}"
    if edge_type == 'same_studio':
        return f"Both from {labels[0]}"
    if edge_type == 'same_collection':
        return f"Part of {labels[0]}"
    return None


def _build_movie_nodes_and_groups(movies, movie_stats, enrichment):
    """Build movie nodes plus the co-occurrence groupings used for edges/compounds."""
    nodes: List[Dict[str, Any]] = []
    movie_nodes: Dict[int, str] = {}

    directors_by_movie, cast_by_movie = _fetch_credits(movies)

    director_groups: Dict[int, List[str]] = defaultdict(list)
    actor_groups: Dict[int, List[str]] = defaultdict(list)
    keyword_groups: Dict[str, List[str]] = defaultdict(list)
    studio_groups: Dict[str, List[str]] = defaultdict(list)
    collection_groups: Dict[int, List[str]] = defaultdict(list)
    collection_meta: Dict[int, Dict[str, Any]] = {}

    director_labels: Dict[int, str] = {}
    actor_labels: Dict[int, str] = {}
    collection_labels: Dict[int, str] = {}

    popularities = [enrichment.get(m.tmdb_id, {}).get('popularity', 0.0) for m in movies]
    max_popularity = max(popularities) if popularities else 0.0
    review_counts = [movie_stats.get(m.id, {}).get('review_count', 0) for m in movies]
    max_review_count = max(review_counts) if review_counts else 0

    for movie in movies:
        node_id = _movie_node_id(movie.id)
        movie_nodes[movie.id] = node_id

        stats_m = movie_stats.get(movie.id, {})
        review_count = stats_m.get('review_count', 0)
        user_rating = stats_m.get('avg_rating', 0.0)

        enrich = enrichment.get(movie.tmdb_id, {})
        popularity = enrich.get('popularity', 0.0)

        directors = directors_by_movie.get(movie.id, [])
        cast = cast_by_movie.get(movie.id, [])
        for director in directors:
            director_groups[director.id].append(node_id)
            director_labels[director.id] = director.name
        for actor in cast:
            actor_groups[actor.id].append(node_id)
            actor_labels[actor.id] = actor.name

        keywords = [kw.name for kw in movie.keywords.all()[:10]]
        for kw in keywords:
            keyword_groups[kw.lower()].append(node_id)

        studio_name = None
        companies = list(movie.production_companies.all()[:1])
        if companies:
            studio_name = companies[0].name
        elif enrich.get('studios'):
            studio_name = enrich['studios'][0]
        if studio_name:
            studio_groups[studio_name].append(node_id)

        collection_id = movie.collection_id
        collection_name = None
        if collection_id:
            collection_groups[collection_id].append(node_id)
            if collection_id not in collection_meta:
                collection_meta[collection_id] = {
                    'id': f"collection_{collection_id}",
                    'collection_id': collection_id,
                    'name': movie.collection.name,
                    'poster': movie.collection.poster,
                    'movie_ids': [],
                }
            collection_meta[collection_id]['movie_ids'].append(node_id)
            collection_name = movie.collection.name
            collection_labels[collection_id] = collection_name

        # Importance blend drives node size and the frontend's fcose "mass" hint
        norm_popularity = math.log1p(popularity) / math.log1p(max_popularity) if max_popularity else 0.0
        norm_reviews = math.log1p(review_count) / math.log1p(max_review_count) if max_review_count else 0.0
        importance = (
            const.IMPORTANCE_WEIGHTS['popularity'] * norm_popularity
            + const.IMPORTANCE_WEIGHTS['rating'] * ((movie.rating or 0) / 10.0)
            + const.IMPORTANCE_WEIGHTS['review_count'] * norm_reviews
        )
        size = max(18, min(60, 24 + importance * 40))

        nodes.append({
            'id': node_id,
            'label': movie.title,
            'type': 'movie',
            'size': round(size, 1),
            'importance': round(importance, 3),
            'tmdb_id': movie.tmdb_id,
            'poster': movie.poster,
            'backdrop': movie.backdrop,
            'rating': movie.rating,
            'user_rating': round(user_rating, 1) if user_rating else None,
            'review_count': review_count,
            'year': movie.release_date.year if movie.release_date else None,
            'runtime': movie.runtime,
            'genres': [g.name for g in movie.genres.all()],
            'keywords': keywords,
            'director_names': [d.name for d in directors],
            'top_cast': [a.name for a in cast[:5]],
            'studio': studio_name,
            'collection_id': collection_id,
            'collection_name': collection_name,
            'popularity': round(popularity, 2) if popularity else 0.0,
            'budget': enrich.get('budget') or None,
            'revenue': enrich.get('revenue') or None,
        })

    compound_groups = [
        {
            'id': meta['id'],
            'collection_id': cid,
            'label': meta['name'],
            'poster': meta['poster'],
            'movie_ids': meta['movie_ids'],
        }
        for cid, meta in collection_meta.items()
        if len(meta['movie_ids']) >= 2
    ]

    groups = {
        'director_groups': director_groups,
        'actor_groups': actor_groups,
        'keyword_groups': keyword_groups,
        'studio_groups': studio_groups,
        'collection_groups': collection_groups,
    }
    labels = {
        'shared_director': director_labels,
        'shared_actor': actor_labels,
        'keyword_similarity': {},
        'same_studio': {},
        'same_collection': collection_labels,
    }
    return nodes, movie_nodes, compound_groups, groups, labels, directors_by_movie, cast_by_movie


def _build_relationship_edges(groups: Dict[str, Dict[Any, List[str]]], labels: Dict[str, Dict[Any, str]]) -> List[Dict[str, Any]]:
    contributions = _merge_contributions(
        _cooccurrence_edges(
            groups['director_groups'], edge_type='shared_director',
            base_weight=const.EDGE_BASE_WEIGHTS['shared_director'], max_group_size=50,
        ),
        _cooccurrence_edges(
            groups['actor_groups'], edge_type='shared_actor',
            base_weight=const.EDGE_BASE_WEIGHTS['shared_actor'], max_group_size=50,
        ),
        _cooccurrence_edges(
            groups['keyword_groups'], edge_type='keyword_similarity',
            base_weight=const.EDGE_BASE_WEIGHTS['keyword_similarity'], max_group_size=const.MAX_KEYWORD_GROUP_SIZE,
        ),
        _cooccurrence_edges(
            groups['studio_groups'], edge_type='same_studio',
            base_weight=const.EDGE_BASE_WEIGHTS['same_studio'], max_group_size=const.MAX_STUDIO_GROUP_SIZE,
        ),
        _cooccurrence_edges(
            groups['collection_groups'], edge_type='same_collection',
            base_weight=const.EDGE_BASE_WEIGHTS['same_collection'], max_group_size=10_000,
        ),
    )

    finalized: Dict[Tuple[str, str], Dict[str, Any]] = {}
    adjacency: Dict[str, List[Tuple[str, float]]] = defaultdict(list)

    for pair, by_type in contributions.items():
        shared_actor = by_type.get('shared_actor')
        if shared_actor and len(set(shared_actor['keys'])) < const.MIN_SHARED_ACTORS:
            del by_type['shared_actor']
        keyword = by_type.get('keyword_similarity')
        if keyword and keyword['weight'] < const.MIN_KEYWORD_EDGE_WEIGHT:
            del by_type['keyword_similarity']
        studio = by_type.get('same_studio')
        if studio and studio['weight'] < const.MIN_STUDIO_EDGE_WEIGHT:
            del by_type['same_studio']
        if not by_type:
            continue

        total_weight = sum(t['weight'] for t in by_type.values())
        if total_weight <= 0:
            continue

        primary_type = max(by_type.items(), key=lambda kv: kv[1]['weight'])[0]
        reasons = []
        for edge_type, data in by_type.items():
            unique_keys = list(dict.fromkeys(data['keys']))
            text = _reason_text(edge_type, unique_keys, labels.get(edge_type, {}))
            if text:
                reasons.append(text)

        edge = {
            'source': pair[0],
            'target': pair[1],
            'type': primary_type,
            'relationship_types': sorted(by_type.keys()),
            'weight': round(total_weight, 3),
            'reasons': reasons,
        }
        finalized[pair] = edge
        adjacency[pair[0]].append((pair[1], total_weight))
        adjacency[pair[1]].append((pair[0], total_weight))

    # Cap density per node so hubs (e.g. a prolific studio) don't overwhelm the view
    kept_pairs: Set[Tuple[str, str]] = set()
    for node_id, neighbors in adjacency.items():
        neighbors.sort(key=lambda nb: nb[1], reverse=True)
        for neighbor_id, _ in neighbors[: const.MAX_EDGES_PER_NODE]:
            pair = (node_id, neighbor_id) if node_id < neighbor_id else (neighbor_id, node_id)
            kept_pairs.add(pair)

    return [finalized[pair] for pair in kept_pairs if pair in finalized]


def _add_people_overlay(nodes, edges, movie_nodes, directors_by_movie, cast_by_movie):
    """Optional director/actor nodes for display context only - not used for clustering.

    Uncapped, this turns into a dense hairball once a prolific actor/director connects
    to dozens of movies, so new people stop being introduced past MAX_PEOPLE_OVERLAY_NODES
    (movies are processed rating-desc, so the most prominent people win the cap) while
    existing ones still pick up edges to further movies they appear in.
    """
    seen_directors: Dict[int, str] = {}
    seen_actors: Dict[int, str] = {}

    def _people_count():
        return len(seen_directors) + len(seen_actors)

    for movie_id, node_id in movie_nodes.items():
        for director in directors_by_movie.get(movie_id, []):
            d_node = seen_directors.get(director.id)
            if d_node is None:
                if _people_count() >= const.MAX_PEOPLE_OVERLAY_NODES:
                    continue
                d_node = f"director_{director.id}"
                seen_directors[director.id] = d_node
                nodes.append({
                    'id': d_node, 'label': director.name, 'type': 'director', 'size': 16,
                    'profile_picture': director.profile_picture, 'person_id': director.id,
                })
            edges.append({'source': node_id, 'target': d_node, 'type': 'directed_by', 'weight': 1.0})
        for actor in cast_by_movie.get(movie_id, [])[: const.PEOPLE_OVERLAY_TOP_CAST]:
            a_node = seen_actors.get(actor.id)
            if a_node is None:
                if _people_count() >= const.MAX_PEOPLE_OVERLAY_NODES:
                    continue
                a_node = f"actor_{actor.id}"
                seen_actors[actor.id] = a_node
                nodes.append({
                    'id': a_node, 'label': actor.name, 'type': 'actor', 'size': 12,
                    'profile_picture': actor.profile_picture, 'person_id': actor.id,
                })
            edges.append({'source': node_id, 'target': a_node, 'type': 'acted_in', 'weight': 0.6})


def _add_social_layer(nodes, edges, movie_nodes, current_user, *, min_reviews, show_predictions, predictions_limit, max_nodes):
    """Optional overlay: user review + collaborative-filtering prediction edges."""
    movie_content_type = _movie_ct()
    active_users = User.objects.annotate(
        movie_review_count=Count('reviews', filter=Q(reviews__content_type=movie_content_type))
    ).filter(movie_review_count__gte=min_reviews)

    active_users = list(active_users)
    if current_user and current_user not in active_users:
        active_users.append(current_user)

    user_nodes: Dict[int, str] = {}
    for user in active_users[: max(1, max_nodes // 3)]:
        node_id = f"user_{user.id}"
        user_nodes[user.id] = node_id
        review_count = getattr(user, 'movie_review_count', None)
        if review_count is None:
            review_count = Review.objects.filter(user=user, content_type=movie_content_type).count()
        profile_picture = user.get_profile_picture() if hasattr(user, 'get_profile_picture') else None
        nodes.append({
            'id': node_id,
            'label': user.username,
            'type': 'user',
            'size': max(15, min(25, 20 + review_count * 0.3)),
            'review_count': review_count,
            'profile_picture': profile_picture,
        })

    if not user_nodes:
        return

    reviews = Review.objects.filter(
        content_type=movie_content_type, user_id__in=user_nodes.keys(), object_id__in=movie_nodes.keys()
    ).values('user_id', 'object_id', 'rating')
    for review in reviews:
        edges.append({
            'source': user_nodes[review['user_id']],
            'target': movie_nodes[review['object_id']],
            'type': 'review',
            'weight': review['rating'] / 10.0,
            'rating': review['rating'],
        })

    if show_predictions and current_user and current_user.id in user_nodes:
        try:
            rating_matrix = get_user_rating_matrix_optimized(list(user_nodes.keys()))
            item_means = get_item_means_optimized(list(movie_nodes.keys()))
            rated = set(rating_matrix.get(current_user.id, {}).keys())
            unrated = [mid for mid in movie_nodes if mid not in rated]
            if unrated:
                similarity_matrix = get_user_similarity_matrix(
                    rating_matrix, similarity_method='cosine', item_means=item_means
                )
                predictions = get_collaborative_filtering_predictions(
                    user_id=current_user.id, rating_matrix=rating_matrix,
                    similarity_matrix=similarity_matrix, target_movies=unrated, k=10,
                )
                ranked = sorted(predictions.items(), key=lambda kv: kv[1], reverse=True)
                ranked = ranked[:predictions_limit] if predictions_limit > 0 else []
                node_by_id = {n['id']: n for n in nodes}
                for movie_id, score in ranked:
                    target = movie_nodes[movie_id]
                    node_by_id[target]['predicted_score'] = round(score, 2)
                    edges.append({
                        'source': user_nodes[current_user.id],
                        'target': target,
                        'type': 'prediction',
                        'weight': score / 10.0,
                        'predicted_rating': round(score, 2),
                    })
        except Exception:
            logger.exception("Failed to generate collaborative-filtering predictions for social layer")


def build_network_graph_refactored(
    current_user: Optional[User],
    *,
    rating_threshold: float = 0.0,
    movie_limit: int = 0,
    max_nodes: int = 500,
    show_people: bool = False,
    include_social_layer: bool = False,
    min_reviews: int = 2,
    show_predictions: bool = True,
    predictions_limit: int = 10,
    seed_tmdb_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Build a movie-centric network graph.

    Args:
        current_user: User requesting the graph (only used by the social layer)
        rating_threshold: Minimum TMDB rating for seed movies (0 = no filter)
        movie_limit: Max movies to seed the graph with (0 = no limit, every movie)
        max_nodes: Soft cap used to size the optional social layer
        show_people: Include director/actor nodes as a display-only overlay
        include_social_layer: Include user review/prediction edges as an overlay
        min_reviews: Minimum reviews for social-layer user inclusion
        show_predictions: Include collaborative-filtering prediction edges
        predictions_limit: Max predictions to show
        seed_tmdb_ids: Explicit set of TMDB ids to seed with (used for progressive expand)

    Returns:
        Dict with nodes, edges, compound_groups (collections), and stats.
    """
    logger.info("Building movie-centric graph (movie_limit=%s, rating_threshold=%s)", movie_limit, rating_threshold)

    movies = _fetch_seed_movies(
        rating_threshold=rating_threshold, movie_limit=movie_limit, seed_tmdb_ids=seed_tmdb_ids
    )
    if not movies:
        logger.warning("No movies matched the graph seed criteria")
        return {'nodes': [], 'edges': [], 'compound_groups': [], 'stats': {'total_nodes': 0, 'total_edges': 0}}

    movie_stats = get_movie_stats_optimized([m.id for m in movies])
    enrichment = load_movie_enrichment()

    nodes, movie_nodes, compound_groups, groups, labels, directors_by_movie, cast_by_movie = (
        _build_movie_nodes_and_groups(movies, movie_stats, enrichment)
    )
    edges = _build_relationship_edges(groups, labels)

    if show_people:
        _add_people_overlay(nodes, edges, movie_nodes, directors_by_movie, cast_by_movie)

    if include_social_layer and current_user is not None:
        _add_social_layer(
            nodes, edges, movie_nodes, current_user,
            min_reviews=min_reviews, show_predictions=show_predictions,
            predictions_limit=predictions_limit, max_nodes=max_nodes,
        )

    stats = {
        'total_nodes': len(nodes),
        'total_edges': len(edges),
        'movie_count': len(movie_nodes),
        'collection_count': len(compound_groups),
    }

    logger.info(
        "Graph built: %d nodes, %d edges, %d collections",
        stats['total_nodes'], stats['total_edges'], stats['collection_count'],
    )

    return {
        'nodes': nodes,
        'edges': edges,
        'compound_groups': compound_groups,
        'stats': stats,
    }


def build_movie_neighbors(tmdb_id: int, *, limit: int = 20, show_people: bool = False) -> Dict[str, Any]:
    """Return the direct relationship neighbors of a single movie for progressive expand-on-click.

    When ``show_people`` is set, also pulls in that movie's (and its kept neighbors')
    director/cast overlay nodes so expanding stays consistent with the "Show cast/crew" mode.
    """
    try:
        seed_movie = Movie.objects.select_related('collection').get(tmdb_id=tmdb_id)
    except Movie.DoesNotExist:
        return {'nodes': [], 'edges': [], 'compound_groups': []}

    movie_ct = _movie_ct()
    candidate_ids: Set[int] = set()

    director_ids = list(seed_movie.directors.values_list('id', flat=True))
    if director_ids:
        candidate_ids.update(
            MediaPerson.objects.filter(content_type=movie_ct, role='Director', person_id__in=director_ids)
            .values_list('object_id', flat=True)
        )

    top_cast_ids = list(
        MediaPerson.objects.filter(content_type=movie_ct, object_id=seed_movie.id, role='Actor')
        .order_by('order').values_list('person_id', flat=True)[: const.TOP_CAST_BILLING_LIMIT]
    )
    if top_cast_ids:
        candidate_ids.update(
            MediaPerson.objects.filter(
                content_type=movie_ct, role='Actor', person_id__in=top_cast_ids, order__lt=const.TOP_CAST_BILLING_LIMIT
            ).values_list('object_id', flat=True)
        )

    keyword_ids = list(seed_movie.keywords.values_list('id', flat=True))
    if keyword_ids:
        candidate_ids.update(Movie.objects.filter(keywords__id__in=keyword_ids).values_list('id', flat=True))

    if seed_movie.collection_id:
        candidate_ids.update(Movie.objects.filter(collection_id=seed_movie.collection_id).values_list('id', flat=True))

    candidate_ids.discard(seed_movie.id)
    if not candidate_ids:
        return {'nodes': [], 'edges': [], 'compound_groups': []}

    candidates = list(
        Movie.objects.filter(id__in=candidate_ids)
        .order_by('-rating')
        .values_list('tmdb_id', flat=True)[: max(limit * 3, limit)]
    )
    seed_tmdb_ids = [seed_movie.tmdb_id] + list(candidates)

    graph = build_network_graph_refactored(
        None, movie_limit=len(seed_tmdb_ids), rating_threshold=0.0, seed_tmdb_ids=seed_tmdb_ids,
        show_people=show_people,
    )

    seed_node_id = _movie_node_id(seed_movie.id)
    people_edge_types = {'directed_by', 'acted_in'}
    movie_edges = [
        e for e in graph['edges']
        if e['type'] not in people_edge_types and seed_node_id in (e['source'], e['target'])
    ]
    movie_edges.sort(key=lambda e: e['weight'], reverse=True)
    movie_edges = movie_edges[:limit]

    keep_ids = {seed_node_id}
    for edge in movie_edges:
        keep_ids.add(edge['source'])
        keep_ids.add(edge['target'])

    people_edges = []
    if show_people:
        people_edges = [
            e for e in graph['edges']
            if e['type'] in people_edge_types and (e['source'] in keep_ids or e['target'] in keep_ids)
        ]
        for edge in people_edges:
            keep_ids.add(edge['source'])
            keep_ids.add(edge['target'])

    return {
        'nodes': [n for n in graph['nodes'] if n['id'] in keep_ids],
        'edges': movie_edges + people_edges,
        'compound_groups': [g for g in graph['compound_groups'] if any(mid in keep_ids for mid in g['movie_ids'])],
    }


def build_person_neighbors(person_id: int, *, limit: int = 15) -> Dict[str, Any]:
    """Return more of a person's movies for expand-on-click on a director/actor overlay node."""
    movie_ct = _movie_ct()
    movie_ids = list(
        MediaPerson.objects.filter(content_type=movie_ct, person_id=person_id, role__in=['Director', 'Actor'])
        .values_list('object_id', flat=True).distinct()
    )
    if not movie_ids:
        return {'nodes': [], 'edges': [], 'compound_groups': []}

    candidate_tmdb_ids = list(
        Movie.objects.filter(id__in=movie_ids).order_by('-rating').values_list('tmdb_id', flat=True)[:limit]
    )
    if not candidate_tmdb_ids:
        return {'nodes': [], 'edges': [], 'compound_groups': []}

    return build_network_graph_refactored(
        None, movie_limit=len(candidate_tmdb_ids), rating_threshold=0.0,
        seed_tmdb_ids=candidate_tmdb_ids, show_people=True,
    )

