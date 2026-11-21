# Description: Base class for API services
import logging
import random
import time

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, Timeout

logger = logging.getLogger(__name__)


def _build_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session


class BaseService:
    _session = _build_session()

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
            params = dict(params) if params else {}
            params['api_key'] = self.api_key
        
        # Initialize retry counter
        retries = 0
        
        while retries <= max_retries:
            try:
                response = self._session.get(url, params=params, headers=headers, timeout=10)
                response.raise_for_status()  # Raises exception for 4XX/5XX responses
                return response.json()
            except (ConnectionError, Timeout) as e:
                retries += 1
                if retries > max_retries:
                    logger.warning("GET %s failed after %d retries: %s", url, max_retries, e)
                    raise
                
                # Calculate backoff time with jitter (between 1-3s, 2-6s, 4-12s)
                backoff = (2 ** (retries - 1)) * (1 + random.random())
                logger.info("GET %s connection error (%s); retrying in %.1fs (%d/%d)", url, e, backoff, retries, max_retries)
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                logger.warning("GET %s request error: %s", url, e)
                raise

    def _post(self, endpoint, data=None, max_retries=3):
        # Ensure endpoint doesn't start with a slash to avoid double slashes
        endpoint = endpoint.lstrip('/') 
        url = f'{self.base_url.rstrip("/")}/{endpoint}'
        headers = {'Content-Type': 'application/json'}
        
        if self.bearer_token:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        if self.api_key:
            headers['api_key'] = self.api_key
        
        # Initialize retry counter
        retries = 0
        
        while retries <= max_retries:
            try:
                response = self._session.post(url, json=data, headers=headers, timeout=10)
                response.raise_for_status()  # Raises exception for 4XX/5XX responses
                return response.json()
            except (ConnectionError, Timeout) as e:
                retries += 1
                if retries > max_retries:
                    logger.warning("POST %s failed after %d retries: %s", url, max_retries, e)
                    raise
                
                # Calculate backoff time with jitter (between 1-3s, 2-6s, 4-12s)
                backoff = (2 ** (retries - 1)) * (1 + random.random())
                logger.info("POST %s connection error (%s); retrying in %.1fs (%d/%d)", url, e, backoff, retries, max_retries)
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                logger.warning("POST %s request error: %s", url, e)
                raise
