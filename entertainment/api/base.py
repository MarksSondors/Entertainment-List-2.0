# Description: Base class for API services
import requests

class BaseService:
    def __init__(self, base_url, api_key=None, bearer_token=None):
        self.api_key = api_key
        self.bearer_token = bearer_token
        self.base_url = base_url

    def _get(self, endpoint, params=None):
        url = f'{self.base_url}/{endpoint}'
        headers = {}
        if self.bearer_token:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        if self.api_key:
            params = params or {}
            params['api_key'] = self.api_key
        print(url)
        response = requests.get(url, params=params, headers=headers)
        return response.json()
