import numpy as np
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Avg, Q, F, Sum
from movies.models import Movie
from custom_auth.models import Review
from datetime import date

User = get_user_model()

class MovieRecommender:
    def __init__(self):
        self.movie_content_type = ContentType.objects.get_for_model(Movie)
    
    def get_recommendations_for_user(self, user_id, max_recommendations=10):
        """
        Get movie recommendations for a user based on collaborative and content-based filtering
        """
        # Get current user's ratings
        user_ratings = Review.objects.filter(
            user_id=user_id,
            content_type=self.movie_content_type
        ).values('object_id', 'rating')
        
        user_ratings_dict = {r['object_id']: r['rating'] for r in user_ratings}
        user_rated_movie_ids = set(user_ratings_dict.keys())
        
        if not user_ratings:
            return self._get_popular_movies(max_recommendations, user_rated_movie_ids)
        
        # Get recommendations using both methods
        cf_recommendations = self._get_collaborative_recommendations(user_id, user_ratings_dict, user_rated_movie_ids, max_recommendations*2)
        cb_recommendations = self._get_content_based_recommendations(user_id, user_rated_movie_ids, max_recommendations*2)
        
        # Combine recommendations with weights
        final_recommendations = self._combine_recommendations(cf_recommendations, cb_recommendations, cf_weight=0.65, cb_weight=0.35)
        
        return final_recommendations[:max_recommendations]
    
    def _get_collaborative_recommendations(self, user_id, user_ratings_dict, user_rated_movie_ids, max_recommendations):
        """Get recommendations based on similar users' preferences"""
        # Find users who rated at least 2 of the same movies
        similar_users = User.objects.filter(
            reviews__content_type=self.movie_content_type,
            reviews__object_id__in=user_rated_movie_ids
        ).annotate(
            common_ratings=Count('reviews', filter=Q(reviews__object_id__in=user_rated_movie_ids))
        ).filter(
            common_ratings__gte=2
        ).exclude(
            id=user_id
        ).values('id')
        
        similar_user_ids = [user['id'] for user in similar_users]
        
        if not similar_user_ids:
            return self._get_popular_movies(max_recommendations, user_rated_movie_ids)
        
        # Get similarity scores for each user
        user_similarities = []
        for sim_user_id in similar_user_ids:
            sim_user_ratings = Review.objects.filter(
                user_id=sim_user_id,
                content_type=self.movie_content_type,
                object_id__in=user_rated_movie_ids
            ).values('object_id', 'rating')
            
            # Calculate similarity (Pearson correlation)
            sim_user_ratings_dict = {r['object_id']: r['rating'] for r in sim_user_ratings}
            common_movie_ids = user_rated_movie_ids.intersection(set(sim_user_ratings_dict.keys()))
            
            if len(common_movie_ids) < 2:
                continue
                
            user_values = [user_ratings_dict[movie_id] for movie_id in common_movie_ids]
            sim_user_values = [sim_user_ratings_dict[movie_id] for movie_id in common_movie_ids]
            
            similarity = self._pearson_correlation(user_values, sim_user_values)
            if not np.isnan(similarity) and similarity > 0:
                user_similarities.append((sim_user_id, similarity))
        
        # Sort by similarity (highest first)
        user_similarities.sort(key=lambda x: x[1], reverse=True)
        top_similar_users = [u[0] for u in user_similarities[:10]]
        
        if not top_similar_users:
            return self._get_popular_movies(max_recommendations, user_rated_movie_ids)
        
        # Get movies rated highly by similar users that current user hasn't rated
        similar_user_movies = Review.objects.filter(
            user_id__in=top_similar_users,
            content_type=self.movie_content_type,
            rating__gte=7.0
        ).exclude(
            object_id__in=user_rated_movie_ids
        ).values('object_id').annotate(
            avg_rating=Avg('rating'),
            rating_count=Count('id')
        ).filter(
            rating_count__gte=1
        ).order_by('-avg_rating')[:max_recommendations*2]
        
        # Get the actual movie objects
        movie_ids = [m['object_id'] for m in similar_user_movies]
        recommended_movies = list(Movie.objects.filter(id__in=movie_ids).select_related('collection'))
        
        # Sort movies by their average rating from similar users
        movie_ratings = {m['object_id']: m['avg_rating'] for m in similar_user_movies}
        recommended_movies.sort(key=lambda m: movie_ratings.get(m.id, 0), reverse=True)
        
        return [(movie, movie_ratings.get(movie.id, 0)) for movie in recommended_movies[:max_recommendations]]

    def _get_content_based_recommendations(self, user_id, user_rated_movie_ids, max_recommendations):
        """Get recommendations based on movie content (genres, people, collections)"""
        # Get highly rated movies by the user (rating >= 7.0)
        high_rated_movies = Review.objects.filter(
            user_id=user_id,
            content_type=self.movie_content_type,
            rating__gte=7.0
        ).values_list('object_id', flat=True)
        
        if not high_rated_movies:
            return []
        
        # Get the movies user liked
        liked_movies = Movie.objects.filter(id__in=high_rated_movies)
        
        # Extract genres, people, and collections from liked movies
        genre_ids = set()
        director_ids = set()
        cast_ids = set()
        crew_ids = set()
        collection_ids = set()
        
        for movie in liked_movies:
            # Add genres
            genre_ids.update(movie.genres.values_list('id', flat=True))
            
            # Separate directors from other crew members
            directors = movie.crew.filter(role='Director').values_list('person_id', flat=True)
            director_ids.update(directors)
            
            # Add only top 3 cast members
            top_cast = movie.cast.order_by('order')[:3].values_list('person_id', flat=True)
            cast_ids.update(top_cast)
            
            # Add other crew (excluding directors)
            other_crew = movie.crew.exclude(role='Director').values_list('person_id', flat=True)
            crew_ids.update(other_crew)
            
            # Add collection
            if movie.collection_id:
                collection_ids.add(movie.collection_id)
        
        # Find movies with similar attributes, excluding already rated ones
        candidate_movies = Movie.objects.exclude(
            id__in=user_rated_movie_ids
        ).filter(
            release_date__lte=date.today()  # Only include movies that have been released
        ).select_related('collection')
        
        # Calculate content similarity scores
        movie_scores = []
        
        for movie in candidate_movies:
            score = 0
            
            # Genre similarity (40% weight)
            movie_genre_ids = set(movie.genres.values_list('id', flat=True))
            genre_overlap = len(genre_ids.intersection(movie_genre_ids))
            if genre_ids:
                genre_score = genre_overlap / len(genre_ids)
                score += 0.4 * genre_score
            
            # Director similarity (25% weight) - higher weight for directors
            movie_director_ids = set(movie.crew.filter(role='Director').values_list('person_id', flat=True))
            director_overlap = len(director_ids.intersection(movie_director_ids))
            if director_ids:
                director_score = director_overlap / len(director_ids)
                score += 0.25 * director_score
            
            # Cast similarity (20% weight) - only using top 3 cast members
            movie_cast_ids = set(movie.cast.order_by('order')[:3].values_list('person_id', flat=True))
            cast_overlap = len(cast_ids.intersection(movie_cast_ids))
            if cast_ids:
                cast_score = cast_overlap / len(cast_ids)
                score += 0.2 * cast_score

            # Other crew similarity (10% weight)
            movie_crew_ids = set(movie.crew.exclude(role='Director').values_list('person_id', flat=True))
            crew_overlap = len(crew_ids.intersection(movie_crew_ids))
            if crew_ids:
                crew_score = crew_overlap / len(crew_ids)
                score += 0.1 * crew_score
            
            # Collection similarity (5% weight)
            if movie.collection_id and movie.collection_id in collection_ids:
                score += 0.05
            
            # Slight penalty for sequels
            import re
            sequel_indicators = re.search(r'\b(2|3|4|5|6|7|8|9|II|III|IV|V|VI|VII|VIII|IX|Part\s+\d+)\b', movie.title)
            if sequel_indicators:
                score -= 0.05  # Small penalty for likely sequels
            
            if score > 0:
                movie_scores.append((movie, score))
        
        # Sort by similarity score (highest first)
        movie_scores.sort(key=lambda x: x[1], reverse=True)
        return movie_scores[:max_recommendations]
    
    def _combine_recommendations(self, cf_recommendations, cb_recommendations, cf_weight=0.65, cb_weight=0.35):
        """Combine collaborative and content-based recommendations with weights"""
        # Create dictionaries for easy lookup
        cf_dict = {movie: score for movie, score in cf_recommendations}
        cb_dict = {movie: score for movie, score in cb_recommendations}
        
        # Combine all movies from both recommendation sets
        all_movies = set([movie for movie, _ in cf_recommendations]).union(
            set([movie for movie, _ in cb_recommendations])
        )
        
        # Calculate combined scores
        combined_scores = []
        for movie in all_movies:
            cf_score = cf_dict.get(movie, 0)
            cb_score = cb_dict.get(movie, 0)
            
            # Normalize the scores if both methods recommended the movie
            if movie in cf_dict and movie in cb_dict:
                combined_score = (cf_weight * cf_score) + (cb_weight * cb_score)
            elif movie in cf_dict:
                combined_score = cf_weight * cf_score
            else:
                combined_score = cb_weight * cb_score
                
            combined_scores.append((movie, combined_score))
        
        # Sort by combined score
        combined_scores.sort(key=lambda x: x[1], reverse=True)
        return [movie for movie, _ in combined_scores]

    def _pearson_correlation(self, x, y):
        """Calculate Pearson correlation coefficient between two lists"""
        x_mean = np.mean(x)
        y_mean = np.mean(y)
        
        numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
        denominator = np.sqrt(sum((xi - x_mean) ** 2 for xi in x)) * np.sqrt(sum((yi - y_mean) ** 2 for yi in y))
        
        if denominator == 0:
            return 0
        return numerator / denominator
    
    def _get_popular_movies(self, limit=10, exclude_movie_ids=None):
        """Get popular movies based on average ratings"""
        # Find movies with highest average ratings and at least 3 reviews
        query = Review.objects.filter(
            content_type=self.movie_content_type
        )
        
        # Exclude movies the user has already rated
        if exclude_movie_ids:
            query = query.exclude(object_id__in=exclude_movie_ids)
        
        popular_movies = query.values('object_id').annotate(
            avg_rating=Avg('rating'),
            rating_count=Count('id')
        ).filter(
            rating_count__gte=3
        ).order_by('-avg_rating')[:limit]
        
        movie_ids = [m['object_id'] for m in popular_movies]
        return list(Movie.objects.filter(id__in=movie_ids))