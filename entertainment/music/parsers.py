from api.services.music import MusicService
from .models import *
import re
from api.services.movies import MoviesService
from movies.parsers import create_movie
from tvshows.parsers import create_tvshow
from django.contrib.contenttypes.models import ContentType

def create_album_from_musicbrainz(musicbrainz_id):
    """
    Fetch album data from MusicBrainz and create a local album record
    """
    music_service = MusicService()
    
    try:
        # Get album details
        album_data = music_service.get_album_details(musicbrainz_id)

        print(f"Album data: {album_data}")
        
        if not album_data:
            return None
            
        # Extract basic details
        title = album_data.get('title', 'Unknown Album')
        original_title = album_data.get('title', title)
        description = album_data.get('annotation', '')
        
        # Extract artist information
        artist_name = 'Unknown Artist'
        artist_mbid = None
        
        if 'artist-credit' in album_data and album_data['artist-credit']:
            artist_credit = album_data['artist-credit'][0]
            artist_name = artist_credit.get('name', 'Unknown Artist')
            
            # If there's an artist object, get its MusicBrainz ID
            if 'artist' in artist_credit:
                artist_mbid = artist_credit['artist'].get('id')
        
        # Get or create the primary artist
        if artist_mbid:
            # Get full artist details to check for IMDb ID
            try:
                artist_data = music_service._get(f'artist/{artist_mbid}', params={'inc': 'url-rels'})
                imdb_id = None
                
                # Extract IMDb ID from relations if it exists
                if 'relations' in artist_data:
                    for relation in artist_data.get('relations', []):
                        if relation.get('type') == 'IMDb' and 'url' in relation:
                            url = relation['url'].get('resource', '')
                            # Extract IMDb ID from URL (nm0000123 format)
                            match = re.search(r'/(nm\d+)', url)
                            if match:
                                imdb_id = match.group(1)
                                break
                
                # Create or update artist with IMDb ID if found
                defaults = {
                    'name': artist_name,
                    'is_musician': True
                }
                
                if imdb_id:
                    defaults['imdb_id'] = imdb_id
                    
                artist, created = Person.objects.get_or_create(
                    musicbrainz_id=artist_mbid,
                    defaults=defaults
                )
                
                # If artist exists but doesn't have IMDb ID, update it
                if not created and imdb_id and not artist.imdb_id:
                    artist.imdb_id = imdb_id
                    artist.save()
                    
            except Exception as e:
                print(f"Error fetching artist details: {str(e)}")
                # Fallback to basic creation if fetching additional details fails
                artist, created = Person.objects.get_or_create(
                    musicbrainz_id=artist_mbid,
                    defaults={
                        'name': artist_name,
                        'is_musician': True
                    }
                )
        else:
            # Create artist without MusicBrainz ID if not found
            artist, created = Person.objects.get_or_create(
                name=artist_name,
                defaults={'is_musician': True}
            )
        
        # Extract release date
        release_date = album_data.get('first-release-date')
        
        # Determine album type
        album_type = 'album'
        if 'primary-type' in album_data:
            mb_type = album_data['primary-type'].lower()
            if mb_type == 'album':
                album_type = 'album'
            elif mb_type == 'single':
                album_type = 'single'
            elif mb_type == 'ep':
                album_type = 'ep'
            elif mb_type == 'soundtrack':
                album_type = 'soundtrack'
        
        # Check if album title suggests it's a soundtrack
        if 'soundtrack' in title.lower() or 'ost' in title.lower() or 'music from' in title.lower():
            album_type = 'soundtrack'
            # If soundtrack, check if we can link it with a movie/TV show, if we find imdb_id, lets try to find the movie/tvshow in the database, if  we will create an entry for it
            # check external links for imdb id for the album
            if 'external-links' in album_data:
                for link in album_data['external-links']:
                    if link.get('type') == 'IMDb':
                        url = link.get('url', '')
                        # Extract IMDb ID from URL (tt1234567 format)
                        match = re.search(r'/(tt\d+)', url)
                        if match:
                            imdb_id = match.group(1)
                            break
                        # If we find the imdb_id, lets try to find the movie/tvshow in the database, if we will create an entry for it
                        if imdb_id:
                            # check if the movie/tvshow is already in the database
                            try:
                                movie = movie.objects.get(imdb_id=imdb_id)
                            except movie.DoesNotExist:
                                try:
                                    tvshow = tvshow.objects.get(imdb_id=imdb_id)
                                except tvshow.DoesNotExist:
                                    # Use find_by_external_id to get correct media type
                                    movie_service = MoviesService()
                                    find_results = movie_service.find_by_external_id(imdb_id)
                                    
                                    if find_results:
                                        if find_results.get('movie_results'):
                                            item = find_results['movie_results'][0]
                                            entity = create_movie(item['id'])
                                            
                                        elif find_results.get('tv_results'):
                                            item = find_results['tv_results'][0]
                                            entity = create_tvshow(item['id'])
                                            
                                    # Original code was:
                                    # movie = movie_service.get_movie_details_with_imdb_id(imdb_id)
                                    # if movie.get('media_type') == 'movie': create_movie(movie)

                        if entity:
                            media_type = ContentType.objects.get_for_model(entity)
                            # Store the entity info to create relationship after album is created
                            media_entity = {
                                'content_type': media_type,
                                'object_id': entity.id,
                                'entity': entity
                            }
                            

        
        # Try to get album cover
        cover_image = None
        try:
            # We'll just set the URL structure for Cover Art Archive, actual image will be fetched when needed
            cover_url = f"https://coverartarchive.org/release-group/{musicbrainz_id}/front-500"
        except:
            cover_url = None
            
        # Create the album
        album = Album.objects.create(
            title=title,
            original_title=original_title,
            description=description,
            musicbrainz_id=musicbrainz_id,
            primary_artist=artist,
            album_type=album_type,
            cover=cover_url,
        )
        
        # Try to parse release date if available
        if release_date:
            from datetime import datetime
            try:
                # Handle different date formats (YYYY, YYYY-MM, YYYY-MM-DD)
                parts = release_date.split('-')
                if len(parts) == 1:  # Just year
                    album.release_date = datetime(int(parts[0]), 1, 1).date()
                elif len(parts) == 2:  # Year and month
                    album.release_date = datetime(int(parts[0]), int(parts[1]), 1).date()
                elif len(parts) == 3:  # Full date
                    album.release_date = datetime(int(parts[0]), int(parts[1]), int(parts[2])).date()
                album.save()
            except:
                pass
        
        # Create relationship if we found a related movie/TV show
        if 'media_entity' in locals() and media_entity:
            from django.contrib.contenttypes.models import ContentType
            MediaAlbumRelationship.objects.create(
                content_type=media_entity['content_type'],
                object_id=media_entity['object_id'],
                album=album,
                relationship_type='soundtrack'
            )
        
        return album
        
    except Exception as e:
        print(f"Error creating album: {str(e)}")
        return None