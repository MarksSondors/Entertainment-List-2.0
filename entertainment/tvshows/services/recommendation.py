import numpy as np
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Avg, Q, F, Sum, Prefetch
from tvshows.models import TVShow
from custom_auth.models import Review, MediaPerson
from datetime import date

User = get_user_model()

class TVShowRecommender:
    def __init__(self):
        self.tv_show_content_type = ContentType.objects.get_for_model(TVShow)
    
    def get_recommendations_for_user(self, user_id, max_recommendations=10):
        """
        Get TV show recommendations for a user based on collaborative and content-based filtering
        """
        # Get current user's ratings
        # Since users rate seasons/subgroups, we aggregate to get an average rating per show
        user_ratings = Review.objects.filter(
            user_id=user_id,
            content_type=self.tv_show_content_type
        ).values('object_id').annotate(avg_rating=Avg('rating'))
        
        user_ratings_dict = {r['object_id']: r['avg_rating'] for r in user_ratings}
        user_rated_show_ids = set(user_ratings_dict.keys())
        
        if not user_ratings:
            return self._get_popular_shows(max_recommendations, user_rated_show_ids)
        
        # Get recommendations using both methods
        cf_recommendations = self._get_collaborative_recommendations(user_id, user_ratings_dict, user_rated_show_ids, max_recommendations*2)
        cb_recommendations = self._get_content_based_recommendations(user_id, user_rated_show_ids, max_recommendations*2)
        
        # Combine recommendations with weights
        final_recommendations = self._combine_recommendations(cf_recommendations, cb_recommendations, cf_weight=0.65, cb_weight=0.35)
        
        return final_recommendations[:max_recommendations]

    def _get_collaborative_recommendations(self, user_id, user_ratings_dict, user_rated_show_ids, max_recommendations):
        """Get recommendations based on similar users' preferences"""
        # Find users who rated at least 2 of the same shows
        similar_users = User.objects.filter(
            reviews__content_type=self.tv_show_content_type,
            reviews__object_id__in=user_rated_show_ids
        ).annotate(
            common_ratings=Count('reviews', filter=Q(reviews__object_id__in=user_rated_show_ids))
        ).filter(
            common_ratings__gte=2
        ).exclude(
            id=user_id
        ).values('id', 'common_ratings')
        
        similar_user_ids = [user['id'] for user in similar_users]
        
        if not similar_user_ids:
            return self._get_popular_shows(max_recommendations, user_rated_show_ids)
        
        # Get all ratings for similar users in one query
        similar_users_ratings = Review.objects.filter(
            user_id__in=similar_user_ids,
            content_type=self.tv_show_content_type,
            object_id__in=user_rated_show_ids
        ).values('user_id', 'object_id').annotate(avg_rating=Avg('rating'))
        
        # Group ratings by user
        users_ratings_dict = {}
        for rating in similar_users_ratings:
            user_id_key = rating['user_id']
            if user_id_key not in users_ratings_dict:
                users_ratings_dict[user_id_key] = {}
            users_ratings_dict[user_id_key][rating['object_id']] = rating['avg_rating']
        
        # Calculate similarity scores for each user
        user_similarities = []
        for sim_user_id in similar_user_ids:
            sim_user_ratings_dict = users_ratings_dict.get(sim_user_id, {})
            common_show_ids = user_rated_show_ids.intersection(set(sim_user_ratings_dict.keys()))
            
            if len(common_show_ids) < 2:
                continue
                
            user_values = [user_ratings_dict[show_id] for show_id in common_show_ids]
            sim_user_values = [sim_user_ratings_dict[show_id] for show_id in common_show_ids]
            
            similarity = self._pearson_correlation(user_values, sim_user_values)
            if not np.isnan(similarity) and similarity > 0:
                user_similarities.append((sim_user_id, similarity))
        
        # Sort by similarity (highest first) and get top 10
        user_similarities.sort(key=lambda x: x[1], reverse=True)
        top_similar_users = [u[0] for u in user_similarities[:10]]
        
        if not top_similar_users:
            return self._get_popular_shows(max_recommendations, user_rated_show_ids)
        
        # Get shows rated highly by similar users that current user hasn't rated
        similar_user_shows = Review.objects.filter(
            user_id__in=top_similar_users,
            content_type=self.tv_show_content_type,
            rating__gte=7.0
        ).exclude(
            object_id__in=user_rated_show_ids
        ).values('object_id').annotate(
            avg_rating=Avg('rating'),
            rating_count=Count('id')
        ).filter(
            rating_count__gte=1
        ).order_by('-avg_rating')[:max_recommendations*2]
        
        # Get the actual show objects
        show_ids = [s['object_id'] for s in similar_user_shows]
        recommended_shows = list(TVShow.objects.filter(id__in=show_ids))
        
        # Sort shows by their average rating from similar users
        show_ratings = {s['object_id']: s['avg_rating'] for s in similar_user_shows}
        recommended_shows.sort(key=lambda s: show_ratings.get(s.id, 0), reverse=True)
        
        return [(show, show_ratings.get(show.id, 0)) for show in recommended_shows[:max_recommendations]]

    def _get_content_based_recommendations(self, user_id, user_rated_show_ids, max_recommendations):
        """Get recommendations based on show content (genres, people)"""
        # Get highly rated shows by the user (rating >= 7.0)
        # We need to aggregate first to find shows with avg rating >= 7.0
        high_rated_shows_data = Review.objects.filter(
            user_id=user_id,
            content_type=self.tv_show_content_type
        ).values('object_id').annotate(avg_rating=Avg('rating')).filter(avg_rating__gte=7.0)
        
        high_rated_show_ids = [item['object_id'] for item in high_rated_shows_data]
        
        if not high_rated_show_ids:
            return []
        
        # Get the shows user liked
        liked_shows = TVShow.objects.filter(id__in=high_rated_show_ids).prefetch_related('genres')
        
        # Get MediaPerson data for liked shows in bulk
        liked_shows_mediapersons = MediaPerson.objects.filter(
            content_type=self.tv_show_content_type,
            object_id__in=high_rated_show_ids
        ).select_related('person').order_by('order')
        
        # Group MediaPerson data by show ID
        show_cast_crew = {}
        for mp in liked_shows_mediapersons:
            show_id = mp.object_id
            if show_id not in show_cast_crew:
                show_cast_crew[show_id] = {'cast': [], 'crew': []}
            
            if mp.role == 'Actor':
                show_cast_crew[show_id]['cast'].append(mp)
            else:
                show_cast_crew[show_id]['crew'].append(mp)
        
        # Extract genres and people from liked shows efficiently
        genre_ids = set()
        creator_ids = set()
        cast_ids = set()
        crew_ids = set()
        
        for show in liked_shows:
            # Add genres
            genre_ids.update(genre.id for genre in show.genres.all())
            
            # Get cast and crew from our bulk data
            show_data = show_cast_crew.get(show.id, {'cast': [], 'crew': []})
            
            # Process crew (including creators)
            for crew_member in show_data['crew']:
                if crew_member.role == 'Creator':
                    creator_ids.add(crew_member.person_id)
                else:
                    crew_ids.add(crew_member.person_id)
            
            # Process cast (limit to top 3 for performance)
            for cast_member in show_data['cast'][:3]:
                cast_ids.add(cast_member.person_id)
        
        # Find candidate shows more efficiently
        candidate_shows_base = TVShow.objects.exclude(
            id__in=user_rated_show_ids
        ).filter(
            first_air_date__lte=date.today()
        )
        
        # If we have genre preferences, prioritize shows with those genres
        if genre_ids:
            priority_shows = candidate_shows_base.filter(
                genres__id__in=genre_ids
            ).distinct()[:max_recommendations * 4]
            
            # Get additional shows if we don't have enough
            remaining_needed = max(0, max_recommendations * 6 - len(priority_shows))
            if remaining_needed > 0:
                additional_shows = candidate_shows_base.exclude(
                    id__in=[s.id for s in priority_shows]
                )[:remaining_needed]
                candidate_shows = list(priority_shows) + list(additional_shows)
            else:
                candidate_shows = list(priority_shows)
        else:
            candidate_shows = list(candidate_shows_base[:max_recommendations * 6])
            
        # Calculate similarity scores
        show_scores = []
        
        # Pre-fetch data for candidate shows to avoid N+1
        candidate_show_ids = [s.id for s in candidate_shows]
        
        # Bulk fetch genres
        candidate_genres = {}
        for show in TVShow.objects.filter(id__in=candidate_show_ids).prefetch_related('genres'):
            candidate_genres[show.id] = set(g.id for g in show.genres.all())
            
        # Bulk fetch people
        candidate_people = {}
        candidate_mps = MediaPerson.objects.filter(
            content_type=self.tv_show_content_type,
            object_id__in=candidate_show_ids
        ).select_related('person')
        
        for mp in candidate_mps:
            if mp.object_id not in candidate_people:
                candidate_people[mp.object_id] = {'cast': set(), 'crew': set(), 'creators': set()}
            
            if mp.role == 'Actor':
                candidate_people[mp.object_id]['cast'].add(mp.person_id)
            elif mp.role == 'Creator':
                candidate_people[mp.object_id]['creators'].add(mp.person_id)
            else:
                candidate_people[mp.object_id]['crew'].add(mp.person_id)
        
        for show in candidate_shows:
            score = 0
            
            # Genre similarity (Jaccard index)
            show_genre_ids = candidate_genres.get(show.id, set())
            if genre_ids and show_genre_ids:
                intersection = len(genre_ids.intersection(show_genre_ids))
                union = len(genre_ids.union(show_genre_ids))
                score += (intersection / union) * 3.0
            
            # People similarity
            people_data = candidate_people.get(show.id, {'cast': set(), 'crew': set(), 'creators': set()})
            
            # Creators (high weight)
            if creator_ids:
                common_creators = len(creator_ids.intersection(people_data['creators']))
                score += common_creators * 4.0
            
            # Cast (medium weight)
            if cast_ids:
                common_cast = len(cast_ids.intersection(people_data['cast']))
                score += common_cast * 2.0
                
            # Other crew (low weight)
            if crew_ids:
                common_crew = len(crew_ids.intersection(people_data['crew']))
                score += common_crew * 1.0
            
            # Boost highly rated shows
            if show.rating:
                score += show.rating * 0.1
                
            show_scores.append((show, score))
            
        # Sort by score
        show_scores.sort(key=lambda x: x[1], reverse=True)
        
        return show_scores[:max_recommendations]

    def _get_popular_shows(self, max_recommendations, user_rated_show_ids):
        """Get popular shows as fallback"""
        popular_shows = TVShow.objects.exclude(
            id__in=user_rated_show_ids
        ).order_by('-rating', '-date_added')[:max_recommendations]
        
        return [(show, 0) for show in popular_shows]

    def _combine_recommendations(self, cf_recs, cb_recs, cf_weight=0.65, cb_weight=0.35):
        """Combine recommendations from different sources"""
        combined_scores = {}
        
        # Normalize scores
        if cf_recs:
            max_cf_score = max(score for _, score in cf_recs)
            if max_cf_score > 0:
                cf_recs = [(show, score/max_cf_score) for show, score in cf_recs]
                
        if cb_recs:
            max_cb_score = max(score for _, score in cb_recs)
            if max_cb_score > 0:
                cb_recs = [(show, score/max_cb_score) for show, score in cb_recs]
        
        # Add CF scores
        for show, score in cf_recs:
            combined_scores[show] = score * cf_weight
            
        # Add CB scores
        for show, score in cb_recs:
            if show in combined_scores:
                combined_scores[show] += score * cb_weight
            else:
                combined_scores[show] = score * cb_weight
                
        # Convert back to list and sort
        final_recs = [(show, score) for show, score in combined_scores.items()]
        final_recs.sort(key=lambda x: x[1], reverse=True)
        
        return final_recs

    def _pearson_correlation(self, user1_ratings, user2_ratings):
        """Calculate Pearson correlation coefficient between two users"""
        if len(user1_ratings) < 2:
            return 0
            
        return np.corrcoef(user1_ratings, user2_ratings)[0, 1]
