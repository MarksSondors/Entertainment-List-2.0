import time
import threading
from collections import deque
from api.base import BaseService
from decouple import config


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


class TVShowsService(BaseService):
    def __init__(self):
        super().__init__(
            base_url='https://api.themoviedb.org/3',
            bearer_token=config("TMDB_BEARER_TOKEN")
        )
        # Initialize the rate limiter with 40 calls per second
        self.rate_limiter = RateLimiter(max_calls=40)
    
    # Override the _get method to add rate limiting
    def _get(self, endpoint, params=None):
        # Wait if we need to respect the rate limit
        self.rate_limiter.wait_if_needed()
        # Call the parent's _get method
        return super()._get(endpoint, params)
    
    def get_popular_shows(self):
        """
        Get trending TV shows
        """
        response = self._get('trending/tv/day')
        return response
    
    def search_shows(self, query, include_adult=False, language='en-US', page=1, first_air_date_year=None, region='US'):
        params = {
            'query': query,
            'include_adult': include_adult,
            'language': language,
            'page': page,
            'first_air_date_year': first_air_date_year,
            'region': region
        }
        params = {key: value for key, value in params.items() if value is not None}
        return self._get('search/tv', params=params)
    
    def get_show_details(self, show_id, append_to_response=None):
        params = {}
        if append_to_response:
            params['append_to_response'] = append_to_response
        return self._get(f'tv/{show_id}', params=params)
    
    def get_show_credits(self, show_id):
        return self._get(f'tv/{show_id}/aggregate_credits')
    
    def get_show_images(self, show_id):
        return self._get(f'tv/{show_id}/images')
    
    def get_similar_shows(self, show_id):
        return self._get(f'tv/{show_id}/similar')
    
    def get_show_seasons(self, show_id):
        """Get all seasons for a TV show"""
        show_data = self._get(f'tv/{show_id}')
        return show_data.get('seasons', [])
    
    def get_season_details(self, show_id, season_number):
        """Get detailed information for a specific season"""
        return self._get(f'tv/{show_id}/season/{season_number}')
    
    def get_episode_details(self, show_id, season_number, episode_number):
        """Get detailed information for a specific episode"""
        return self._get(f'tv/{show_id}/season/{season_number}/episode/{episode_number}')
    
    def get_latest_episode(self, show_id):
        """Get information about the latest episode of a TV show"""
        show_data = self._get(f'tv/{show_id}')
        if 'last_episode_to_air' in show_data:
            return show_data['last_episode_to_air']
        return None
    
    def get_next_episode(self, show_id):
        """Get information about the next episode of a TV show (if available)"""
        show_data = self._get(f'tv/{show_id}')
        if 'next_episode_to_air' in show_data:
            return show_data['next_episode_to_air']
        return None
    
    def is_show_running(self, show_id):
        """Check if a show is still running"""
        show_data = self._get(f'tv/{show_id}')
        return show_data.get('status') == 'Returning Series'
    
    def get_show_changes(self, show_id, start_date=None, end_date=None):
        """Get changes for a TV show within a date range"""
        params = {}
        if start_date:
            params['start_date'] = start_date
        if end_date:
            params['end_date'] = end_date
        return self._get(f'tv/{show_id}/changes', params=params)