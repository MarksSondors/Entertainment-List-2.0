# tmdb
from api.base import BaseService
from decouple import config


class MoviesService(BaseService):
    def __init__(self):
        super().__init__(
            base_url='https://api.themoviedb.org/3',
            bearer_token=config("TMDB_BEARER_TOKEN")
        )

    def get_popular_movies(self):
        return self._get('/movie/popular', use_bearer=True)
    
    def search_movies(self, query):
        return self._get('/search/movie', params={'query': query}, use_bearer=True)
    
    def get_movie_details(self, movie_id):
        return self._get(f'/movie/{movie_id}', use_bearer=True)
    
    def get_movie_credits(self, movie_id):
        return self._get(f'/movie/{movie_id}/credits', use_bearer=True)