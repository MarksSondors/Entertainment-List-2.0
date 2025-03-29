# Description: Base class for API services
import requests

class BaseService:
    def __init__(self, base_url, api_key=None, bearer_token=None):
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.base_url = base_url

    def _get(self, endpoint, params=None):
        # Ensure endpoint doesn't start with a slash to avoid double slashes
        endpoint = endpoint.lstrip('/') 
        url = f'{self.base_url.rstrip("/")}/{endpoint}'
        headers = {}
        if self.bearer_token:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        if self.api_key:
            params = params or {}
            params['api_key'] = self.api_key
        print(url)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()  # Raises exception for 4XX/5XX responses
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}")
            raise
