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
    
    def get_movie_details(self, movie_id, append_to_response=None):
        params = {}
        if append_to_response:
            params['append_to_response'] = append_to_response
        return self._get(f'movie/{movie_id}', params=params)
    
    def get_movie_credits(self, movie_id):
        return self._get(f'movie/{movie_id}/credits')
    
    def get_movie_keywords(self, movie_id):
        return self._get(f'movie/{movie_id}/keywords')
    
    def get_similar_movies(self, movie_id):
        return self._get(f'movie/{movie_id}/similar')
    
    def get_movie_images(self, movie_id):
        return self._get(f'movie/{movie_id}/images')
    
    def get_person_details(self, person_id):
        return self._get(f'person/{person_id}')
    
    def get_movie_details_with_imdb_id(self, imdb_id):
        # First, search for the movie using the IMDb ID
        # Use the find endpoint with the IMDB ID
        search_results = self._get(f'find/{imdb_id}', params={'external_source': 'imdb_id'})
        
        # Check if any results were found
        if search_results and 'results' in search_results and len(search_results['results']) > 0:
            movie_id = search_results['results'][0]['id']
            return self.get_movie_details(movie_id)
        
        return None