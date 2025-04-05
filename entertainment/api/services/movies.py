# tmdb
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


class MoviesService(BaseService):
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

    def get_popular_movies(self):
        return self._get('movie/popular')
    
    def search_movies(self, query, include_adult=False, language='en-US', page=1, primary_release_year=None, region='US', year=None):
        params = {
            'query': query,
            'include_adult': include_adult,
            'language': language,
            'page': page,
            'primary_release_year': primary_release_year,
            'region': region,
            'year': year
        }
        params = {key: value for key, value in params.items() if value is not None}
        return self._get('search/movie', params=params)
    
    def get_movie_details(self, movie_id, append_to_response=None):
        params = {}
        if append_to_response:
            params['append_to_response'] = append_to_response
        return self._get(f'movie/{movie_id}', params=params)
    
    def get_movie_credits(self, movie_id):
        return self._get(f'movie/{movie_id}/credits')
    
    def get_movie_keywords(self, movie_id):
        return self._get(f'movie/{movie_id}/keywords')
    
    def get_similar_movies(self, movie_id):
        return self._get(f'movie/{movie_id}/similar')
    
    def get_movie_images(self, movie_id):
        return self._get(f'movie/{movie_id}/images')
    
    def get_person_details(self, person_id):
        return self._get(f'person/{person_id}')
    
    def get_movie_details_with_imdb_id(self, imdb_id):
        # First, search for the movie using the IMDb ID
        # Use the find endpoint with the IMDB ID
        search_results = self._get(f'find/{imdb_id}', params={'external_source': 'imdb_id'})
        
        # Check if any results were found
        if search_results and 'results' in search_results and len(search_results['results']) > 0:
            movie_id = search_results['results'][0]['id']
            return self.get_movie_details(movie_id)
        
        return None
    
    def get_collection_details(self, collection_id):
        return self._get(f'collection/{collection_id}')