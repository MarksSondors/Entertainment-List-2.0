import numpy as np
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Avg, Q, F, Sum, Prefetch
from games.models import Game, GameGenre, GameDeveloper, GamePublisher
from custom_auth.models import Review
from datetime import date

User = get_user_model()

class GameRecommender:
    def __init__(self):
        self.game_content_type = ContentType.objects.get_for_model(Game)
    
    def get_recommendations_for_user(self, user_id, max_recommendations=10):
        """
        Get game recommendations for a user based on collaborative and content-based filtering
        """
        # Get current user's ratings
        user_ratings = Review.objects.filter(
            user_id=user_id,
            content_type=self.game_content_type
        ).values('object_id', 'rating')
        
        user_ratings_dict = {r['object_id']: r['rating'] for r in user_ratings}
        user_rated_game_ids = set(user_ratings_dict.keys())
        
        if not user_ratings:
            return self._get_popular_games(max_recommendations, user_rated_game_ids)
        
        # Get recommendations using both methods
        cf_recommendations = self._get_collaborative_recommendations(user_id, user_ratings_dict, user_rated_game_ids, max_recommendations*2)
        cb_recommendations = self._get_content_based_recommendations(user_id, user_rated_game_ids, max_recommendations*2)
        
        # Combine recommendations with weights
        final_recommendations = self._combine_recommendations(cf_recommendations, cb_recommendations, cf_weight=0.65, cb_weight=0.35)
        
        return final_recommendations[:max_recommendations]

    def _get_collaborative_recommendations(self, user_id, user_ratings_dict, user_rated_game_ids, max_recommendations):
        """Get recommendations based on similar users' preferences"""
        # Find users who rated at least 2 of the same games
        similar_users = User.objects.filter(
            reviews__content_type=self.game_content_type,
            reviews__object_id__in=user_rated_game_ids
        ).annotate(
            common_ratings=Count('reviews', filter=Q(reviews__object_id__in=user_rated_game_ids))
        ).filter(
            common_ratings__gte=2
        ).exclude(
            id=user_id
        ).values('id', 'common_ratings')
        
        similar_user_ids = [user['id'] for user in similar_users]
        
        if not similar_user_ids:
            return self._get_popular_games(max_recommendations, user_rated_game_ids)
        
        # Get all ratings for similar users in one query
        similar_users_ratings = Review.objects.filter(
            user_id__in=similar_user_ids,
            content_type=self.game_content_type,
            object_id__in=user_rated_game_ids
        ).values('user_id', 'object_id', 'rating')
        
        # Group ratings by user
        users_ratings_dict = {}
        for rating in similar_users_ratings:
            user_id_key = rating['user_id']
            if user_id_key not in users_ratings_dict:
                users_ratings_dict[user_id_key] = {}
            users_ratings_dict[user_id_key][rating['object_id']] = rating['rating']
        
        # Calculate similarity scores for each user
        user_similarities = []
        for sim_user_id in similar_user_ids:
            sim_user_ratings_dict = users_ratings_dict.get(sim_user_id, {})
            common_game_ids = user_rated_game_ids.intersection(set(sim_user_ratings_dict.keys()))
            
            if len(common_game_ids) < 2:
                continue
                
            user_values = [user_ratings_dict[game_id] for game_id in common_game_ids]
            sim_user_values = [sim_user_ratings_dict[game_id] for game_id in common_game_ids]
            
            similarity = self._pearson_correlation(user_values, sim_user_values)
            if not np.isnan(similarity) and similarity > 0:
                user_similarities.append((sim_user_id, similarity))
        
        # Sort by similarity (highest first) and get top 10
        user_similarities.sort(key=lambda x: x[1], reverse=True)
        top_similar_users = [u[0] for u in user_similarities[:10]]
        
        if not top_similar_users:
            return self._get_popular_games(max_recommendations, user_rated_game_ids)
        
        # Get games rated highly by similar users that current user hasn't rated
        similar_user_games = Review.objects.filter(
            user_id__in=top_similar_users,
            content_type=self.game_content_type,
            rating__gte=7.0
        ).exclude(
            object_id__in=user_rated_game_ids
        ).values('object_id').annotate(
            avg_rating=Avg('rating'),
            rating_count=Count('id')
        ).filter(
            rating_count__gte=1
        ).order_by('-avg_rating')[:max_recommendations*2]
        
        # Get the actual game objects
        game_ids = [g['object_id'] for g in similar_user_games]
        recommended_games = list(Game.objects.filter(id__in=game_ids))
        
        # Sort games by their average rating from similar users
        game_ratings = {g['object_id']: g['avg_rating'] for g in similar_user_games}
        recommended_games.sort(key=lambda g: game_ratings.get(g.id, 0), reverse=True)
        
        return [(game, game_ratings.get(game.id, 0)) for game in recommended_games[:max_recommendations]]

    def _get_content_based_recommendations(self, user_id, user_rated_game_ids, max_recommendations):
        """Get recommendations based on game content (genres, developers, publishers)"""
        # Get highly rated games by the user (rating >= 7.0)
        high_rated_games = Review.objects.filter(
            user_id=user_id,
            content_type=self.game_content_type,
            rating__gte=7.0
        ).values_list('object_id', flat=True)
        
        if not high_rated_games:
            return []
        
        # Get the games user liked
        liked_games = Game.objects.filter(id__in=high_rated_games).prefetch_related(
            'genres', 'developers', 'publishers'
        )
        
        # Extract features from liked games
        genre_ids = set()
        developer_ids = set()
        publisher_ids = set()
        collection_ids = set()
        
        for game in liked_games:
            genre_ids.update(genre.id for genre in game.genres.all())
            developer_ids.update(dev.id for dev in game.developers.all())
            publisher_ids.update(pub.id for pub in game.publishers.all())
            
            if game.game_collection_id:
                collection_ids.add(game.game_collection_id)
        
        # Find candidate games
        candidate_games_base = Game.objects.exclude(
            id__in=user_rated_game_ids
        ).filter(
            release_date__lte=date.today()
        )
        
        # If we have genre preferences, prioritize games with those genres
        if genre_ids:
            priority_games = candidate_games_base.filter(
                genres__id__in=genre_ids
            ).distinct()[:max_recommendations * 4]
            
            # Get additional games if we don't have enough
            remaining_needed = max(0, max_recommendations * 6 - len(priority_games))
            if remaining_needed > 0:
                additional_games = candidate_games_base.exclude(
                    id__in=[g.id for g in priority_games]
                )[:remaining_needed]
                candidate_games = list(priority_games) + list(additional_games)
            else:
                candidate_games = list(priority_games)
        else:
            candidate_games = list(candidate_games_base[:max_recommendations * 6])
            
        # Calculate similarity scores
        game_scores = []
        
        # Pre-fetch data for candidate games
        candidate_game_ids = [g.id for g in candidate_games]
        
        # Bulk fetch related data
        candidate_games_with_relations = Game.objects.filter(
            id__in=candidate_game_ids
        ).prefetch_related('genres', 'developers', 'publishers')
        
        for game in candidate_games_with_relations:
            score = 0
            
            # Genre similarity (Jaccard index)
            game_genre_ids = set(g.id for g in game.genres.all())
            if genre_ids and game_genre_ids:
                intersection = len(genre_ids.intersection(game_genre_ids))
                union = len(genre_ids.union(game_genre_ids))
                score += (intersection / union) * 3.0
            
            # Developer similarity
            game_dev_ids = set(d.id for d in game.developers.all())
            if developer_ids and game_dev_ids:
                common_devs = len(developer_ids.intersection(game_dev_ids))
                score += common_devs * 4.0
                
            # Publisher similarity
            game_pub_ids = set(p.id for p in game.publishers.all())
            if publisher_ids and game_pub_ids:
                common_pubs = len(publisher_ids.intersection(game_pub_ids))
                score += common_pubs * 2.0
            
            # Collection bonus
            if game.game_collection_id and game.game_collection_id in collection_ids:
                score += 5.0
            
            # Boost highly rated games
            if game.rating:
                score += game.rating * 0.1
                
            game_scores.append((game, score))
            
        # Sort by score
        game_scores.sort(key=lambda x: x[1], reverse=True)
        
        return game_scores[:max_recommendations]

    def _get_popular_games(self, max_recommendations, user_rated_game_ids):
        """Get popular games as fallback"""
        popular_games = Game.objects.exclude(
            id__in=user_rated_game_ids
        ).order_by('-rating', '-date_added')[:max_recommendations]
        
        return [(game, 0) for game in popular_games]

    def _combine_recommendations(self, cf_recs, cb_recs, cf_weight=0.65, cb_weight=0.35):
        """Combine recommendations from different sources"""
        combined_scores = {}
        
        # Normalize scores
        if cf_recs:
            max_cf_score = max(score for _, score in cf_recs)
            if max_cf_score > 0:
                cf_recs = [(game, score/max_cf_score) for game, score in cf_recs]
                
        if cb_recs:
            max_cb_score = max(score for _, score in cb_recs)
            if max_cb_score > 0:
                cb_recs = [(game, score/max_cb_score) for game, score in cb_recs]
        
        # Add CF scores
        for game, score in cf_recs:
            combined_scores[game] = score * cf_weight
            
        # Add CB scores
        for game, score in cb_recs:
            if game in combined_scores:
                combined_scores[game] += score * cb_weight
            else:
                combined_scores[game] = score * cb_weight
                
        # Convert back to list and sort
        final_recs = [(game, score) for game, score in combined_scores.items()]
        final_recs.sort(key=lambda x: x[1], reverse=True)
        
        return final_recs

    def _pearson_correlation(self, user1_ratings, user2_ratings):
        """Calculate Pearson correlation coefficient between two users"""
        if len(user1_ratings) < 2:
            return 0
            
        return np.corrcoef(user1_ratings, user2_ratings)[0, 1]
