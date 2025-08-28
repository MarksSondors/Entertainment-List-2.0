"""Graph building service functions extracted from views.

Provides two main helpers:
  - build_movie_analytics_graph_context(): returns context dict for the analytics template
  - build_network_graph(...): returns dict with nodes, edges, stats for API JSON

The heavy computation is moved here to slim down view functions and allow
future reuse / unit testing.
"""

from collections import defaultdict
from itertools import groupby
from django.db.models import Avg, Count, Q
from django.contrib.contenttypes.models import ContentType

from custom_auth.models import CustomUser, Review
from ..models import Movie, Genre, Country  # type: ignore

# Cache the ContentType lookup (expensive if repeated) at import time
_MOVIE_CT = None
def _movie_ct():  # lazy to avoid issues during migrations
    global _MOVIE_CT
    if _MOVIE_CT is None:
        _MOVIE_CT = ContentType.objects.get_for_model(Movie)
    return _MOVIE_CT


def build_movie_analytics_graph_context():
    """Replicates the prior logic inside movie_analytics_graph view.

    Returns:
        dict: template context with graph_data, stats, country_stats (top 10)
    """
    movie_ct = _movie_ct()

    # Prefetch related objects needed for building graph
    movies = (
        Movie.objects.select_related()
        .prefetch_related('countries', 'genres', 'production_companies')
        .all()
    )

    # Materialize reviews once (avoid per-movie .filter calls) and group in-memory
    reviews_qs = (
        Review.objects.select_related('user')
        .filter(content_type=movie_ct)
        .only('id', 'object_id', 'rating', 'review_text', 'date_added', 'user__id', 'user__username')
    )
    reviews_list = list(reviews_qs)

    # Users that actually reviewed movies (distinct over review.user_id)
    user_ids = sorted({r.user_id for r in reviews_list})
    users = CustomUser.objects.filter(id__in=user_ids)

    # Build mapping movie_id -> list of reviews (already in memory)
    reviews_by_movie = defaultdict(list)
    for rv in reviews_list:
        reviews_by_movie[rv.object_id].append(rv)
    # Build mapping user_id -> review count (movie specific)
    user_review_counts = defaultdict(int)
    for rv in reviews_list:
        user_review_counts[rv.user_id] += 1

    nodes = []
    edges = []
    node_id_counter = 0
    node_mapping = {}

    # Users
    for user in users:  # all users already constrained to those with movie reviews
        user_id = f"user_{user.id}"
        node_mapping[user_id] = node_id_counter
        nodes.append({
            'id': node_id_counter,
            'label': user.username,
            'type': 'user',
            'group': 'users',
            'size': 20,
            'color': '#FF6B6B',
            'metadata': {
                'username': user.username,
                'review_count': user_review_counts.get(user.id, 0)
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
        nodes.append({
            'id': node_id_counter,
            'label': movie.title,
            'type': 'movie',
            'group': 'movies',
            'size': max(15, min(30, len(movie_reviews) * 3)),
            'color': '#4ECDC4',
            'metadata': {
                'title': movie.title,
                'release_date': movie.release_date.strftime('%Y') if movie.release_date else 'Unknown',
                'rating': round(avg_rating, 1),
                'review_count': len(movie_reviews),
                'runtime': movie.runtime,
                'countries': [c.name for c in movie.countries.all()],
                'genres': [g.name for g in movie.genres.all()]
            }
        })
        node_id_counter += 1

    # Countries
    countries = Country.objects.filter(movie__in=movies).distinct()
    for country in countries:
        cid = f"country_{country.id}"
        node_mapping[cid] = node_id_counter
        country_movies = movies.filter(countries=country)
        nodes.append({
            'id': node_id_counter,
            'label': country.name,
            'type': 'country',
            'group': 'countries',
            'size': max(10, min(25, len(country_movies) * 2)),
            'color': '#95E1D3',
            'metadata': {
                'name': country.name,
                'iso_code': country.iso_3166_1,
                'movie_count': len(country_movies)
            }
        })
        node_id_counter += 1

    # Genres
    genres = Genre.objects.filter(movie__in=movies).distinct()
    for genre in genres:
        gid = f"genre_{genre.id}"
        node_mapping[gid] = node_id_counter
        genre_movies = movies.filter(genres=genre)
        nodes.append({
            'id': node_id_counter,
            'label': genre.name,
            'type': 'genre',
            'group': 'genres',
            'size': max(10, min(25, len(genre_movies) * 1.5)),
            'color': '#F38BA8',
            'metadata': {
                'name': genre.name,
                'movie_count': len(genre_movies)
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

    # Movie -> country
    for movie in movies:
        mid = f"movie_{movie.id}"
        for country in movie.countries.all():
            cid = f"country_{country.id}"
            if mid in node_mapping and cid in node_mapping:
                edges.append({
                    'source': node_mapping[mid],
                    'target': node_mapping[cid],
                    'type': 'origin',
                    'weight': 0.5,
                    'color': '#BDC3C7'
                })

    # Movie -> genre
    for movie in movies:
        mid = f"movie_{movie.id}"
        for genre in movie.genres.all():
            gid = f"genre_{genre.id}"
            if mid in node_mapping and gid in node_mapping:
                edges.append({
                    'source': node_mapping[mid],
                    'target': node_mapping[gid],
                    'type': 'genre',
                    'weight': 0.3,
                    'color': '#9B59B6'
                })

    # User similarity edges
    user_ratings = defaultdict(dict)
    for review in reviews:
        user_ratings[review.user.id][review.object_id] = review.rating

    user_list = list(user_ratings.keys())
    for i, u1 in enumerate(user_list):
        for u2 in user_list[i+1:]:
            common = set(user_ratings[u1]) & set(user_ratings[u2])
            if len(common) >= 3:
                diffs = [abs(user_ratings[u1][m] - user_ratings[u2][m]) for m in common]
                avg_diff = sum(diffs) / len(diffs)
                similarity = 1 - (avg_diff / 10)
                if similarity > 0.7:
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
                                'common_movies': len(common)
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

    country_stats = []
    for country in countries:
        country_movies = movies.filter(countries=country)
        # Collect reviews belonging to movies from this country via pre-grouped mapping
        relevant_reviews = []
        for mv in country_movies:
            relevant_reviews.extend(reviews_by_movie.get(mv.id, []))
        if relevant_reviews:
            avg_rating = sum(r.rating for r in relevant_reviews) / len(relevant_reviews)
        else:
            avg_rating = 0
        country_stats.append({
            'name': country.name,
            'movie_count': len(country_movies),
            'avg_rating': round(avg_rating, 2)
        })
    country_stats.sort(key=lambda x: x['movie_count'], reverse=True)

    return {
        'graph_data': {
            'nodes': nodes,
            'edges': edges
        },
        'stats': stats,
        'country_stats': country_stats[:10]
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
    show_crew: bool,
):
    """Full network graph computation (parity with original view)."""
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
    all_movie_reviews = Review.objects.filter(content_type=movie_content_type).values('object_id', 'rating', 'user_id')

    # Aggregate movie stats in one DB round trip
    aggregated = (
        Review.objects.filter(content_type=movie_content_type, object_id__in=well_reviewed_movie_ids)
        .values('object_id')
        .annotate(avg_rating=Avg('rating'), review_count=Count('id'))
    )
    movie_stats = {a['object_id']: {'avg_rating': a['avg_rating'], 'review_count': a['review_count']} for a in aggregated}
    well_reviewed_movies = list(
        Movie.objects.filter(id__in=movie_stats.keys())
        .prefetch_related('genres', 'countries', 'keywords')
    )[:movie_limit]
    if not well_reviewed_movies:
        # Fallback: sample movies referenced in any review (limit) with aggregated stats
        fallback_movie_cap = min(movie_limit, max_nodes // 2)
        fallback_ids = list({r['object_id'] for r in all_movie_reviews})[:fallback_movie_cap]
        aggregated_fb = (
            Review.objects.filter(content_type=movie_content_type, object_id__in=fallback_ids)
            .values('object_id')
            .annotate(avg_rating=Avg('rating'), review_count=Count('id'))
        )
        movie_stats.update({a['object_id']: {'avg_rating': a['avg_rating'], 'review_count': a['review_count']} for a in aggregated_fb})
        well_reviewed_movies = list(
            Movie.objects.filter(id__in=[a['object_id'] for a in aggregated_fb])
            .prefetch_related('genres', 'countries', 'keywords')
        )

    # User nodes
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
        nodes.append({
            'id': node_id,
            'label': user.username,
            'type': 'user',
            'size': min(30, user_movie_reviews * 2),
            'color': '#4299E1',
            'review_count': user_movie_reviews
        })

    # Movie nodes
    movie_nodes = {}
    for movie in well_reviewed_movies:
        node_id = f"movie_{movie.id}"
        movie_nodes[movie.id] = node_id
        node_ids.add(node_id)
        stats_m = movie_stats[movie.id]
        nodes.append({
            'id': node_id,
            'label': movie.title,
            'type': 'movie',
            'size': min(25, stats_m['review_count'] * 3),
            'color': '#F56565',
            'rating': round(stats_m['avg_rating'], 1),
            'year': movie.release_date.year if movie.release_date else None,
            'tmdb_id': movie.tmdb_id
        })

    # Country nodes
    country_nodes = {}
    if show_countries:
        country_counts = defaultdict(int)
        for movie in well_reviewed_movies:
            for country in movie.countries.all():
                country_counts[country] += 1
        popular_countries = {c: ct for c, ct in country_counts.items() if ct >= 2}
        for country, count in popular_countries.items():
            node_id = f"country_{country.id}"
            country_nodes[country.id] = node_id
            node_ids.add(node_id)
            nodes.append({
                'id': node_id,
                'label': country.name,
                'type': 'country',
                'size': min(20, count * 2),
                'color': '#48BB78',
                'movie_count': count,
                'mass': 8 + count * 0.6
            })

    # Genre nodes
    genre_nodes = {}
    if show_genres:
        genre_counts = defaultdict(int)
        for movie in well_reviewed_movies:
            for genre in movie.genres.all():
                genre_counts[genre] += 1
        popular_genres = {g: ct for g, ct in genre_counts.items() if ct >= 3}
        for genre, count in popular_genres.items():
            node_id = f"genre_{genre.id}"
            genre_nodes[genre.id] = node_id
            node_ids.add(node_id)
            nodes.append({
                'id': node_id,
                'label': genre.name,
                'type': 'genre',
                'size': min(18, count * 1.5),
                'color': '#9F7AEA',
                'movie_count': count,
                'mass': 10 + count * 0.5
            })

    # Director nodes
    director_nodes = {}
    director_counts = defaultdict(int)
    if show_directors:
        for movie in well_reviewed_movies:
            for director in movie.directors:
                director_counts[director] += 1
        popular_directors = {d: ct for d, ct in director_counts.items() if ct >= 2}
        for director, count in popular_directors.items():
            node_id = f"director_{director.id}"
            director_nodes[director.id] = node_id
            node_ids.add(node_id)
            nodes.append({
                'id': node_id,
                'label': director.name,
                'type': 'director',
                'size': min(16, count * 2),
                'color': '#ED8936',
                'movie_count': count
            })

    # Chaos mode actors/crew
    actor_nodes = {}
    crew_nodes = {}
    actor_counts = defaultdict(int)
    crew_counts = defaultdict(int)
    if chaos_mode and (show_actors or show_crew):
        for movie in well_reviewed_movies:
            for mp in movie.cast[:8]:
                person = getattr(mp, 'person', None)
                if person:
                    actor_counts[person] += 1
            for mp in [mp for mp in movie.crew[:8] if getattr(mp, 'role', '') != 'Director']:
                person = getattr(mp, 'person', None)
                if person:
                    crew_counts[person] += 1
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
                nodes.append({
                    'id': node_id,
                    'label': person.name,
                    'type': 'actor',
                    'size': min(18, 6 + count * 2),
                    'color': '#FFD700',
                    'movie_count': count
                })
        if show_crew:
            for person, count in sorted_crew:
                if person.id in director_nodes or person.id in actor_nodes or person.id in crew_nodes:
                    continue
                node_id = f"crew_{person.id}"
                crew_nodes[person.id] = node_id
                nodes.append({
                    'id': node_id,
                    'label': person.name,
                    'type': 'crew',
                    'size': min(16, 6 + count * 1.5),
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
                    'from': user_nodes[uid],
                    'to': movie_nodes[movie_id],
                    'type': 'review',
                    'weight': rating_val / 10.0,
                    'color': {'color': '#4299E1', 'opacity': 0.6},
                    'width': max(1, rating_val / 2),
                    'label': f"{rating_val}/10",
                    'length': dynamic_length
                })

    # Movie -> Country
    if show_countries:
        for movie in well_reviewed_movies:
            if movie.id not in movie_nodes:
                continue
            for country in movie.countries.all():
                if country.id in country_nodes:
                    edges.append({
                        'from': movie_nodes[movie.id],
                        'to': country_nodes[country.id],
                        'type': 'origin',
                        'color': {'color': '#48BB78', 'opacity': 0.4},
                        'width': 2,
                        'length': 110
                    })

    # Movie -> Genre
    if show_genres:
        for movie in well_reviewed_movies:
            if movie.id not in movie_nodes:
                continue
            for genre in movie.genres.all():
                if genre.id in genre_nodes:
                    edges.append({
                        'from': movie_nodes[movie.id],
                        'to': genre_nodes[genre.id],
                        'type': 'genre',
                        'color': {'color': '#9F7AEA', 'opacity': 0.4},
                        'width': 1.5,
                        'length': 85
                    })

    # Movie -> Director
    if show_directors:
        for movie in well_reviewed_movies:
            if movie.id not in movie_nodes:
                continue
            for director in movie.directors:
                if director.id in director_nodes:
                    edges.append({
                        'from': movie_nodes[movie.id],
                        'to': director_nodes[director.id],
                        'type': 'directed_by',
                        'color': {'color': '#ED8936', 'opacity': 0.5},
                        'width': 2
                    })

    # Chaos Movie -> Actor / Crew
    if chaos_mode:
        if show_actors:
            for movie in well_reviewed_movies:
                if movie.id not in movie_nodes:
                    continue
                for mp in movie.cast[:8]:
                    person = getattr(mp, 'person', None)
                    if not person or person.id not in actor_nodes:
                        continue
                    edges.append({
                        'from': movie_nodes[movie.id],
                        'to': actor_nodes[person.id],
                        'type': 'acted_in',
                        'color': {'color': '#FFD700', 'opacity': 0.35},
                        'width': 1
                    })
        if show_crew:
            for movie in well_reviewed_movies:
                if movie.id not in movie_nodes:
                    continue
                for mp in movie.crew[:8]:
                    if getattr(mp, 'role', '') == 'Director':
                        continue
                    person = getattr(mp, 'person', None)
                    if not person or person.id not in crew_nodes:
                        continue
                    edges.append({
                        'from': movie_nodes[movie.id],
                        'to': crew_nodes[person.id],
                        'type': 'crew',
                        'color': {'color': '#00CED1', 'opacity': 0.35},
                        'width': 1
                    })

    # Affinity edges for people to genre/country
    if (show_genres or show_countries) and (genre_nodes or country_nodes):
        def _add_affinity_edges(node_map, person_type, max_genres=3, max_countries=2):
            for person_id, node_id in node_map.items():
                genre_counter = defaultdict(int)
                country_counter = defaultdict(int)
                total_movies = 0
                for mv in well_reviewed_movies:
                    involved = False
                    if person_type == 'actor':
                        for mp in mv.cast[:8]:
                            p = getattr(mp, 'person', None)
                            if p and p.id == person_id:
                                involved = True
                                break
                    elif person_type == 'director':
                        for d in mv.directors:
                            if d.id == person_id:
                                involved = True
                                break
                    if not involved:
                        continue
                    total_movies += 1
                    if show_genres:
                        for g in mv.genres.all():
                            if g.id in genre_nodes:
                                genre_counter[g.id] += 1
                    if show_countries:
                        for c in mv.countries.all():
                            if c.id in country_nodes:
                                country_counter[c.id] += 1
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
                            'from': node_id,
                            'to': genre_nodes[gid],
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
                            'from': node_id,
                            'to': country_nodes[cid],
                            'type': f'{person_type}_country_affinity',
                            'color': {'color': '#68D391' if person_type=='actor' else '#ED8936', 'opacity': 0.30},
                            'width': 0.8 + strength * 2.0,
                            'dashes': True,
                            'length': int(280 - strength * 150),
                            'label': f"{int(strength*100)}%"
                        })
        if show_actors and actor_nodes:
            _add_affinity_edges(actor_nodes, 'actor')
        if show_directors and director_nodes:
            _add_affinity_edges(director_nodes, 'director')

    # User similarity & preferences
    # Build user -> liked movie set from grouped reviews (fast, no extra queries)
    user_movie_preferences = {uid: {r['object_id'] for r in rs} for uid, rs in good_reviews_by_user.items() if uid in user_nodes}

    user_list = list(user_movie_preferences.keys())
    current_user_similarities = {}
    if show_similarity or show_predictions:
        for i, user1_id in enumerate(user_list):
            for user2_id in user_list[i+1:]:
                if user1_id in user_nodes and user2_id in user_nodes:
                    common_movies = user_movie_preferences[user1_id] & user_movie_preferences[user2_id]
                    if len(common_movies) >= 1:
                        similarity = len(common_movies) / max(len(user_movie_preferences[user1_id]), len(user_movie_preferences[user2_id]))
                        if similarity >= 0.2:
                            if show_similarity:
                                edges.append({
                                    'from': user_nodes[user1_id],
                                    'to': user_nodes[user2_id],
                                    'type': 'similarity',
                                    'weight': similarity,
                                    'color': {'color': '#38B2AC', 'opacity': 0.5},
                                    'width': max(1, similarity * 4),
                                    'label': f"{int(similarity*100)}% similar",
                                    'dashes': True
                                })
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

    # User -> genre / country affinity edges
    if show_genres or show_countries:
        movie_lookup = {m.id: m for m in well_reviewed_movies}
        for uid, liked_ids in user_movie_preferences.items():
            if uid not in user_nodes or not liked_ids:
                continue
            genre_counter = defaultdict(int)
            country_counter = defaultdict(int)
            for mid in liked_ids:
                mv = movie_lookup.get(mid)
                if not mv:
                    continue
                if show_genres:
                    for g in mv.genres.all():
                        if g.id in genre_nodes:
                            genre_counter[g.id] += 1
                if show_countries:
                    for c in mv.countries.all():
                        if c.id in country_nodes:
                            country_counter[c.id] += 1
            if genre_counter and show_genres:
                max_g = max(genre_counter.values()) or 1
                for gid, cnt in genre_counter.items():
                    strength = cnt / max_g
                    edges.append({
                        'from': user_nodes[uid],
                        'to': genre_nodes[gid],
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
                        'from': user_nodes[uid],
                        'to': country_nodes[cid],
                        'type': 'user_country_affinity',
                        'color': {'color': '#2F855A', 'opacity': 0.35},
                        'width': 1 + strength * 2.5,
                        'dashes': True,
                        'length': int(260 - strength * 130),
                        'label': f"{int(strength*100)}%"
                    })

    # Predictions
    if show_predictions and current_user and current_user.id in user_nodes:
        user_reviews_qs = Review.objects.filter(user=current_user, content_type=movie_content_type)
        rated_ids_for_affinity = list(user_reviews_qs.values_list('object_id', flat=True))
        user_review_movies = {m.id: m for m in Movie.objects.filter(id__in=rated_ids_for_affinity).prefetch_related('genres','countries','keywords')}
        genre_pref = defaultdict(lambda: {'sum':0.0,'count':0})
        country_pref = defaultdict(lambda: {'sum':0.0,'count':0})
        director_pref = defaultdict(lambda: {'sum':0.0,'count':0})
        keyword_pref = defaultdict(lambda: {'sum':0.0,'count':0})
        for r in user_reviews_qs:
            mv = user_review_movies.get(r.object_id)
            if not mv:
                continue
            for g in mv.genres.all():
                genre_pref[g.id]['sum'] += r.rating
                genre_pref[g.id]['count'] += 1
            for c in mv.countries.all():
                country_pref[c.id]['sum'] += r.rating
                country_pref[c.id]['count'] += 1
            for d in mv.directors:
                director_pref[d.id]['sum'] += r.rating
                director_pref[d.id]['count'] += 1
            for kw in mv.keywords.all():
                keyword_pref[kw.id]['sum'] += r.rating
                keyword_pref[kw.id]['count'] += 1
        genre_affinity_map = {gid: (vals['sum']/vals['count'])/10.0 for gid, vals in genre_pref.items() if vals['count']>0}
        country_affinity_map = {cid: (vals['sum']/vals['count'])/10.0 for cid, vals in country_pref.items() if vals['count']>0}
        director_affinity_map = {did: (vals['sum']/vals['count'])/10.0 for did, vals in director_pref.items() if vals['count']>0}
        keyword_affinity_map = {kid: (vals['sum']/vals['count'])/10.0 for kid, vals in keyword_pref.items() if vals['count']>0}
        rated_movie_ids = set(Review.objects.filter(
            user=current_user,
            content_type=movie_content_type
        ).values_list('object_id', flat=True))
        predicted_scores = []
        other_user_reviews = Review.objects.filter(
            content_type=movie_content_type,
            user__in=[u for u in active_users if u != current_user]
        ).values('user_id', 'object_id', 'rating')
        user_rating_sums = defaultdict(float)
        user_rating_counts = defaultdict(int)
        for r in other_user_reviews:
            user_rating_sums[r['user_id']] += r['rating']
            user_rating_counts[r['user_id']] += 1
        user_mean_rating = {uid: user_rating_sums[uid]/user_rating_counts[uid] for uid in user_rating_sums if user_rating_counts[uid] > 0}
        all_ratings_qs = Review.objects.filter(content_type=movie_content_type).values_list('rating', flat=True)
        global_mean = sum(all_ratings_qs)/len(all_ratings_qs) if all_ratings_qs else 6.0
        movie_ratings_map = defaultdict(list)
        for r in other_user_reviews:
            if r['user_id'] in current_user_similarities and current_user_similarities[r['user_id']] > 0:
                movie_ratings_map[r['object_id']].append((r['user_id'], r['rating']))
        current_user_all_ratings = list(Review.objects.filter(user=current_user, content_type=movie_content_type).values_list('rating', flat=True))
        current_user_mean = sum(current_user_all_ratings)/len(current_user_all_ratings) if current_user_all_ratings else 6.0
        for movie in well_reviewed_movies:
            if movie.id in rated_movie_ids or movie.id not in movie_nodes:
                continue
            ratings = movie_ratings_map.get(movie.id, [])
            g_aff_list = [genre_affinity_map.get(g.id, 0) for g in movie.genres.all()]
            c_aff_list = [country_affinity_map.get(c.id, 0) for c in movie.countries.all()]
            d_aff_list = [director_affinity_map.get(d.id, 0) for d in movie.directors]
            kw_aff_list = []
            matched_keyword_names = []
            for kw in movie.keywords.all():
                if kw.id in keyword_affinity_map:
                    kw_aff_list.append(keyword_affinity_map[kw.id])
                    if len(matched_keyword_names) < 5:
                        matched_keyword_names.append(kw.name)
            movie_genre_affinity = sum(g_aff_list)/len(g_aff_list) if g_aff_list else 0.0
            movie_country_affinity = sum(c_aff_list)/len(c_aff_list) if c_aff_list else 0.0
            movie_director_affinity = sum(d_aff_list)/len(d_aff_list) if d_aff_list else 0.0
            movie_keyword_affinity = sum(kw_aff_list)/len(kw_aff_list) if kw_aff_list else 0.0
            if ratings:
                residual_sum = 0.0
                weight_sum = 0.0
                sq_weighted_residual_sum = 0.0
                contributors = 0
                for uid, rating in ratings:
                    sim = current_user_similarities.get(uid, 0.0)
                    if sim <= 0:
                        continue
                    mean_u = user_mean_rating.get(uid)
                    if mean_u is None:
                        continue
                    affinity_factor = 1 + 0.15*movie_genre_affinity + 0.10*movie_country_affinity + 0.12*movie_director_affinity + 0.08*movie_keyword_affinity
                    weight = sim * affinity_factor
                    movie_avg_local = movie_stats[movie.id]['avg_rating'] if movie.id in movie_stats else global_mean
                    baseline_um = mean_u + movie_avg_local - global_mean
                    residual = rating - baseline_um
                    if residual > 2.0: residual = 2.0
                    elif residual < -2.0: residual = -2.0
                    residual_sum += weight * residual
                    weight_sum += weight
                    sq_weighted_residual_sum += (weight * residual) ** 2
                    contributors += 1
                if contributors > 0 and weight_sum > 0:
                    movie_avg = movie_stats[movie.id]['avg_rating'] if movie.id in movie_stats else global_mean
                    movie_count = movie_stats[movie.id].get('review_count', 0) if movie.id in movie_stats else 0
                    m = 5
                    bayes_movie_avg = ((movie_count * movie_avg) + (m * global_mean)) / (movie_count + m) if movie_count + m > 0 else global_mean
                    baseline = (current_user_mean + bayes_movie_avg - global_mean)
                    if contributors < 3:
                        baseline = 0.7 * bayes_movie_avg + 0.3 * current_user_mean
                    shrink = 1.0 + (2.0 / contributors)
                    adjusted_residual = (residual_sum / weight_sum) / shrink
                    cf_pred = baseline + adjusted_residual
                    cf_pred = max(1.0, min(10.0, cf_pred))
                    std_err = (sq_weighted_residual_sum ** 0.5) / weight_sum if weight_sum > 0 else 0.0
                    if contributors >= 8 and std_err < 0.35:
                        confidence = 'high'
                    elif contributors >= 4 and std_err < 0.55:
                        confidence = 'med'
                    else:
                        confidence = 'low'
                    predicted_scores.append((movie.id, cf_pred, movie_genre_affinity, movie_country_affinity, movie_director_affinity, movie_keyword_affinity, contributors, confidence, round(std_err,3), matched_keyword_names))

        # Content vectors + blending
        import math as _math
        total_movies_for_features = len(well_reviewed_movies) or 1
        genre_freq = defaultdict(int)
        country_freq = defaultdict(int)
        director_freq = defaultdict(int)
        keyword_freq = defaultdict(int)
        for mv in well_reviewed_movies:
            for g in mv.genres.all():
                genre_freq[g.id] += 1
            for c in mv.countries.all():
                country_freq[c.id] += 1
            for d in mv.directors:
                director_freq[d.id] += 1
            for kw in mv.keywords.all():
                keyword_freq[kw.id] += 1
        def _idf(freq):
            return _math.log((1 + total_movies_for_features) / (1 + freq)) + 1.0
        movie_vectors = {}
        for mv in well_reviewed_movies:
            vec = {}
            for g in mv.genres.all():
                vec[f"g{g.id}"] = _idf(genre_freq[g.id])
            for c in mv.countries.all():
                vec[f"c{c.id}"] = _idf(country_freq[c.id])
            for d in mv.directors:
                vec[f"d{d.id}"] = _idf(director_freq[d.id])
            for kw in mv.keywords.all():
                vec[f"k{kw.id}"] = _idf(keyword_freq[kw.id])
            norm = _math.sqrt(sum(v*v for v in vec.values())) or 1.0
            for k in list(vec.keys()):
                vec[k] /= norm
            movie_vectors[mv.id] = vec
        user_vec = {}
        if current_user_all_ratings := list(Review.objects.filter(user=current_user, content_type=movie_content_type).values_list('rating', flat=True)):
            mean_r = sum(current_user_all_ratings)/len(current_user_all_ratings)
            var = sum((r - mean_r)**2 for r in current_user_all_ratings) / max(1, len(current_user_all_ratings)-1)
            user_rating_std = _math.sqrt(var) if var > 0 else 1.0
        else:
            user_rating_std = 1.0
        rated_movies_qs = Review.objects.filter(user=current_user, content_type=movie_content_type).values('object_id','rating')
        current_user_mean = sum(current_user_all_ratings)/len(current_user_all_ratings) if current_user_all_ratings else 6.0
        for r in rated_movies_qs:
            mv_id = r['object_id']
            mv_vec = movie_vectors.get(mv_id)
            if not mv_vec:
                continue
            delta = r['rating'] - current_user_mean
            weight = 1.0 + (delta / 2.5)
            if weight < 0.2:
                weight = 0.2
            for k, v in mv_vec.items():
                user_vec[k] = user_vec.get(k, 0.0) + weight * v
        if user_vec:
            norm_u = _math.sqrt(sum(v*v for v in user_vec.values())) or 1.0
            for k in list(user_vec.keys()):
                user_vec[k] /= norm_u
        def _content_sim(mv_id):
            if not user_vec:
                return 0.0
            mv_vec = movie_vectors.get(mv_id)
            if not mv_vec:
                return 0.0
            if len(user_vec) < len(mv_vec):
                return sum(user_vec.get(k,0.0)*mv_vec.get(k,0.0) for k in user_vec.keys())
            return sum(user_vec.get(k,0.0)*mv_vec.get(k,0.0) for k in mv_vec.keys())
        cf_map = {p[0]: p for p in predicted_scores}
        content_sim_map = {}
        blend_w_cf_map = {}
        updated_predictions = []
        for mv in well_reviewed_movies:
            if mv.id in rated_movie_ids:
                continue
            mv_vec = movie_vectors.get(mv.id)
            if not mv_vec:
                continue
            sim = _content_sim(mv.id)
            content_score = current_user_mean + sim * user_rating_std
            content_score = max(1.0, min(10.0, content_score))
            if mv.id in cf_map:
                _, cf_pred, g_aff, c_aff, d_aff, kw_aff, contributors, confidence, std_err, kw_names = cf_map[mv.id]
                movie_count = movie_stats[mv.id].get('review_count', 0) if mv.id in movie_stats else 0
                evidence_user = contributors / (contributors + 2.0) if contributors > 0 else 0.0
                evidence_item = movie_count / (movie_count + 5.0) if movie_count > 0 else 0.0
                w_cf = min(1.0, max(0.0, 0.6*evidence_user + 0.4*evidence_item))
                blended = w_cf * cf_pred + (1 - w_cf) * content_score
                content_sim_map[mv.id] = round(sim,4)
                blend_w_cf_map[mv.id] = round(w_cf,3)
                updated_predictions.append((mv.id, blended, g_aff, c_aff, d_aff, kw_aff, contributors, confidence, std_err, kw_names))
            else:
                g_aff_list = [genre_affinity_map.get(g.id, 0) for g in mv.genres.all()]
                c_aff_list = [country_affinity_map.get(c.id, 0) for c in mv.countries.all()]
                d_aff_list = [director_affinity_map.get(d.id, 0) for d in mv.directors]
                kw_aff_list = [keyword_affinity_map.get(k.id, 0) for k in mv.keywords.all()]
                g_aff = sum(g_aff_list)/len(g_aff_list) if g_aff_list else 0.0
                c_aff = sum(c_aff_list)/len(c_aff_list) if c_aff_list else 0.0
                d_aff = sum(d_aff_list)/len(d_aff_list) if d_aff_list else 0.0
                kw_aff = sum(kw_aff_list)/len(kw_aff_list) if kw_aff_list else 0.0
                content_sim_map[mv.id] = round(sim,4)
                blend_w_cf_map[mv.id] = 0.0
                updated_predictions.append((mv.id, content_score, g_aff, c_aff, d_aff, kw_aff, 0, 'low', 0.0, []))
        predicted_scores = updated_predictions
        predicted_scores.sort(key=lambda x: x[1], reverse=True)
        top_predictions = predicted_scores[:predictions_limit] if predictions_limit else []
        for movie_id, pred, g_aff, c_aff, d_aff, kw_aff, contributors, confidence, std_err, kw_names in top_predictions:
            for node in nodes:
                if node['id'] == movie_nodes[movie_id]:
                    node['predicted_score'] = round(pred, 2)
                    node['genre_affinity'] = round(g_aff, 3)
                    node['country_affinity'] = round(c_aff, 3)
                    node['director_affinity'] = round(d_aff, 3)
                    node['keyword_affinity'] = round(kw_aff, 3)
                    node['predicted_contributors'] = contributors
                    node['predicted_confidence'] = confidence
                    node['predicted_std_err'] = std_err
                    cs = content_sim_map.get(movie_id)
                    if cs is not None:
                        node['content_similarity'] = cs
                    bw = blend_w_cf_map.get(movie_id)
                    if bw is not None:
                        node['blend_w_cf'] = bw
                    if kw_names:
                        node['matched_keywords'] = kw_names
                    break
            length = max(120, 400 - int(pred * 25))
            edges.append({
                'from': user_nodes[current_user.id],
                'to': movie_nodes[movie_id],
                'type': 'prediction',
                'color': {'color': '#FFD700', 'opacity': 0.6},
                'width': max(1, pred / 3),
                'label': f"Pred {pred:.1f}",
                'length': length
            })

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

    return {'nodes': nodes, 'edges': edges, 'stats': stats}


__all__ = [
    'build_movie_analytics_graph_context',
    'build_network_graph'
]
