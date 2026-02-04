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
                    self.user_means = self.model_data.get('user_means', {})
                    self.global_mean = self.model_data.get('global_mean', 3.5)
        except Exception:
            pass
    
    def predict_rating(self, user_id_str, tmdb_id_int):
        """Predict rating using SVD matrices + Mean: score = Mean + U[user] . Sigma . Vt[:, item]"""
        if not self.model_data:
            return 0
            
        u_idx = self.user_to_idx.get(user_id_str)
        i_idx = self.item_to_idx.get(tmdb_id_int)
        
        if u_idx is None or i_idx is None:
            return 0
            
        # Prediction
        # user_vector = U[u_idx] (1 x k)
        # item_vector = Vt[:, i_idx] (k x 1)
        # score = user_vector . Sigma . item_vector
        
        user_vec = self.U[u_idx, :]
        item_vec = self.Vt[:, i_idx]
        
        if np.all(user_vec == 0) or np.all(item_vec == 0):
            return 0

        # Sigma is diagonal, so we can do element-wise mult
        # or just dot product
        # user_features_weighted = np.dot(user_vec, self.Sigma)
        # score = np.dot(user_features_weighted, item_vec)
        
        # Optimization: Sigma is diagonal KxK
        # score = sum( U[u][k] * Sigma[k][k] * Vt[k][i] )
        # Since Sigma is stored as full matrix in my save script, use dot
        dot_product = np.dot(np.dot(user_vec, self.Sigma), item_vec)
        
        # Add Mean back
        user_mean = self.user_means.get(user_id_str, self.global_mean)
        score = user_mean + dot_product
        
        return score

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
            ).values('id', 'tmdb_id')
            
            predictions = []
            user_id_str = f"loc_{user_id}"
            
            # Pre-check if user is in model
            if user_id_str not in self.user_to_idx:
                return self._get_popular_movies(max_recommendations, user_rated_movie_ids)

            for movie in candidates:
                tmdb_id = movie['tmdb_id']
                est = self.predict_rating(user_id_str, tmdb_id)
                
                if est == 0:
                    continue

                # If est is 0, it means item might not be in model or orthogonal
                # SVD can output neg values or >5. Clamp and scale.
                # Range 0.5-5.0 -> 1.0-10.0
                est = max(0.5, min(5.0, est))
                predicted_rating = round(est * 2, 1)
                
                predictions.append((movie['id'], predicted_rating))
            
            # Sort by predicted rating
            predictions.sort(key=lambda x: x[1], reverse=True)
            top_predictions = predictions[:max_recommendations]
            
            # Fetch predicted movies
            top_movie_ids = [p[0] for p in top_predictions]
            movies = list(Movie.objects.filter(id__in=top_movie_ids))
            movies_dict = {m.id: m for m in movies}
            
            recommendations = []
            for m_id, score in top_predictions:
                if m_id in movies_dict:
                    movie = movies_dict[m_id]
                    movie.predicted_rating = score
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
            if est < 3.5: # < 7.0/10
                continue
                
            predictions.append({
                'tmdb_id': int(tmdb_id),
                'predicted_rating': round(est * 2, 1)
            })
                
        predictions.sort(key=lambda x: x['predicted_rating'], reverse=True)
        return predictions[:max_recommendations]
    
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
