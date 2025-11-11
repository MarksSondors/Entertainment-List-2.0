# IGDB API Service
import time
import threading
from collections import deque
from api.base import BaseService
from decouple import config
import requests


class RateLimiter:
    """Rate limiter to control the number of requests per second"""
    def __init__(self, max_calls, period=1.0):
        self.max_calls = max_calls  # maximum calls allowed in period
        self.period = period  # time period in seconds
        self.calls = deque()  # timestamps of calls
        self.lock = threading.RLock()  # lock for thread safety

    def wait_if_needed(self):
        """Wait if rate limit has been reached"""
        with self.lock:
            now = time.time()
            
            # Remove timestamps older than our period
            while self.calls and self.calls[0] <= now - self.period:
                self.calls.popleft()
            
            # If we've reached our limit, wait
            if len(self.calls) >= self.max_calls:
                sleep_time = self.calls[0] - (now - self.period)
                if sleep_time > 0:
                    time.sleep(sleep_time)
            
            # Record this call
            self.calls.append(time.time())


class IGDBTokenManager:
    """Manages Twitch OAuth2 tokens for IGDB API access"""
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None
        self.token_expires_at = None
        self.lock = threading.RLock()
    
    def get_valid_token(self):
        """Get a valid access token, refreshing if necessary"""
        with self.lock:
            # Check if token exists and is still valid (with 5 minute buffer)
            if (self.token and self.token_expires_at and 
                time.time() < self.token_expires_at - 300):
                return self.token
              # Get new token
            return self._get_new_token()
    
    def _get_new_token(self):
        """Get a new access token from Twitch"""
        url = "https://id.twitch.tv/oauth2/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials"
        }
        
        
        try:
            response = requests.post(url, data=data, timeout=10)
            print(f"DEBUG: Token request response status: {response.status_code}")
            response.raise_for_status()
            token_data = response.json()
            print(f"DEBUG: Token obtained successfully")
            
            self.token = token_data['access_token']
            # Tokens expire in 60 days according to IGDB docs
            self.token_expires_at = time.time() + token_data.get('expires_in', 5184000)
            
            return self.token
        
        except requests.exceptions.RequestException as e:
            print(f"Failed to get IGDB access token: {e}")
            raise


