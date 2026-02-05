import numpy as np
import pickle
import os
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Avg
from movies.models import Movie
from custom_auth.models import Review

User = get_user_model()

class MovieRecommender:
    def __init__(self):
        self.movie_content_type = ContentType.objects.get_for_model(Movie)
        self.model_data = None
        self.known_tmdb_ids = set()
        self._load_model()
        
    def _load_model(self):
        """Load the Scipy SVD model and mappings"""
        try:
            model_path = os.path.join(settings.BASE_DIR, 'movies', 'ml_models', 'svd_model.pkl')
            if os.path.exists(model_path):
                with open(model_path, 'rb') as f:
                    self.model_data = pickle.load(f)
                
                if self.model_data:
                    self.known_tmdb_ids = set(self.model_data.get('known_tmdb_ids', []))
                    self.user_to_idx = self.model_data.get('user_to_idx', {})
                    self.item_to_idx = self.model_data.get('item_to_idx', {})
                    self.U = self.model_data.get('U')
                    self.Sigma = self.model_data.get('Sigma')
                    self.Vt = self.model_data.get('Vt')
                    
                    # New Bias Terms
                    self.item_biases = self.model_data.get('item_biases', {})
                    self.user_biases = self.model_data.get('user_biases', {})
                    self.year_biases = self.model_data.get('year_biases', {})
                    self.user_genre_biases = self.model_data.get('user_genre_biases', {})
                    # [NEW] Load Decade Biases
                    self.user_decade_biases = self.model_data.get('user_decade_biases', {})
                    
                    self.tmdb_to_genres = self.model_data.get('tmdb_to_genres', {})
                    self.genre_mapping = self.model_data.get('genre_mapping', {}) # TMDB -> ML mapping
                    
                    self.tmdb_id_to_year = self.model_data.get('tmdb_id_to_year', {})
                    self.global_mean = self.model_data.get('global_mean', 3.5)
        except Exception:
            pass
    
    def predict_rating(self, user_id_str, tmdb_id_int, year=None):
        """Predict rating using SVD + Biases: score = Global + b_i + b_u + b_year + b_genre + Interaction"""
        if not self.model_data:
            return 0
            
        u_idx = self.user_to_idx.get(user_id_str)
        i_idx = self.item_to_idx.get(tmdb_id_int)
        
        # Biases
        b_i = self.item_biases.get(tmdb_id_int, 0.0)
        b_u = self.user_biases.get(user_id_str, 0.0)
        
        # Year & Decade Bias
        b_y = 0.0
        b_dec = 0.0
        
        if year is None:
            year = self.tmdb_id_to_year.get(tmdb_id_int)
        if year is not None:
             b_y = self.year_biases.get(year, 0.0)
             if hasattr(self, 'user_decade_biases') and self.user_decade_biases:
                 decade = (int(year) // 10) * 10
                 b_dec = self.user_decade_biases.get(decade, {}).get(user_id_str, 0.0)

        # Genre Bias
        # Looking up genres for this movie to apply specific user tastes
        b_g = 0.0
        
        # Local lookup first (from tmdb_to_genres loaded map)
        movie_genres = self.tmdb_to_genres.get(tmdb_id_int, [])
        
        # If movie_genres are TMDB names, we must map them to MovieLens names
        # Note: If they came from local DB (via view passing them), we could support that too, 
        # but for now we rely on the static map for consistency.
        
        if movie_genres and self.user_genre_biases:
            valid_count = 0
            total_bias = 0.0
            
            for g_raw in movie_genres:
                # If genre is "Action", map to "Action"
                # If genre is "Science Fiction", map to "Sci-Fi"
                # If genre is "Children", map to "Children's"
                
                # Check mapping first (TMDB -> ML)
                ml_genre = self.genre_mapping.get(g_raw, g_raw) 
                
                if ml_genre and ml_genre in self.user_genre_biases:
                     genre_map = self.user_genre_biases[ml_genre]
                     if user_id_str in genre_map:
                         total_bias += genre_map[user_id_str]
                         valid_count += 1
            
            if valid_count > 0:
                b_g = total_bias / valid_count
        
        interaction = 0.0
        
        if u_idx is not None and i_idx is not None:
            user_vec = self.U[u_idx, :]
            item_vec = self.Vt[:, i_idx]
            interaction = np.dot(np.dot(user_vec, self.Sigma), item_vec)
        
        # Final Score
        score = self.global_mean + b_i + b_u + b_y + b_g + b_dec + interaction
        
        return score

    def _rerank_mmr(self, candidates, max_recommendations, diversity_alpha=0.7):
        """
        Maximal Marginal Relevance (MMR) for Diversity.
        Balances relevance (predicted rating) with dissimilarity (genre distance).
        alpha = 1.0 (Pure Accuracy) vs 0.0 (Pure Diversity). 0.7 is a good scientific baseline.
        """
        if not candidates: return []
            
        # 1. Normalize Ratings (0-1 scale)
        ratings = [c['predicted_rating'] for c in candidates]
        max_r, min_r = max(ratings), min(ratings)
        range_r = max_r - min_r if max_r > min_r else 1.0
        
        # 2. Pre-compute Genres for candidates
        cand_genres = {}
        for c in candidates:
            # Get normalized genres from model map
            raw_gens = self.tmdb_to_genres.get(c['tmdb_id'], [])
            # Map to sets for Jaccard sim
            cand_genres[c['tmdb_id']] = set(self.genre_mapping.get(g, g) for g in raw_gens)

        selected = []
        remaining = sorted(candidates, key=lambda x: x['predicted_rating'], reverse=True)
        
        # 3. Always pick the absolute best match first (Anchor)
        if remaining:
            selected.append(remaining.pop(0))
            
        # 4. Iteratively select next item that maximizes MMR score
        while len(selected) < max_recommendations and remaining:
            best_score = -float('inf')
            best_idx = -1
            
            for i, item in enumerate(remaining):
                # Calculate Similarity to ALREADY SELECTED items
                # We use Max pairwise similarity (avoiding items too similar to what we already have)
                max_sim = 0.0
                i_gens = cand_genres.get(item['tmdb_id'], set())
                
                if i_gens:
                    for sel in selected:
                        s_gens = cand_genres.get(sel['tmdb_id'], set())
                        if not s_gens: continue
                        
                        # Jaccard Similarity: intersection / union
                        inter = len(i_gens.intersection(s_gens))
                        union = len(i_gens.union(s_gens))
                        sim = inter / union if union > 0 else 0
                        if sim > max_sim: max_sim = sim
                
                # MMR Formula: Lambda * Relevance - (1-Lambda) * Similarity
                norm_rating = (item['predicted_rating'] - min_r) / range_r
                mmr_score = (diversity_alpha * norm_rating) - ((1.0 - diversity_alpha) * max_sim)
                
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i
            
            if best_idx != -1:
                selected.append(remaining.pop(best_idx))
            else:
                # Fallback if calculation fails
                selected.append(remaining.pop(0))
                
        return selected

    def get_recommendations_for_user(self, user_id, max_recommendations=10, scope='local'):
        """
        Get movie recommendations for a user.
        """
        if scope == 'external':
            return self._get_external_recommendations(user_id, max_recommendations)
            
        # --- Local Recommendation Logic ---
        
        # Get current user's ratings to exclude watched movies
        user_ratings = Review.objects.filter(
            user_id=user_id,
            content_type=self.movie_content_type
        ).values_list('object_id', flat=True)
        
        user_rated_movie_ids = set(user_ratings)
        
        # Fallback
        if not self.model_data:
            return self._get_popular_movies(max_recommendations, user_rated_movie_ids)

        try:
            # Candidates: Local movies not seen, with Valid TMDB ID
            candidates = Movie.objects.exclude(
                id__in=user_rated_movie_ids
            ).filter(
                tmdb_id__isnull=False
            ).values('id', 'tmdb_id', 'release_date')
            
            predictions = []
            user_id_str = f"loc_{user_id}"
            
            # Pre-check if user is in model
            if user_id_str not in self.user_to_idx:
                return self._get_popular_movies(max_recommendations, user_rated_movie_ids)

            for movie in candidates:
                tmdb_id = movie['tmdb_id']
                release_date = movie['release_date']
                year = release_date.year if release_date else None
                
                est = self.predict_rating(user_id_str, tmdb_id, year=year)
                
                if est == 0:
                    continue

                # If est is 0, it means item might not be in model or orthogonal
                # SVD can output neg values or >5. Clamp and scale.
                # Range 0.5-5.0 -> 1.0-10.0
                est = max(0.5, min(5.0, est))
                predicted_rating = round(est * 2, 1)
                
                predictions.append({
                    'id': movie['id'],
                    'tmdb_id': tmdb_id,
                    'predicted_rating': predicted_rating
                })
            
            # Sort by predicted rating
            predictions.sort(key=lambda x: x['predicted_rating'], reverse=True)
            
            # MMR Re-ranking for Local (Diversity Check)
            pool_size = max_recommendations * 3
            pool = predictions[:pool_size]
            top_predictions = self._rerank_mmr(pool, max_recommendations)
            
            # Fetch predicted movies
            top_movie_ids = [p['id'] for p in top_predictions]
            movies = list(Movie.objects.filter(id__in=top_movie_ids))
            movies_dict = {m.id: m for m in movies}
            
            recommendations = []
            for item in top_predictions:
                m_id = item['id']
                if m_id in movies_dict:
                    movie = movies_dict[m_id]
                    movie.predicted_rating = item['predicted_rating']
                    recommendations.append(movie)
            
            if not recommendations:
                 return self._get_popular_movies(max_recommendations, user_rated_movie_ids)

            return recommendations

        except Exception:
            return self._get_popular_movies(max_recommendations, user_rated_movie_ids)

    def _get_external_recommendations(self, user_id, max_recommendations):
        """Get recommendations for movies not in the local database"""
        if not self.model_data or not self.known_tmdb_ids:
            return []
            
        local_tmdb_ids = set(Movie.objects.exclude(tmdb_id__isnull=True).values_list('tmdb_id', flat=True))
        candidates = self.known_tmdb_ids - local_tmdb_ids
        
        predictions = []
        user_id_str = f"loc_{user_id}"
        
        if user_id_str not in self.user_to_idx:
            return []
            
        for tmdb_id in candidates:
            # Check if item is in model index before predicting call overhead
            if tmdb_id not in self.item_to_idx:
                continue
                
            est = self.predict_rating(user_id_str, tmdb_id)
            est = max(0.5, min(5.0, est))
            
            # Only include decent recommendations
            if est < 3.2: # Lowered to allow diverse candidates
                continue
                
            predictions.append({
                'tmdb_id': int(tmdb_id),
                'predicted_rating': round(est * 2, 1)
            })
                
        predictions.sort(key=lambda x: x['predicted_rating'], reverse=True)
        
        # [NEW]: fetch a larger pool (3x) and then re-rank for diversity
        # This solves the "Filter Bubble" problem by balancing relevance with novelty
        pool_size = max_recommendations * 3
        pool = predictions[:pool_size]
        
        final_list = self._rerank_mmr(pool, max_recommendations)
        
        return final_list
    
    def _get_popular_movies(self, limit=10, exclude_movie_ids=None):
        """Get popular movies based on average ratings as fallback"""
        query = Review.objects.filter(
            content_type=self.movie_content_type
        )
        if exclude_movie_ids:
            query = query.exclude(object_id__in=exclude_movie_ids)
        
        popular_movies = query.values('object_id').annotate(
            avg_rating=Avg('rating'),
            rating_count=Count('id')
        ).filter(
            rating_count__gte=1
        ).order_by('-avg_rating')[:limit]
        
        movie_ids = [m['object_id'] for m in popular_movies]
        return list(Movie.objects.filter(id__in=movie_ids))
