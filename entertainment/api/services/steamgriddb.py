# SteamGridDB API Service for game cover art
from api.base import BaseService
from decouple import config
import requests
import time
import random
import os
from requests.exceptions import ConnectionError, Timeout


class SteamGridDBService(BaseService):
    """
    SteamGridDB API service for fetching game cover art/posters.
    API Documentation: https://www.steamgriddb.com/api/v2
    
    Get your API key at: https://www.steamgriddb.com/profile/preferences/api
    
    This service provides vertical game covers (grids) that work well as posters,
    unlike RAWG which only provides landscape background images.
    """
    
    def __init__(self):
        super().__init__(
            base_url='https://www.steamgriddb.com/api/v2',
            api_key=None
        )
        # Set API key AFTER super().__init__() to avoid being overwritten
        self.api_key = os.environ.get("STEAMGRIDDB_API_KEY") or config("STEAMGRIDDB_API_KEY", default=None)
    
    def _get(self, endpoint, params=None, max_retries=3):
        """
        Override base _get to use Bearer token authentication.
        SteamGridDB uses Authorization: Bearer <key> header.
        """
        endpoint = endpoint.lstrip('/')
        url = f'{self.base_url.rstrip("/")}/{endpoint}'
        
        headers = {
            'Authorization': f'Bearer {self.api_key}'
        }
        
        retries = 0
        while retries <= max_retries:
            try:
                response = requests.get(url, params=params, headers=headers, timeout=10)
                response.raise_for_status()
                return response.json()
            except (ConnectionError, Timeout) as e:
                retries += 1
                if retries > max_retries:
                    print(f"SteamGridDB: Failed after {max_retries} retries: {e}")
                    raise
                backoff = (2 ** (retries - 1)) * (1 + random.random())
                print(f"SteamGridDB: Connection error: {e}. Retrying in {backoff:.1f}s... (Attempt {retries}/{max_retries})")
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                print(f"SteamGridDB: Request error: {e}")
                raise
    
    def search_games(self, query):
        """
        Search for games by name to get SteamGridDB game ID.
        
        Args:
            query: Search query string (game name)
        
        Returns:
            List of matching games with their SteamGridDB IDs
        """
        try:
            result = self._get(f'search/autocomplete/{query}')
            return result.get('data', [])
        except Exception as e:
            print(f"SteamGridDB search error: {e}")
            return []
    
    def get_grids(self, game_id, dimensions=None, styles=None, types=None, limit=1):
        """
        Get grid images (vertical covers/posters) for a game.
        
        Args:
            game_id: SteamGridDB game ID
            dimensions: Filter by dimensions (e.g., '600x900' for vertical posters)
            styles: Filter by style ('alternate', 'blurred', 'white_logo', 'material', 'no_logo')
            types: Filter by type ('static', 'animated') - defaults to both
            limit: Maximum number of results
        
        Returns:
            List of grid images with URLs
        """
        params = {
            'types': types or 'static,animated'  # Include both static and animated by default
        }
        if dimensions:
            params['dimensions'] = dimensions
        if styles:
            params['styles'] = styles
        
        try:
            result = self._get(f'grids/game/{game_id}', params=params)
            grids = result.get('data', [])
            return grids[:limit] if limit else grids
        except Exception as e:
            print(f"SteamGridDB get_grids error: {e}")
            return []
    
    def get_poster_url(self, game_name):
        """
        Convenience method: Search for game and get the best poster URL.
        
        Args:
            game_name: Name of the game to search for
        
        Returns:
            URL string of the poster image, or None if not found
        """
        games = self.search_games(game_name)
        if not games:
            return None
        
        # Get the first (best) match
        game_id = games[0].get('id')
        if not game_id:
            return None
        
        # Get grids, preferring 600x900 (2:3 aspect ratio like movie posters)
        grids = self.get_grids(game_id, dimensions='600x900', limit=1)
        
        # If no 600x900, try any grid
        if not grids:
            grids = self.get_grids(game_id, limit=1)
        
        if grids:
            return grids[0].get('url')
        
        return None
    
    def get_all_covers(self, game_name, limit=20):
        """
        Get all available covers/grids for a game for user selection.
        
        Args:
            game_name: Name of the game to search for
            limit: Maximum number of covers to return
        
        Returns:
            List of cover dictionaries with url, width, height, etc.
        """
        games = self.search_games(game_name)
        if not games:
            return []
        
        # Get the first (best) match
        game_id = games[0].get('id')
        if not game_id:
            return []
        
        # Get all grids (no dimension filter for more variety)
        grids = self.get_grids(game_id, limit=limit)
        
        # Return list of cover info
        return [{
            'url': grid.get('url'),
            'thumb': grid.get('thumb'),
            'width': grid.get('width'),
            'height': grid.get('height'),
            'style': grid.get('style')
        } for grid in grids]
    
    def get_hero_url(self, game_id):
        """
        Get hero image (banner/backdrop) for a game.
        
        Args:
            game_id: SteamGridDB game ID
        
        Returns:
            URL string of the hero image, or None if not found
        """
        try:
            result = self._get(f'heroes/game/{game_id}')
            heroes = result.get('data', [])
            if heroes:
                return heroes[0].get('url')
        except Exception as e:
            print(f"SteamGridDB get_hero error: {e}")
        return None
