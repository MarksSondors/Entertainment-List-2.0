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
            headers['Authorization'] = f'Bearer {self.api_token}'
            
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
    
    # ---------------------------------------------------------------------------
    # Book fragments (reused across queries)
    # ---------------------------------------------------------------------------
    _BOOK_FIELDS = """
        id
        title
        subtitle
        description
        slug
        rating
        ratings_count
        release_date
        release_year
        compilation
        cached_image
        contributions(where: {contributable_type: {_eq: "Book"}}) {
            contribution
            author {
                id
                name
                bio
                born_date
                death_date
                cached_image
                identifiers
                links
                alternate_names
            }
        }
        book_series {
            position
            featured
            series {
                id
                name
            }
        }
        default_physical_edition {
            isbn_10
            isbn_13
            pages
            language {
                language
            }
            publisher {
                id
                name
            }
        }
        taggings {
            tag { id tag tag_category_id }
        }
    """

    def search_books(self, query, per_page=25, page=1):
        """
        Search for books using Hardcover's Typesense-backed search endpoint.
        Returns a dict with 'results' (Typesense hits JSON) and 'ids' (list of book IDs).
        """
        search_query = """
        query SearchBooks($query: String!, $per_page: Int, $page: Int) {
            search(
                query: $query,
                query_type: "Book",
                per_page: $per_page,
                page: $page
            ) {
                results
                ids
            }
        }
        """
        variables = {'query': query, 'per_page': per_page, 'page': page}
        return self._make_graphql_request(search_query, variables)

    def get_books_by_ids(self, ids):
        """Fetch full book details for a list of integer IDs (used after search)."""
        if not ids:
            return []
        query = """
        query GetBooksByIds($ids: [Int!]!) {
            books(where: {id: {_in: $ids}}) {
                """ + self._BOOK_FIELDS + """
            }
        }
        """
        result = self._make_graphql_request(query, {'ids': ids})
        return result.get('books', [])

    def get_book_details(self, book_id):
        """Get detailed information about a specific book by its integer ID."""
        query = """
        query GetBook($id: Int!) {
            books(where: {id: {_eq: $id}}, limit: 1) {
                """ + self._BOOK_FIELDS + """
            }
        }
        """
        result = self._make_graphql_request(query, {'id': book_id})
        books = result.get('books', [])
        return books[0] if books else None

    def get_popular_books(self, limit=25, offset=0):
        """Get popular books ordered by user count."""
        query = """
        query GetPopularBooks($limit: Int, $offset: Int) {
            books(
                order_by: {users_count: desc},
                limit: $limit,
                offset: $offset
            ) {
                """ + self._BOOK_FIELDS + """
            }
        }
        """
        result = self._make_graphql_request(query, {'limit': limit, 'offset': offset})
        return result.get('books', [])

    def get_books_by_author(self, author_id, limit=25, offset=0):
        """Get books by a specific author (integer author ID)."""
        query = """
        query GetBooksByAuthor($author_id: Int!, $limit: Int, $offset: Int) {
            books(
                where: {contributions: {author_id: {_eq: $author_id}}},
                limit: $limit,
                offset: $offset,
                order_by: {users_count: desc}
            ) {
                """ + self._BOOK_FIELDS + """
            }
        }
        """
        variables = {
            'author_id': author_id,
            'limit': limit,
            'offset': offset
        }
        return self._make_graphql_request(query, variables)

    def get_series_books(self, hardcover_series_id):
        """Get all books in a series ordered by position.
        featured=true  → this series is the book's primary series (not a side-association)
        compilation=false → skip box-set / omnibus entries (shown as "Collections" on Hardcover)
        """
        query = """
        query GetSeriesBooks($series_id: Int!) {
            book_series(
                where: {
                    series_id: {_eq: $series_id},
                    featured: {_eq: true},
                    compilation: {_eq: false}
                },
                order_by: {position: asc}
            ) {
                position
                book {
                    id
                    title
                    subtitle
                    rating
                    ratings_count
                    release_year
                    cached_image
                    contributions(where: {contributable_type: {_eq: "Book"}}, limit: 3) {
                        author { name }
                    }
                }
            }
        }
        """
        result = self._make_graphql_request(query, {'series_id': hardcover_series_id})
        return result.get('book_series', [])

    def get_series_info(self, hardcover_series_id):
        """Fetch metadata (name, description, is_completed) for a single series."""
        query = """
        query GetSeriesInfo($series_id: Int!) {
            series(where: {id: {_eq: $series_id}}, limit: 1) {
                id
                name
                description
                is_completed
                primary_books_count
            }
        }
        """
        result = self._make_graphql_request(query, {'series_id': hardcover_series_id})
        rows = result.get('series', [])
        return rows[0] if rows else None