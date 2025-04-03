from api.base import BaseService
from decouple import config


class TVShowsService(BaseService):
    def __init__(self):
        super().__init__(
            base_url='https://api.themoviedb.org/3',
            bearer_token=config("TMDB_BEARER_TOKEN")
        )

    def get_popular_shows(self, exclude_genres=None):
        """
        Get popular TV shows, excluding certain genres like talk shows and other live media formats.
        
        Uses the discover/tv endpoint with filtering to exclude specified genres directly in the API call.
        """
        # Default genres to exclude if none provided
        if exclude_genres is None:
            exclude_genres = [10767, 10763, 10764, 10766]  # Talk, News, Reality, Soap
        
        params = {
            'sort_by': 'popularity.desc',
            'without_genres': ','.join(str(genre_id) for genre_id in exclude_genres),
            'include_adult': False,
            'language': 'en-US',
            'page': 1
        }
        
        response = self._get('discover/tv', params=params)
            
        return response
    
    def search_shows(self, query, include_adult=False, language='en-US', page=1, first_air_date_year=None, region='US'):
        params = {
            'query': query,
            'include_adult': include_adult,
            'language': language,
            'page': page,
            'first_air_date_year': first_air_date_year,
            'region': region
        }
        params = {key: value for key, value in params.items() if value is not None}
        return self._get('search/tv', params=params)
    
    def get_show_details(self, show_id, append_to_response=None):
        params = {}
        if append_to_response:
            params['append_to_response'] = append_to_response
        return self._get(f'tv/{show_id}', params=params)
    
    def get_show_credits(self, show_id):
        return self._get(f'tv/{show_id}/aggregate_credits')
    
    def get_show_images(self, show_id):
        return self._get(f'tv/{show_id}/images')
    
    def get_similar_shows(self, show_id):
        return self._get(f'tv/{show_id}/similar')
    
    def get_show_seasons(self, show_id):
        """Get all seasons for a TV show"""
        show_data = self._get(f'tv/{show_id}')
        return show_data.get('seasons', [])
    
    def get_season_details(self, show_id, season_number):
        """Get detailed information for a specific season"""
        return self._get(f'tv/{show_id}/season/{season_number}')
    
    def get_episode_details(self, show_id, season_number, episode_number):
        """Get detailed information for a specific episode"""
        return self._get(f'tv/{show_id}/season/{season_number}/episode/{episode_number}')
    
    def get_latest_episode(self, show_id):
        """Get information about the latest episode of a TV show"""
        show_data = self._get(f'tv/{show_id}')
        if 'last_episode_to_air' in show_data:
            return show_data['last_episode_to_air']
        return None
    
    def get_next_episode(self, show_id):
        """Get information about the next episode of a TV show (if available)"""
        show_data = self._get(f'tv/{show_id}')
        if 'next_episode_to_air' in show_data:
            return show_data['next_episode_to_air']
        return None
    
    def is_show_running(self, show_id):
        """Check if a show is still running"""
        show_data = self._get(f'tv/{show_id}')
        return show_data.get('status') == 'Returning Series'
    
    def get_show_changes(self, show_id, start_date=None, end_date=None):
        """Get changes for a TV show within a date range"""
        params = {}
        if start_date:
            params['start_date'] = start_date
        if end_date:
            params['end_date'] = end_date
        return self._get(f'tv/{show_id}/changes', params=params)