class GamesService(BaseService):
    def __init__(self):
        
        try:
            self.client_id = config("IGDB_CLIENT_ID")
            self.client_secret = config("IGDB_CLIENT_SECRET")
        except Exception as e:
            raise
        
        self.token_manager = IGDBTokenManager(self.client_id, self.client_secret)
        
        super().__init__(
            base_url='https://api.igdb.com/v4'
        )
        
        # Initialize rate limiter - IGDB doesn't specify exact limits, 
        # so using conservative 4 requests per second
        self.rate_limiter = RateLimiter(max_calls=4)
    
    def _post(self, endpoint, data=None, max_retries=3):
        """Override _post method to add IGDB-specific headers and rate limiting"""
        # Wait if we need to respect the rate limit
        self.rate_limiter.wait_if_needed()
        
        # Get valid token
        token = self.token_manager.get_valid_token()
        
        # Ensure endpoint doesn't start with a slash to avoid double slashes
        endpoint = endpoint.lstrip('/') 
        url = f'{self.base_url.rstrip("/")}/{endpoint}'
        
        headers = {
            'Client-ID': self.client_id,
            'Authorization': f'Bearer {token}',
            'Content-Type': 'text/plain'  # IGDB expects text/plain for query body
        }
        
        # Convert data to IGDB query format if it's a dict
        if isinstance(data, dict):
            # Convert dict to IGDB query string
            query_parts = []
            for key, value in data.items():
                if isinstance(value, list):
                    query_parts.append(f"{key} {','.join(map(str, value))}")
                else:
                    query_parts.append(f"{key} {value}")
            query_body = '; '.join(query_parts) + ';'
        else:
            query_body = data or ''
        
        # Initialize retry counter
        retries = 0
        
        while retries <= max_retries:
            try:
                response = requests.post(url, data=query_body, headers=headers, timeout=10)
                response.raise_for_status()
                return response.json()
            except (requests.ConnectionError, requests.Timeout) as e:
                retries += 1
                if retries > max_retries:
                    print(f"Failed after {max_retries} retries: {e}")
                    raise
                
                # Calculate backoff time with jitter
                backoff = (2 ** (retries - 1)) * (1 + __import__('random').random())
                print(f"Connection error: {e}. Retrying in {backoff:.1f} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                print(f"Request error: {e}")
                raise
    
    def search_games(self, query, limit=10, offset=0):
        """Search for games by name"""
        igdb_query = f"""
            search "{query}";
         fields name, summary, cover.url, cover.image_id, first_release_date, rating, rating_count,
                   aggregated_rating, aggregated_rating_count, storyline, status, category,
                   genres.name, platforms.name, involved_companies.company.name,
                   involved_companies.developer, involved_companies.publisher,
             screenshots.url, screenshots.image_id, artworks.url, collection.name, franchise.name;
            limit {limit};
            offset {offset};
        """
        return self._post('games', igdb_query.strip())
    
    def get_game_details(self, game_id):
        """Get detailed information about a specific game"""
        igdb_query = f"""
            fields name, summary, cover.url, cover.image_id, first_release_date, rating, rating_count,
                   aggregated_rating, aggregated_rating_count, storyline, status, category,
                   genres.id, genres.name,
                   platforms.id, platforms.name, platforms.abbreviation, platforms.alternative_name, platforms.generation, platforms.platform_logo.url,
                   involved_companies.company.id, involved_companies.company.name, involved_companies.company.logo.url,
                   involved_companies.developer, involved_companies.publisher,
                   screenshots.url, screenshots.image_id, artworks.url,
                   collection.id, collection.name, franchise.id, franchise.name, franchises.id, franchises.name,
                   parent_game,
                   dlcs.name, dlcs.id, expansions.name, expansions.id,
                   similar_games.name, similar_games.id, similar_games.cover.url,
                   keywords.id, keywords.name, websites.url, websites.category,
                   external_games.uid, external_games.category;
            where id = {game_id};
        """
        return self._post('games', igdb_query.strip())
    
    def get_popular_games(self, limit=20, offset=0):
        """Get popular games based on rating and rating count"""
        igdb_query = f"""
            fields name, summary, cover.url, first_release_date, rating, rating_count,
                   aggregated_rating, aggregated_rating_count, genres.name, platforms.name;
            where rating_count > 10 & rating > 70;
            sort rating desc;
            limit {limit};
            offset {offset};
        """
        return self._post('games', igdb_query.strip())
    
    def get_new_releases(self, limit=20, offset=0):
        """Get recently released games"""
        # Get timestamp for 30 days ago
        thirty_days_ago = int(time.time()) - (30 * 24 * 60 * 60)
        
        igdb_query = f"""
            fields name, summary, cover.url, first_release_date, rating, rating_count,
                   genres.name, platforms.name;
            where first_release_date > {thirty_days_ago} & status = 0;
            sort first_release_date desc;
            limit {limit};
            offset {offset};
        """
        return self._post('games', igdb_query.strip())
    
    def get_upcoming_games(self, limit=20, offset=0):
        """Get upcoming games"""
        # Get current timestamp
        now = int(time.time())
        
        igdb_query = f"""
            fields name, summary, cover.url, first_release_date, rating, rating_count,
                   genres.name, platforms.name;
            where first_release_date > {now} & status = 0;
            sort first_release_date asc;
            limit {limit};
            offset {offset};
        """
        return self._post('games', igdb_query.strip())
    
    def get_games_by_genre(self, genre_id, limit=20, offset=0):
        """Get games by genre ID"""
        igdb_query = f"""
            fields name, summary, cover.url, first_release_date, rating, rating_count,
                   genres.name, platforms.name;
            where genres = {genre_id};
            sort rating desc;
            limit {limit};
            offset {offset};
        """
        return self._post('games', igdb_query.strip())
    
    def get_games_by_platform(self, platform_id, limit=20, offset=0):
        """Get games by platform ID"""
        igdb_query = f"""
            fields name, summary, cover.url, first_release_date, rating, rating_count,
                   genres.name, platforms.name;
            where platforms = {platform_id};
            sort rating desc;
            limit {limit};
            offset {offset};
        """
        return self._post('games', igdb_query.strip())
    
    def get_genres(self, limit=100):
        """Get all game genres"""
        igdb_query = f"""
            fields name;
            limit {limit};
        """
        return self._post('genres', igdb_query.strip())
    
    def get_platforms(self, limit=200):
        """Get all gaming platforms"""
        igdb_query = f"""
            fields name, abbreviation, alternative_name, generation, platform_logo.url;
            limit {limit};
        """
        return self._post('platforms', igdb_query.strip())
    
    def get_companies(self, limit=100, offset=0):
        """Get game companies (developers/publishers)"""
        igdb_query = f"""
            fields name, description, logo.url, websites.url, country;
            limit {limit};
            offset {offset};
        """
        return self._post('companies', igdb_query.strip())
    
    def get_game_covers(self, game_ids):
        """Get cover images for multiple games"""
        if isinstance(game_ids, list):
            game_ids_str = ','.join(map(str, game_ids))
        else:
            game_ids_str = str(game_ids)
            
        igdb_query = f"""
            fields game, url, width, height, image_id;
            where game = ({game_ids_str});
        """
        return self._post('covers', igdb_query.strip())
    
    def get_game_screenshots(self, game_id):
        """Get screenshots for a specific game"""
        igdb_query = f"""
            fields game, url, width, height, image_id;
            where game = {game_id};
        """
        return self._post('screenshots', igdb_query.strip())
    
    def get_similar_games(self, game_id, limit=10):
        """Get games similar to the specified game"""
        igdb_query = f"""
            fields name, summary, cover.url, first_release_date, rating, rating_count,
                   genres.name, platforms.name;
            where similar_games = {game_id};
            limit {limit};
        """
        return self._post('games', igdb_query.strip())
    
    def get_game_collections(self, limit=100, offset=0):
        """Get game collections (IGDB 'collections' endpoint)"""
        igdb_query = f"""
            fields name, games.name, games.id;
            limit {limit};
            offset {offset};
        """
        return self._post('collections', igdb_query.strip())
    
    def get_game_franchises(self, limit=100, offset=0):
        """Get game franchises (IGDB 'franchises' endpoint)"""
        igdb_query = f"""
            fields name, games.name, games.id;
            limit {limit};
            offset {offset};
        """
        return self._post('franchises', igdb_query.strip())
    
    def get_games_by_external_id(self, external_id, category):
        """
        Get game by external ID (Steam, GOG, etc.)
        Category: 1 (Steam), 5 (GOG), 11 (Epic Games Store), etc.
        """
        igdb_query = f"""
            fields game.name, game.id, uid, category;
            where uid = "{external_id}" & category = {category};
        """
        return self._post('external_games', igdb_query.strip())
    
    def get_multiquery(self, queries):
        """Execute multiple queries in a single request"""
        # IGDB multiquery format allows multiple queries separated by specific syntax
        query_body = ""
        for name, query in queries.items():
            query_body += f'query {name} "{query}";\n'
        
        return self._post('multiquery', query_body.strip())
