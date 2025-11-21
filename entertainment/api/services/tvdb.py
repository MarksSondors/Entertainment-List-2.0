import time
from api.base import BaseService
from decouple import config

class TVDBService(BaseService):
    def __init__(self):
        super().__init__(
            base_url='https://api4.thetvdb.com/v4',
            api_key=config("TVDB_API_KEY", default=None)
        )
        self.token = None
        self.token_expiry = 0

    def login(self):
        """Login to TVDB API to get a bearer token"""
        if not self.api_key:
            print("TVDB_API_KEY not found in environment variables.")
            return None

        # Check if we have a valid token
        if self.token and time.time() < self.token_expiry:
            return self.token

        try:
            response = self._post('login', data={'apikey': self.api_key})
            if response and 'data' in response:
                self.token = response['data']['token']
                # Token is valid for 1 month, but let's refresh it sooner or just re-login if needed.
                # For simplicity, let's set expiry to 24 hours from now.
                self.token_expiry = time.time() + 86400 
                self.bearer_token = self.token # Update the bearer token in the base class
                return self.token
        except Exception as e:
            print(f"Failed to login to TVDB: {e}")
            return None

    def _ensure_token(self):
        """Ensure we have a valid token before making a request"""
        if not self.token or time.time() >= self.token_expiry:
            self.login()

    def _get(self, endpoint, params=None):
        self._ensure_token()
        if not self.token:
            # If login failed, we can't make requests.
            # Depending on how strict we want to be, we could raise an error or return None.
            # BaseService._get might fail if no token is present but we set it in login.
            pass
        return super()._get(endpoint, params)

    def get_series_extended(self, tvdb_id):
        """
        Get extended series information including episodes.
        """
        if not tvdb_id:
            return None
        
        # endpoint: /series/{id}/extended
        # meta=episodes to get episodes
        params = {'meta': 'episodes'}
        return self._get(f'series/{tvdb_id}/extended', params=params)

    def get_episode_details(self, episode_id):
        """Get details for a specific episode"""
        return self._get(f'episodes/{episode_id}')
