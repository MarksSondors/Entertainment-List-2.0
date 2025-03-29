# Description: Base class for API services
import requests
import time
import random
from requests.exceptions import ConnectionError, Timeout

class BaseService:
    def __init__(self, base_url, api_key=None, bearer_token=None):
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.base_url = base_url

    def _get(self, endpoint, params=None, max_retries=3):
        # Ensure endpoint doesn't start with a slash to avoid double slashes
        endpoint = endpoint.lstrip('/') 
        url = f'{self.base_url.rstrip("/")}/{endpoint}'
        headers = {}
        if self.bearer_token:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        if self.api_key:
            params = params or {}
            params['api_key'] = self.api_key
        
        # Initialize retry counter
        retries = 0
        
        while retries <= max_retries:
            try:
                response = requests.get(url, params=params, headers=headers, timeout=10)
                response.raise_for_status()  # Raises exception for 4XX/5XX responses
                return response.json()
            except (ConnectionError, Timeout) as e:
                retries += 1
                if retries > max_retries:
                    print(f"Failed after {max_retries} retries: {e}")
                    raise
                
                # Calculate backoff time with jitter (between 1-3s, 2-6s, 4-12s)
                backoff = (2 ** (retries - 1)) * (1 + random.random())
                print(f"Connection error: {e}. Retrying in {backoff:.1f} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                print(f"Request error: {e}")
                raise
