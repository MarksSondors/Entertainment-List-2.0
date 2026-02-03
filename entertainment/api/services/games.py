# RAWG API Service
from api.base import BaseService
from decouple import config
from datetime import datetime, timedelta
import requests
import time
import random
import os
from requests.exceptions import ConnectionError, Timeout


class GamesService(BaseService):
    """
    RAWG API service for fetching game data.
    API Documentation: https://rawg.io/apidocs
    
    Note: RAWG uses 'key' as the API key parameter name (not 'api_key'),
    so we override the _get method to handle this.
    """
    
    def __init__(self):
        super().__init__(
            base_url='https://api.rawg.io/api',
            api_key=None  # Don't pass to base, we handle it ourselves
        )
        # Set api_key AFTER super().__init__() to avoid being overwritten
        self.api_key = os.environ.get("RAWG_API_KEY") or config("RAWG_API_KEY", default=None)
    
    def _get(self, endpoint, params=None, max_retries=3):
        """
        Override base _get to use 'key' parameter instead of 'api_key'.
        RAWG API requires the parameter to be named 'key'.
        """
        endpoint = endpoint.lstrip('/')
        url = f'{self.base_url.rstrip("/")}/{endpoint}'
        
        # RAWG uses 'key' not 'api_key'
        params = params or {}
        params['key'] = self.api_key
        
        retries = 0
        while retries <= max_retries:
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                return response.json()
            except (ConnectionError, Timeout) as e:
                retries += 1
                if retries > max_retries:
                    print(f"Failed after {max_retries} retries: {e}")
                    raise
                backoff = (2 ** (retries - 1)) * (1 + random.random())
                print(f"Connection error: {e}. Retrying in {backoff:.1f} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                print(f"Request error: {e}")
                raise
    
    def search_games(self, query, page_size=10, page=1):
        """
        Search for games by name.
        
        Args:
            query: Search query string
            page_size: Number of results per page (max 40)
            page: Page number for pagination
        
        Returns:
            Dict with 'count', 'next', 'previous', and 'results' keys
        """
        params = {
            'search': query,
            'page_size': min(page_size, 40),
            'page': page,
            'search_precise': True,
        }
        return self._get('games', params=params)
    
    def get_game_details(self, game_id):
        """
        Get detailed information about a specific game.
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with full game details including description, ratings, etc.
        """
        return self._get(f'games/{game_id}')

    def get_game_series(self, game_id, page=1, page_size=20):
        """
        Get games that are part of the same series.
        
        Args:
            game_id: RAWG game ID or slug
            page: Page number
            page_size: Results per page
            
        Returns:
            Dict with list of games in the series
        """
        params = {
            'page': page,
            'page_size': page_size
        }
        return self._get(f'games/{game_id}/game-series', params=params)
    
    def get_game_screenshots(self, game_id, page=1, page_size=20):
        """
        Get screenshots for a specific game.
        
        Args:
            game_id: RAWG game ID or slug
            page: Page number
            page_size: Results per page
        
        Returns:
            Dict with screenshot results
        """
        params = {
            'page': page,
            'page_size': page_size,
        }
        return self._get(f'games/{game_id}/screenshots', params=params)
    
    def get_game_movies(self, game_id, page=1, page_size=20):
        """
        Get trailers/movies for a specific game.
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with video/trailer results
        """
        params = {
            'page': page,
            'page_size': page_size,
        }
        return self._get(f'games/{game_id}/movies', params=params)
    
    def get_game_additions(self, game_id, page=1, page_size=20):
        """
        Get DLCs and editions for a specific game.
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with DLC/edition results
        """
        params = {
            'page': page,
            'page_size': page_size,
        }
        return self._get(f'games/{game_id}/additions', params=params)
    
    def get_game_parent(self, game_id):
        """
        Get parent game (for DLCs/editions).
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with parent game info
        """
        return self._get(f'games/{game_id}/parent-games')
    
    def get_game_achievements(self, game_id, page=1, page_size=20):
        """
        Get achievements for a specific game.
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with achievement results
        """
        params = {
            'page': page,
            'page_size': page_size,
        }
        return self._get(f'games/{game_id}/achievements', params=params)
    
    def get_game_stores(self, game_id):
        """
        Get store links for a specific game.
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with store links (Steam, GOG, Epic, etc.)
        """
        return self._get(f'games/{game_id}/stores')
    
    def get_game_reddit(self, game_id):
        """
        Get Reddit posts related to a specific game.
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with Reddit post results
        """
        return self._get(f'games/{game_id}/reddit')
    
    def get_game_suggested(self, game_id, page=1, page_size=10):
        """
        Get suggested games similar to the specified game.
        
        Args:
            game_id: RAWG game ID or slug
        
        Returns:
            Dict with suggested game results
        """
        params = {
            'page': page,
            'page_size': page_size,
        }
        return self._get(f'games/{game_id}/suggested', params=params)
    
    def get_popular_games(self, page_size=20, page=1, ordering='-rating'):
        """
        Get popular games based on rating.
        
        Args:
            page_size: Number of results per page
            page: Page number
            ordering: Sort order (e.g., '-rating', '-added', '-released')
        
        Returns:
            Dict with game results
        """
        params = {
            'page_size': min(page_size, 40),
            'page': page,
            'ordering': ordering,
            'metacritic': '70,100',  # Only games with metacritic score 70+
        }
        return self._get('games', params=params)
    
    def get_new_releases(self, page_size=20, page=1):
        """
        Get recently released games (last 30 days).
        
        Returns:
            Dict with newly released games
        """
        today = datetime.now()
        thirty_days_ago = today - timedelta(days=30)
        
        params = {
            'page_size': min(page_size, 40),
            'page': page,
            'dates': f'{thirty_days_ago.strftime("%Y-%m-%d")},{today.strftime("%Y-%m-%d")}',
            'ordering': '-released',
        }
        return self._get('games', params=params)
    
    def get_upcoming_games(self, page_size=20, page=1):
        """
        Get upcoming games (future release dates).
        
        Returns:
            Dict with upcoming games
        """
        today = datetime.now()
        one_year_later = today + timedelta(days=365)
        
        params = {
            'page_size': min(page_size, 40),
            'page': page,
            'dates': f'{today.strftime("%Y-%m-%d")},{one_year_later.strftime("%Y-%m-%d")}',
            'ordering': 'released',
        }
        return self._get('games', params=params)
    
    def get_games_by_genre(self, genre_slug, page_size=20, page=1, ordering='-rating'):
        """
        Get games by genre.
        
        Args:
            genre_slug: Genre slug (e.g., 'action', 'adventure', 'rpg')
            page_size: Number of results per page
            page: Page number
            ordering: Sort order
        
        Returns:
            Dict with games of the specified genre
        """
        params = {
            'genres': genre_slug,
            'page_size': min(page_size, 40),
            'page': page,
            'ordering': ordering,
        }
        return self._get('games', params=params)
    
    def get_games_by_platform(self, platform_id, page_size=20, page=1, ordering='-rating'):
        """
        Get games by platform.
        
        Args:
            platform_id: Platform ID (e.g., 4 for PC, 187 for PS5, 1 for Xbox One)
            page_size: Number of results per page
            page: Page number
            ordering: Sort order
        
        Returns:
            Dict with games for the specified platform
        """
        params = {
            'platforms': platform_id,
            'page_size': min(page_size, 40),
            'page': page,
            'ordering': ordering,
        }
        return self._get('games', params=params)
    
    def get_games_by_store(self, store_id, page_size=20, page=1, ordering='-rating'):
        """
        Get games by store.
        
        Args:
            store_id: Store ID (e.g., 1 for Steam, 2 for Xbox Store, 3 for PlayStation Store)
            page_size: Number of results per page
            page: Page number
            ordering: Sort order
        
        Returns:
            Dict with games for the specified store
        """
        params = {
            'stores': store_id,
            'page_size': min(page_size, 40),
            'page': page,
            'ordering': ordering,
        }
        return self._get('games', params=params)
    
    def get_games_by_developer(self, developer_slug, page_size=20, page=1, ordering='-released'):
        """
        Get games by developer.
        
        Args:
            developer_slug: Developer slug or ID
            page_size: Number of results per page
            page: Page number
            ordering: Sort order
        
        Returns:
            Dict with games by the specified developer
        """
        params = {
            'developers': developer_slug,
            'page_size': min(page_size, 40),
            'page': page,
            'ordering': ordering,
        }
        return self._get('games', params=params)
    
    def get_games_by_publisher(self, publisher_slug, page_size=20, page=1, ordering='-released'):
        """
        Get games by publisher.
        
        Args:
            publisher_slug: Publisher slug or ID
            page_size: Number of results per page
            page: Page number
            ordering: Sort order
        
        Returns:
            Dict with games by the specified publisher
        """
        params = {
            'publishers': publisher_slug,
            'page_size': min(page_size, 40),
            'page': page,
            'ordering': ordering,
        }
        return self._get('games', params=params)
    
    def get_games_by_tag(self, tag_slug, page_size=20, page=1, ordering='-rating'):
        """
        Get games by tag.
        
        Args:
            tag_slug: Tag slug (e.g., 'singleplayer', 'multiplayer', 'open-world')
            page_size: Number of results per page
            page: Page number
            ordering: Sort order
        
        Returns:
            Dict with games with the specified tag
        """
        params = {
            'tags': tag_slug,
            'page_size': min(page_size, 40),
            'page': page,
            'ordering': ordering,
        }
        return self._get('games', params=params)
    
    def get_genres(self, page=1, page_size=100, ordering='name'):
        """
        Get all game genres.
        
        Returns:
            Dict with genre results including id, name, slug, games_count, image_background
        """
        params = {
            'page': page,
            'page_size': min(page_size, 100),
            'ordering': ordering,
        }
        return self._get('genres', params=params)
    
    def get_genre_details(self, genre_id):
        """
        Get details about a specific genre.
        
        Args:
            genre_id: Genre ID or slug
        
        Returns:
            Dict with genre details
        """
        return self._get(f'genres/{genre_id}')
    
    def get_platforms(self, page=1, page_size=100, ordering='name'):
        """
        Get all gaming platforms.
        
        Returns:
            Dict with platform results including id, name, slug, games_count
        """
        params = {
            'page': page,
            'page_size': min(page_size, 100),
            'ordering': ordering,
        }
        return self._get('platforms', params=params)
    
    def get_platform_details(self, platform_id):
        """
        Get details about a specific platform.
        
        Args:
            platform_id: Platform ID or slug
        
        Returns:
            Dict with platform details
        """
        return self._get(f'platforms/{platform_id}')
    
    def get_platform_parent_list(self, page=1, page_size=20):
        """
        Get parent platform list (e.g., PC, PlayStation, Xbox, Nintendo, etc.)
        
        Returns:
            Dict with parent platform results
        """
        params = {
            'page': page,
            'page_size': page_size,
        }
        return self._get('platforms/lists/parents', params=params)
    
    def get_stores(self, page=1, page_size=100, ordering='name'):
        """
        Get all game stores.
        
        Returns:
            Dict with store results including id, name, slug, games_count
        """
        params = {
            'page': page,
            'page_size': min(page_size, 100),
            'ordering': ordering,
        }
        return self._get('stores', params=params)
    
    def get_store_details(self, store_id):
        """
        Get details about a specific store.
        
        Args:
            store_id: Store ID or slug
        
        Returns:
            Dict with store details
        """
        return self._get(f'stores/{store_id}')
    
    def get_developers(self, page=1, page_size=20, ordering='-games_count'):
        """
        Get game developers.
        
        Returns:
            Dict with developer results including id, name, slug, games_count
        """
        params = {
            'page': page,
            'page_size': min(page_size, 40),
            'ordering': ordering,
        }
        return self._get('developers', params=params)
    
    def get_developer_details(self, developer_id):
        """
        Get details about a specific developer.
        
        Args:
            developer_id: Developer ID or slug
        
        Returns:
            Dict with developer details
        """
        return self._get(f'developers/{developer_id}')
    
    def get_publishers(self, page=1, page_size=20, ordering='-games_count'):
        """
        Get game publishers.
        
        Returns:
            Dict with publisher results including id, name, slug, games_count
        """
        params = {
            'page': page,
            'page_size': min(page_size, 40),
            'ordering': ordering,
        }
        return self._get('publishers', params=params)
    
    def get_publisher_details(self, publisher_id):
        """
        Get details about a specific publisher.
        
        Args:
            publisher_id: Publisher ID or slug
        
        Returns:
            Dict with publisher details
        """
        return self._get(f'publishers/{publisher_id}')
    
    def get_tags(self, page=1, page_size=40, ordering='-games_count'):
        """
        Get game tags.
        
        Returns:
            Dict with tag results including id, name, slug, games_count
        """
        params = {
            'page': page,
            'page_size': min(page_size, 40),
            'ordering': ordering,
        }
        return self._get('tags', params=params)
    
    def get_tag_details(self, tag_id):
        """
        Get details about a specific tag.
        
        Args:
            tag_id: Tag ID or slug
        
        Returns:
            Dict with tag details
        """
        return self._get(f'tags/{tag_id}')
    
    def get_creators(self, page=1, page_size=20):
        """
        Get game creators/developers (people).
        
        Returns:
            Dict with creator results
        """
        params = {
            'page': page,
            'page_size': min(page_size, 40),
        }
        return self._get('creators', params=params)
    
    def get_creator_details(self, creator_id):
        """
        Get details about a specific creator.
        
        Args:
            creator_id: Creator ID or slug
        
        Returns:
            Dict with creator details including games, positions, etc.
        """
        return self._get(f'creators/{creator_id}')
    
    def get_creator_roles(self):
        """
        Get available creator roles (e.g., designer, writer, artist).
        
        Returns:
            Dict with creator role results
        """
        return self._get('creator-roles')
    
    def advanced_search(
        self,
        search=None,
        page=1,
        page_size=20,
        ordering='-rating',
        dates=None,
        platforms=None,
        stores=None,
        genres=None,
        tags=None,
        developers=None,
        publishers=None,
        metacritic=None,
        exclude_additions=True,
    ):
        """
        Advanced game search with multiple filters.
        
        Args:
            search: Search query string
            page: Page number
            page_size: Results per page
            ordering: Sort order ('-rating', '-released', '-added', 'name', etc.)
            dates: Date range string (e.g., '2020-01-01,2024-12-31')
            platforms: Comma-separated platform IDs
            stores: Comma-separated store IDs
            genres: Comma-separated genre slugs
            tags: Comma-separated tag slugs
            developers: Comma-separated developer slugs
            publishers: Comma-separated publisher slugs
            metacritic: Metacritic score range (e.g., '80,100')
            exclude_additions: Exclude DLCs and editions
        
        Returns:
            Dict with filtered game results
        """
        params = {
            'page': page,
            'page_size': min(page_size, 40),
            'ordering': ordering,
        }
        
        if search:
            params['search'] = search
            params['search_precise'] = True
        if dates:
            params['dates'] = dates
        if platforms:
            params['platforms'] = platforms
        if stores:
            params['stores'] = stores
        if genres:
            params['genres'] = genres
        if tags:
            params['tags'] = tags
        if developers:
            params['developers'] = developers
        if publishers:
            params['publishers'] = publishers
        if metacritic:
            params['metacritic'] = metacritic
        if exclude_additions:
            params['exclude_additions'] = 'true'
        
        return self._get('games', params=params)
