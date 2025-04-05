from api.base import BaseService
from decouple import config
import requests

class MusicService(BaseService):
    def __init__(self):
        self.app_name = config("APP_NAME", default="Entertainment-List-2.0")
        self.app_version = config("APP_VERSION", default="1.0.0")
        self.contact = config("CONTACT_INFO", default="example@example.com")
        
        super().__init__(
            base_url='https://musicbrainz.org/ws/2',
        )
    
    def _get(self, endpoint, params=None, max_retries=3):
        # Override to add required MusicBrainz headers
        endpoint = endpoint.lstrip('/') 
        url = f'{self.base_url.rstrip("/")}/{endpoint}'
        
        # MusicBrainz requires a User-Agent header
        headers = {
            'User-Agent': f'{self.app_name}/{self.app_version} ({self.contact})'
        }
        
        if self.bearer_token:
            headers['Authorization'] = f'Bearer {self.bearer_token}'
        
        params = params or {}
        params['fmt'] = 'json'  # Request JSON instead of default XML
        if self.api_key:
            params['api_key'] = self.api_key
        
        # Implement retry logic from BaseService
        retries = 0
        while retries <= max_retries:
            try:
                response = requests.get(url, params=params, headers=headers, timeout=10)
                response.raise_for_status()
                return response.json()
            except (requests.ConnectionError, requests.Timeout) as e:
                retries += 1
                if retries > max_retries:
                    print(f"Failed after {max_retries} retries: {e}")
                    raise
                
                import time
                import random
                backoff = (2 ** (retries - 1)) * (1 + random.random())
                print(f"Connection error: {e}. Retrying in {backoff:.1f} seconds... (Attempt {retries}/{max_retries})")
                time.sleep(backoff)
            except requests.exceptions.RequestException as e:
                print(f"Request error: {e}")
                raise
    
    def search_artists(self, query, limit=25, offset=0):
        params = {
            'query': query,
            'limit': limit,
            'offset': offset
        }
        return self._get('artist', params=params)
    
    def get_artist_details(self, artist_id):
        return self._get(f'artist/{artist_id}')
    
    def get_artist_releases(self, artist_id, limit=25, offset=0):
        params = {
            'limit': limit,
            'offset': offset,
            'inc': 'releases'  # Include releases in response
        }
        return self._get(f'artist/{artist_id}', params=params)
    
    def search_releases(self, query, limit=25, offset=0):
        params = {
            'query': query,
            'limit': limit,
            'offset': offset
        }
        return self._get('release', params=params)
    
    def get_release_details(self, release_id):
        params = {
            'inc': 'artists+labels+recordings+release-groups'
        }
        return self._get(f'release/{release_id}', params=params)
    
    def search_recordings(self, query, limit=25, offset=0):
        params = {
            'query': query,
            'limit': limit,
            'offset': offset
        }
        return self._get('recording', params=params)
    
    def get_recording_details(self, recording_id):
        params = {
            'inc': 'artists+isrcs'
        }
        return self._get(f'recording/{recording_id}', params=params)
    
    def find_soundtrack_albums(self, movie_title, limit=25, offset=0):
        """Find soundtrack albums related to a movie title"""
        params = {
            'query': f'type:soundtrack AND "{movie_title}"',
            'limit': limit,
            'offset': offset
        }
        return self._get('release-group', params=params)

    def get_artist(self, artist_id):
        """Get artist details from MusicBrainz"""
        return self._get(f'artist/{artist_id}', params={'inc': 'aliases+tags+ratings'})

    def link_album_to_movie(self, musicbrainz_album_id, movie_id, relationship_type='soundtrack'):
        """Create a relationship between an album and a movie in the database
        
        Args:
            musicbrainz_album_id: The MusicBrainz ID of the album
            movie_id: The ID of the movie in our database
            relationship_type: The type of relationship (soundtrack, score, etc.)
            
        Returns:
            The created or updated MovieAlbumRelationship object, or None on failure
        """
        from custom_auth.models import Album, Movie, MovieAlbumRelationship
        from django.db import transaction
        
        try:
            # Find the movie
            try:
                movie = Movie.objects.get(id=movie_id)
            except Movie.DoesNotExist:
                print(f"Movie with ID {movie_id} not found")
                return None
            
            # Check if album exists in our database
            album = None
            try:
                album = Album.objects.get(musicbrainz_id=musicbrainz_album_id)
            except Album.DoesNotExist:
                # Album doesn't exist, let's create it from MusicBrainz data
                album_data = self.get_album_details(musicbrainz_album_id, include_relationships=True)
                if not album_data:
                    print(f"Failed to retrieve data for album ID {musicbrainz_album_id}")
                    return None
                
                with transaction.atomic():
                    # Get or create the primary artist
                    artist_credit = album_data.get('artist-credit', [{}])[0]
                    artist_mb_id = artist_credit.get('artist', {}).get('id')
                    artist_name = artist_credit.get('artist', {}).get('name', 'Unknown Artist')
                    
                    # Use our enhanced person matching
                    primary_artist, _ = self.find_or_create_person(
                        musicbrainz_id=artist_mb_id, 
                        name=artist_name,
                        role_types=['is_musician', 'is_performer']
                    )
                    
                    # Determine album type
                    album_type = 'album'
                    if album_data.get('primary-type'):
                        album_type = album_data['primary-type'].lower()
                    else:
                        # Check for soundtrack in secondary types
                        secondary_types = [t.lower() for t in album_data.get('secondary-types', [])]
                        if 'soundtrack' in secondary_types:
                            album_type = 'soundtrack'
                        elif 'score' in secondary_types:
                            album_type = 'score'
                    
                    # Create the album
                    album = Album.objects.create(
                        title=album_data.get('title', 'Unknown Album'),
                        original_title=album_data.get('title', 'Unknown Album'),
                        description=album_data.get('annotation', ''),
                        primary_artist=primary_artist,
                        release_date=album_data.get('first-release-date'),
                        musicbrainz_id=musicbrainz_album_id,
                        album_type=album_type,
                        total_tracks=len(album_data.get('releases', [])) # Rough estimate of track count
                    )
                    
                    # Add featured artists if available
                    if len(album_data.get('artist-credit', [])) > 1:
                        for credit in album_data.get('artist-credit', [])[1:]:
                            if 'artist' in credit:
                                featured_artist, _ = self.find_or_create_person(
                                    musicbrainz_id=credit['artist'].get('id'),
                                    name=credit['artist'].get('name'),
                                    role_types=['is_musician', 'is_performer']
                                )
                                album.featured_artists.add(featured_artist)
            
            # Now create or update the relationship
            relationship, created = MovieAlbumRelationship.objects.update_or_create(
                movie=movie,
                album=album,
                defaults={'relationship_type': relationship_type}
            )
            
            return relationship
            
        except Exception as e:
            print(f"Error linking album to movie: {str(e)}")
            return None

    def search_albums(self, query, limit=25, offset=0):
        """Search for albums by name, artist, etc."""
        params = {
            'query': query,
            'limit': limit,
            'offset': offset
        }
        return self._get('release-group', params=params)

    def get_album_details(self, album_id, include_relationships=False):
        """Get detailed information about an album from MusicBrainz
        
        Args:
            album_id: MusicBrainz release-group ID
            include_relationships: Whether to include relationship data
            
        Returns:
            Album data from MusicBrainz
        """
        inc_params = ['artists', 'artist-credits', 'releases']
        # Removed both 'release-groups' and 'annotations' as they're not valid
        
        # Add relationship data if requested
        if include_relationships:
            inc_params.extend(['url-rels', 'recording-rels', 'work-rels'])
            
        params = {
            'inc': '+'.join(inc_params)
        }
        
        return self._get(f'release-group/{album_id}', params=params)

    def find_soundtracks_by_imdb_id(self, imdb_id):
        """Find soundtrack albums related to a movie by its IMDb ID
        
        Args:
            imdb_id: The IMDb ID (without the 'tt' prefix if present)
        
        Returns:
            List of soundtrack album data from MusicBrainz
        """
        # Ensure IMDb ID is properly formatted (remove 'tt' prefix if present)
        if imdb_id.startswith('tt'):
            imdb_id = imdb_id[2:]
            
        # Query MusicBrainz for release-groups with this IMDb ID
        params = {
            'query': f'type:soundtrack AND asin:imdb-{imdb_id}',
            'limit': 100
        }
        return self._get('release-group', params=params)

    def extract_imdb_id_from_album(self, album_data):
        """Extract IMDb ID from a MusicBrainz album dataset
        
        Args:
            album_data: Album data from MusicBrainz API
            
        Returns:
            IMDb ID (without 'tt' prefix) or None if not found
        """
        # Check relations for IMDb links
        if 'relations' in album_data:
            for relation in album_data.get('relations', []):
                if relation.get('type') == 'soundtrack' and 'target' in relation:
                    target = relation['target']
                    # Check if it's an IMDb URL
                    if 'imdb.com/title/tt' in target:
                        # Extract IMDb ID
                        import re
                        imdb_match = re.search(r'imdb\.com/title/tt(\d+)', target)
                        if imdb_match:
                            return imdb_match.group(1)
        
        # Also check for IMDb ID in external IDs
        if 'external-ids' in album_data:
            for ext_id in album_data.get('external-ids', []):
                if ext_id.get('type') == 'IMDb' and 'id' in ext_id:
                    imdb_id = ext_id['id']
                    # Remove 'tt' prefix if present
                    if imdb_id.startswith('tt'):
                        imdb_id = imdb_id[2:]
                    return imdb_id
                    
        return None

    def auto_link_soundtrack_to_movie(self, album_mb_id):
        """Find and link a soundtrack album to its corresponding movie automatically
        
        Args:
            album_mb_id: MusicBrainz ID of the soundtrack album
            
        Returns:
            Tuple (success, message, relationship_obj)
        """
        from custom_auth.models import Album, Movie
        
        try:
            # Get detailed album info with relations
            album_data = self.get_album_details(album_mb_id, include_relationships=True)
            if not album_data:
                return False, "Failed to retrieve album data", None
            
            # Extract IMDb ID
            imdb_id = self.extract_imdb_id_from_album(album_data)
            if not imdb_id:
                return False, "No IMDb ID found for this soundtrack", None
            
            # Add 'tt' prefix for database matching
            if not imdb_id.startswith('tt'):
                imdb_id = f"tt{imdb_id}"
                
            # Try to find the movie in our database
            try:
                movie = Movie.objects.get(imdb_id=imdb_id)
            except Movie.DoesNotExist:
                return False, f"No movie with IMDb ID {imdb_id} found in database", None
            
            # Create or get the album in our database
            relationship = self.link_album_to_movie(album_mb_id, movie.id, relationship_type='soundtrack')
            if relationship:
                return True, f"Successfully linked soundtrack to {movie.title}", relationship
            else:
                return False, "Failed to create relationship", None
                
        except Exception as e:
            return False, f"Error: {str(e)}", None

    def find_and_link_all_movie_soundtracks(self, movie_id):
        """Find and link all soundtrack albums for a movie
        
        Args:
            movie_id: Database ID of the movie
            
        Returns:
            Dictionary with results
        """
        from custom_auth.models import Movie
        
        try:
            movie = Movie.objects.get(id=movie_id)
            
            # Get movie IMDb ID - required for this operation
            if not movie.imdb_id:
                return {"success": False, "message": "Movie has no IMDb ID", "linked": 0}
            
            # Strip 'tt' prefix if present
            imdb_id = movie.imdb_id
            if imdb_id.startswith('tt'):
                imdb_id = imdb_id[2:]
            
            # Find soundtracks by IMDb ID
            soundtracks = self.find_soundtracks_by_imdb_id(imdb_id)
            
            if not soundtracks or 'release-groups' not in soundtracks or len(soundtracks['release-groups']) == 0:
                return {"success": False, "message": "No soundtracks found", "linked": 0}
            
            # Link each soundtrack
            linked = 0
            for album in soundtracks['release-groups']:
                try:
                    relationship = self.link_album_to_movie(album['id'], movie.id, relationship_type='soundtrack')
                    if relationship:
                        linked += 1
                except Exception as e:
                    print(f"Error linking album {album.get('title')}: {str(e)}")
                    
            return {
                "success": True, 
                "message": f"Linked {linked} soundtrack albums to {movie.title}", 
                "linked": linked,
                "total": len(soundtracks['release-groups'])
            }
            
        except Movie.DoesNotExist:
            return {"success": False, "message": f"Movie with ID {movie_id} not found", "linked": 0}
        except Exception as e:
            return {"success": False, "message": f"Error: {str(e)}", "linked": 0}

    def find_or_create_person(self, musicbrainz_id=None, name=None, role_types=None):
        """Find or create a Person based on MusicBrainz ID or name
        
        Attempts multiple matching strategies in this order:
        1. By MusicBrainz ID if provided
        2. By exact name match
        3. Create a new Person if no match found
        
        Args:
            musicbrainz_id: MusicBrainz ID
            name: Person's name
            role_types: List of role types to set (e.g., ['is_musician', 'is_performer'])
            
        Returns:
            Person object and a boolean indicating if it was created
        """
        from custom_auth.models import Person
        
        if not name and not musicbrainz_id:
            raise ValueError("Either name or musicbrainz_id must be provided")
            
        role_types = role_types or []
        person = None
        created = False
        
        # Try finding by MusicBrainz ID first
        if musicbrainz_id:
            person = Person.objects.filter(musicbrainz_id=musicbrainz_id).first()
            
        # If not found by MusicBrainz ID, try by name
        if not person and name:
            person = Person.objects.filter(name=name).first()
            
        # If still not found, create a new Person
        if not person:
            person_data = {'name': name or 'Unknown Artist'}
            
            # Add MusicBrainz ID if available
            if musicbrainz_id:
                person_data['musicbrainz_id'] = musicbrainz_id
                
            # Set role flags
            for role in role_types:
                if hasattr(Person, role):
                    person_data[role] = True
                    
            person = Person.objects.create(**person_data)
            created = True
        else:
            # Update existing person with MusicBrainz ID if missing
            if musicbrainz_id and not person.musicbrainz_id:
                person.musicbrainz_id = musicbrainz_id
                
            # Update role flags
            update_fields = []
            for role in role_types:
                if hasattr(Person, role) and not getattr(person, role):
                    setattr(person, role, True)
                    update_fields.append(role)
                    
            if musicbrainz_id and not person.musicbrainz_id:
                update_fields.append('musicbrainz_id')
                
            if update_fields:
                person.save(update_fields=update_fields)
        
        return person, created