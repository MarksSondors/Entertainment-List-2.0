"""
Cross-media recommendation service that suggests content across all media types.
Provides unified recommendations across movies, games, books, music, and TV shows.
"""

from typing import List, Dict, Any, Optional, TYPE_CHECKING
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from custom_auth.models import Review, Watchlist

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

User = get_user_model()


class CrossMediaRecommender:
    """
    Unified recommendation system that works across all media types.
    Provides suggestions based on user's taste across movies, games, books, music, etc.
    """
    
    def __init__(self):
        self._recommenders = {}
        self._initialize_recommenders()
    
    def _initialize_recommenders(self):
        """Initialize recommenders for each media type."""
        try:
            from movies.services.recommendation import MovieRecommender
            self._recommenders['movies'] = MovieRecommender()
        except ImportError:
            pass
        
        try:
            from games.services.recommendation import GameRecommender
            self._recommenders['games'] = GameRecommender()
        except ImportError:
            pass
        
        # Add other media types as they're implemented
        # try:
        #     from books.services.recommendation import BookRecommender
        #     self._recommenders['books'] = BookRecommender()
        # except ImportError:
        #     pass
        
        # try:
        #     from music.services.recommendation import MusicRecommender
        #     self._recommenders['music'] = MusicRecommender()
        # except ImportError:
        #     pass
        
        try:
            from tvshows.services.recommendation import TVShowRecommender
            self._recommenders['tvshows'] = TVShowRecommender()
        except ImportError:
            pass
    
    def get_unified_recommendations(
        self, 
        user, 
        limit_per_medium: int = 10,
        include_movies: bool = True,
        include_games: bool = True,
        include_books: bool = False,
        include_music: bool = False,
        include_tvshows: bool = False
    ) -> Dict[str, List[Any]]:
        """
        Get recommendations across all media types.
        
        Args:
            user: User object
            limit_per_medium: Number of recommendations per media type
            include_movies: Include movie recommendations
            include_games: Include game recommendations
            include_books: Include book recommendations
            include_music: Include music recommendations
            include_tvshows: Include TV show recommendations
        
        Returns:
            Dict with keys: 'movies', 'games', 'books', 'music', 'tvshows'
            Each containing a list of recommended items.
        """
        recommendations = {}
        
        if include_movies and 'movies' in self._recommenders:
            try:
                # get_recommendations_for_user returns list of (movie, score) tuples
                recs = self._recommenders['movies'].get_recommendations_for_user(
                    user.id, max_recommendations=limit_per_medium
                )
                recommendations['movies'] = [item for item, score in recs]
            except Exception as e:
                print(f"Error getting movie recommendations: {e}")
                recommendations['movies'] = []
        
        if include_games and 'games' in self._recommenders:
            try:
                recs = self._recommenders['games'].get_recommendations_for_user(
                    user.id, max_recommendations=limit_per_medium
                )
                recommendations['games'] = [item for item, score in recs]
            except Exception as e:
                print(f"Error getting game recommendations: {e}")
                recommendations['games'] = []
        
        if include_books and 'books' in self._recommenders:
            try:
                recs = self._recommenders['books'].get_recommendations_for_user(
                    user.id, max_recommendations=limit_per_medium
                )
                recommendations['books'] = [item for item, score in recs]
            except Exception as e:
                print(f"Error getting book recommendations: {e}")
                recommendations['books'] = []
        
        if include_music and 'music' in self._recommenders:
            try:
                recs = self._recommenders['music'].get_recommendations_for_user(
                    user.id, max_recommendations=limit_per_medium
                )
                recommendations['music'] = [item for item, score in recs]
            except Exception as e:
                print(f"Error getting music recommendations: {e}")
                recommendations['music'] = []
        
        if include_tvshows and 'tvshows' in self._recommenders:
            try:
                recs = self._recommenders['tvshows'].get_recommendations_for_user(
                    user.id, max_recommendations=limit_per_medium
                )
                recommendations['tvshows'] = [item for item, score in recs]
            except Exception as e:
                print(f"Error getting TV show recommendations: {e}")
                recommendations['tvshows'] = []
        
        return recommendations
    
    def get_discovery_feed(
        self, 
        user, 
        total_items: int = 20,
        media_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get a mixed feed of recommendations across all media types.
        Returns items in a unified format for display.
        
        Args:
            user: User object
            total_items: Total number of items in the feed
            media_types: List of media types to include. If None, includes all available.
        
        Returns:
            List of dicts with normalized format for display
        """
        if media_types is None:
            media_types = ['movies', 'games', 'books', 'music', 'tvshows']
        
        # Determine which media types to include
        include_params = {
            'include_movies': 'movies' in media_types,
            'include_games': 'games' in media_types,
            'include_books': 'books' in media_types,
            'include_music': 'music' in media_types,
            'include_tvshows': 'tvshows' in media_types,
        }
        
        # Get recommendations from all sources
        all_recommendations = self.get_unified_recommendations(
            user,
            limit_per_medium=10,
            **include_params
        )
        
        # Normalize to unified format
        feed_items = []
        
        for media_type, items in all_recommendations.items():
            for item in items:
                feed_items.append(self._normalize_item(item, media_type))
        
        # Shuffle for variety
        import random
        random.shuffle(feed_items)
        
        return feed_items[:total_items]
    
    def _normalize_item(self, item: Any, media_type: str) -> Dict[str, Any]:
        """
        Normalize a media item to a consistent format.
        
        Returns:
            Dict with common fields across all media types
        """
        # Handle rating scaling for games (0-5 to 0-10)
        rating = getattr(item, 'rating', None)
        if media_type == 'games' and rating is not None:
            rating = rating * 2

        data = {
            'media_type': media_type,
            # 'item': item,  # Removed to avoid serialization issues
            'id': item.id,
            'title': item.title,
            'poster': getattr(item, 'poster', None),
            'rating': rating,
            'normalized_rating': getattr(item, 'normalized_rating', None),
            'year': getattr(item, 'year', None),
            # 'content_type': ContentType.objects.get_for_model(item.__class__), # Removed to avoid serialization issues
            'object_id': item.id,
            'url': item.get_absolute_url() if hasattr(item, 'get_absolute_url') else None,
        }
        
        # Add tmdb_id if available
        if hasattr(item, 'tmdb_id'):
            data['tmdb_id'] = item.tmdb_id
            
        return data
    
    def get_cross_media_similar_items(
        self, 
        source_item: Any,
        limit: int = 10,
        target_media_types: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Find similar content across different media types.
        E.g., if user likes a movie, suggest games/books with similar themes.
        
        Args:
            source_item: The source media item (Movie, Game, Book, etc.)
            limit: Maximum number of recommendations
            target_media_types: List of media types to search in. If None, searches all except source type.
        
        Returns:
            List of normalized items from other media types
        """
        # Extract themes, genres, keywords from source item
        themes = self._extract_themes(source_item)
        
        if not themes:
            return []
        
        # Determine source media type
        source_type = self._get_media_type_name(source_item)
        
        # If target types not specified, use all except source
        if target_media_types is None:
            all_types = ['movies', 'games', 'books', 'music', 'tvshows']
            target_media_types = [t for t in all_types if t != source_type]
        
        # Search across media types
        similar_items = []
        
        for media_type in target_media_types:
            items = self._find_items_by_themes(media_type, themes, limit=5)
            for item in items:
                similar_items.append(self._normalize_item(item, media_type))
        
        return similar_items[:limit]
    
    def _extract_themes(self, item: Any) -> Dict[str, set]:
        """Extract themes, genres, keywords, people, and collections from an item."""
        themes = {
            'genres': set(),
            'keywords': set(),
            'people': set(),
            'collections': set(),
        }
        
        # Get genres (works for movies, games, etc.)
        if hasattr(item, 'genres'):
            themes['genres'] = set(item.genres.values_list('name', flat=True))
        
        # Get keywords
        if hasattr(item, 'keywords'):
            themes['keywords'] = set(item.keywords.values_list('name', flat=True))
            
        # Get collections
        if hasattr(item, 'collection') and item.collection:
            themes['collections'].add(item.collection.name)
            
        if hasattr(item, 'game_collection') and item.game_collection:
            themes['collections'].add(item.game_collection.name)
            
        # Get people (for Movies/TV)
        if hasattr(item, 'cast'):
            # cast is usually a property returning a queryset or list of MediaPerson
            try:
                for mp in item.cast:
                    if hasattr(mp, 'person'):
                        themes['people'].add(mp.person.name)
            except:
                pass
                
        if hasattr(item, 'crew'):
            try:
                for mp in item.crew:
                    if hasattr(mp, 'person'):
                        themes['people'].add(mp.person.name)
            except:
                pass
        
        return themes
    
    def _get_media_type_name(self, item: Any) -> str:
        """Determine the media type of an item."""
        model_name = item.__class__.__name__.lower()
        
        if 'movie' in model_name:
            return 'movies'
        elif 'game' in model_name:
            return 'games'
        elif 'book' in model_name:
            return 'books'
        elif 'album' in model_name or 'music' in model_name:
            return 'music'
        elif 'tvshow' in model_name or 'show' in model_name:
            return 'tvshows'
        
        return 'unknown'
    
    def _find_items_by_themes(
        self, 
        media_type: str, 
        themes: Dict[str, set], 
        limit: int = 5
    ) -> List[Any]:
        """Find items of a specific media type matching the given themes."""
        # Import models dynamically
        try:
            if media_type == 'movies':
                from movies.models import Movie
                model = Movie
            elif media_type == 'games':
                from games.models import Game
                model = Game
            elif media_type == 'books':
                from books.models import Book
                model = Book
            elif media_type == 'music':
                from music.models import Album
                model = Album
            elif media_type == 'tvshows':
                from tvshows.models import TVShow
                model = TVShow
            else:
                return []
        except ImportError:
            return []
        
        # Build query
        from django.db.models import Q, Count
        
        query = Q()
        
        # Match genres
        if themes.get('genres'):
            query |= Q(genres__name__in=themes['genres'])
        
        # Match keywords
        if themes.get('keywords'):
            query |= Q(keywords__name__in=themes['keywords'])
            
        # Match collections
        if themes.get('collections'):
            if media_type == 'movies':
                query |= Q(collection__name__in=themes['collections'])
            elif media_type == 'games':
                query |= Q(game_collection__name__in=themes['collections'])
            
        # Match people (only for Movies/TV)
        if themes.get('people') and media_type in ['movies', 'tvshows']:
            try:
                from custom_auth.models import Person, MediaPerson
                from django.contrib.contenttypes.models import ContentType
                
                # Get ContentType for the target model
                ct = ContentType.objects.get_for_model(model)
                
                # Find matching people
                matching_people = Person.objects.filter(name__in=themes['people'])
                
                # Find media IDs linked to these people
                media_ids = MediaPerson.objects.filter(
                    content_type=ct,
                    person__in=matching_people
                ).values_list('object_id', flat=True)
                
                if media_ids:
                    query |= Q(id__in=media_ids)
            except Exception as e:
                print(f"Error matching people: {e}")
        
        if not query:
            return []
        
        # Execute query
        items = model.objects.filter(query).distinct().annotate(
            match_count=Count('id')
        ).order_by('-match_count')[:limit]
        
        return list(items)
    
    def get_user_taste_profile(self, user) -> Dict[str, Any]:
        """
        Analyze user's ratings across all media types to build a taste profile.
        
        Returns:
            Dict with favorite genres, average ratings, and preferences across media types
        """
        from django.db.models import Avg, Count
        
        # Get all user reviews
        reviews = Review.objects.filter(user=user).select_related('content_type')
        
        profile = {
            'total_reviews': reviews.count(),
            'average_rating': reviews.aggregate(Avg('rating'))['rating__avg'],
            'media_breakdown': {},
            'favorite_genres': [],
        }
        
        # Breakdown by media type
        media_types = reviews.values('content_type__model').annotate(
            count=Count('id'),
            avg_rating=Avg('rating')
        )
        
        for mt in media_types:
            model_name = mt['content_type__model']
            profile['media_breakdown'][model_name] = {
                'count': mt['count'],
                'average_rating': mt['avg_rating']
            }
        
        # Get favorite genres across all media
        # This is complex because genres are on the related objects, not the review
        # We'll do a simplified version: get high rated reviews and check genres of those items
        
        high_rated_reviews = reviews.filter(rating__gte=7.0)
        genre_counts = {}
        
        for review in high_rated_reviews:
            item = review.media
            if hasattr(item, 'genres'):
                for genre in item.genres.all():
                    name = genre.name
                    genre_counts[name] = genre_counts.get(name, 0) + 1
        
        # Sort genres by count
        sorted_genres = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)
        profile['favorite_genres'] = [g[0] for g in sorted_genres[:10]]
        
        return profile
