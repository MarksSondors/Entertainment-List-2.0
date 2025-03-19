# Description: Base class for API services
import requests

class BaseService:
    def __init__(self, api_key, base_url, bearer_token=None):
        self.api_key = api_key
        self.base_url = base_url
        self.bearer_token = bearer_token  # Optional bearer token for authorization

    def _get(self, endpoint, params=None, headers=None, use_bearer=False):
        """
        Makes a GET request to the specified endpoint.
        :param endpoint: API endpoint (relative to the base URL)
        :param params: Dictionary of query parameters
        :param headers: Dictionary of custom headers
        :param use_bearer: Whether to use Bearer token for authorization
        :return: Response JSON or raises an exception for HTTP errors
        """
        if params is None:
            params = {}
        if headers is None:
            headers = {}
        url = f"{self.base_url}{endpoint}"
        if self.bearer_token:
            headers['Authorization'] = f"Bearer {self.bearer_token}"
        else:
            params['api_key'] = self.api_key  # Add the API key to the parameters
        headers['accept'] = 'application/json'
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
