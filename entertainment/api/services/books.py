# filepath: c:\Users\Marks Sondors\Desktop\Personal projects\Entertainment-List-2.0\entertainment\api\services\books.py
import time
import threading
from collections import deque
import requests
from decouple import config


class RateLimiter:
    """Rate limiter to control the number of requests per minute for Hardcover API"""
    def __init__(self, max_calls_per_minute=60):
        self.max_calls = max_calls_per_minute
        self.period = 60.0  # 60 seconds
        self.calls = deque()
        self.lock = threading.RLock()

    def wait_if_needed(self):
        """Wait if rate limit has been reached (60 requests per minute)"""
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


class BooksService:
    """
    Hardcover API service for books data
    Uses GraphQL API: https://api.hardcover.app/v1/graphql
    Documentation: https://docs.hardcover.app/api/getting-started/
    """
    
    def __init__(self):
        self.base_url = 'https://api.hardcover.app/v1/graphql'
        self.api_token = config("HARDCOVER_API_TOKEN", default=None)
        self.app_name = config("APP_NAME", default="Entertainment-List-2.0")
        self.app_version = config("APP_VERSION", default="1.0.0")
        self.contact = config("CONTACT_INFO", default="example@example.com")
        
        # Rate limiter: 60 requests per minute
        self.rate_limiter = RateLimiter(max_calls_per_minute=60)
        
        if not self.api_token:
            print("Warning: HARDCOVER_API_TOKEN not found in environment variables")
    
    def _get_headers(self):
        """Get headers for Hardcover API requests"""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': f'{self.app_name}/{self.app_version} ({self.contact})'
        }
        
        if self.api_token:
            headers['Authorization'] = self.api_token
            
        return headers
    
    def _make_graphql_request(self, query, variables=None, max_retries=3):
        """Make a GraphQL request to Hardcover API"""
        if not self.api_token:
            raise ValueError("HARDCOVER_API_TOKEN is required")
        
        # Apply rate limiting
        self.rate_limiter.wait_if_needed()
        
        payload = {
            'query': query
        }
        
        if variables:
            payload['variables'] = variables
        
        retries = 0
        while retries <= max_retries:
            try:
                response = requests.post(
                    self.base_url,
                    json=payload,
                    headers=self._get_headers(),
                    timeout=30  # Max timeout as per API docs
                )
                
                response.raise_for_status()
                data = response.json()
                
                # Check for GraphQL errors
                if 'errors' in data:
                    raise Exception(f"GraphQL errors: {data['errors']}")
                
                return data.get('data', {})
                
            except requests.exceptions.RequestException as e:
                retries += 1
                if retries > max_retries:
                    print(f"Failed after {max_retries} retries: {e}")
                    raise
                
                import random
                backoff = (2 ** (retries - 1)) * (1 + random.random())
                print(f"Request error: {e}. Retrying in {backoff:.1f} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(backoff)
    
    def search_books(self, query, limit=25, offset=0):
        """Search for books"""
        search_query = """
        query SearchBooks($query: String!, $limit: Int, $offset: Int) {
            books(where: {_or: [
                {title: {_ilike: $query}},
                {authors: {name: {_ilike: $query}}},
                {isbn_10: {_eq: $query}},
                {isbn_13: {_eq: $query}}
            ]}, limit: $limit, offset: $offset) {
                id
                title
                subtitle
                description
                isbn_10
                isbn_13
                pages
                published_date
                language
                image_url
                authors {
                    id
                    name
                }
                genres {
                    id
                    name
                }
            }
        }
        """
        variables = {
            'query': f'%{query}%',
            'limit': limit,
            'offset': offset
        }
        return self._make_graphql_request(search_query, variables)
    
    def get_book_details(self, book_id):
        """Get detailed information about a specific book"""
        query = """
        query GetBookDetails($book_id: uuid!) {
            books_by_pk(id: $book_id) {
                id
                title
                subtitle
                description
                isbn_10
                isbn_13
                pages
                published_date
                language
                image_url
                authors {
                    id
                    name
                    bio
                }
                genres {
                    id
                    name
                }
                publishers {
                    id
                    name
                }
                series {
                    id
                    name
                    position
                }
            }
        }
        """
        variables = {'book_id': book_id}
        return self._make_graphql_request(query, variables)
    
    def get_popular_books(self, limit=25, offset=0):
        """Get popular books"""
        query = """
        query GetPopularBooks($limit: Int, $offset: Int) {
            books(
                order_by: {user_books_aggregate: {count: desc}},
                limit: $limit,
                offset: $offset
            ) {
                id
                title
                subtitle
                description
                isbn_10
                isbn_13
                pages
                published_date
                language
                image_url
                authors {
                    id
                    name
                }
                genres {
                    id
                    name
                }
            }
        }
        """
        variables = {
            'limit': limit,
            'offset': offset
        }
        return self._make_graphql_request(query, variables)
    
    def get_recently_added_books(self, limit=25, offset=0):
        """Get recently added books"""
        query = """
        query GetRecentlyAddedBooks($limit: Int, $offset: Int) {
            books(
                order_by: {created_at: desc},
                limit: $limit,
                offset: $offset
            ) {
                id
                title
                subtitle
                description
                isbn_10
                isbn_13
                pages
                published_date
                language
                image_url
                created_at
                authors {
                    id
                    name
                }
                genres {
                    id
                    name
                }
            }
        }
        """
        variables = {
            'limit': limit,
            'offset': offset
        }
        return self._make_graphql_request(query, variables)
    
    def get_books_by_author(self, author_id, limit=25, offset=0):
        """Get books by a specific author"""
        query = """
        query GetBooksByAuthor($author_id: uuid!, $limit: Int, $offset: Int) {
            books(
                where: {authors: {id: {_eq: $author_id}}},
                limit: $limit,
                offset: $offset
            ) {
                id
                title
                subtitle
                description
                isbn_10
                isbn_13
                pages
                published_date
                language
                image_url
                authors {
                    id
                    name
                }
                genres {
                    id
                    name
                }
            }
        }
        """
        variables = {
            'author_id': author_id,
            'limit': limit,
            'offset': offset
        }
        return self._make_graphql_request(query, variables)
    
    def get_books_by_genre(self, genre_id, limit=25, offset=0):
        """Get books by a specific genre"""
        query = """
        query GetBooksByGenre($genre_id: uuid!, $limit: Int, $offset: Int) {
            books(
                where: {genres: {id: {_eq: $genre_id}}},
                limit: $limit,
                offset: $offset
            ) {
                id
                title
                subtitle
                description
                isbn_10
                isbn_13
                pages
                published_date
                language
                image_url
                authors {
                    id
                    name
                }
                genres {
                    id
                    name
                }
            }
        }
        """
        variables = {
            'genre_id': genre_id,
            'limit': limit,
            'offset': offset
        }
        return self._make_graphql_request(query, variables)


# Convenience function for easy usage
def get_books_service():
    """Get an instance of the BooksService"""
    return BooksService()


# Simplified search function
def search_books(query, limit=10):
    """Search for books by title, author, or ISBN"""
    service = BooksService()
    results = service.search_books(query, limit=limit)
    
    if results and 'books' in results:
        return results['books']
    return []