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
    
    def get_movie_details(self, movie_id):
        return self._get(f'movie/{movie_id}')
    
    def get_movie_credits(self, movie_id):
        return self._get(f'movie/{movie_id}/credits')
    
    def get_movie_keywords(self, movie_id):
        return self._get(f'movie/{movie_id}/keywords')
    
    def get_similar_movies(self, movie_id):
        return self._get(f'movie/{movie_id}/similar')
    
    def get_movie_images(self, movie_id):
        return self._get(f'movie/{movie_id}/images')