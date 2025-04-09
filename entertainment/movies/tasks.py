import logging
from datetime import date
from django.conf import settings
from django_q.models import Schedule
from django_q.tasks import async_task, schedule

from api.services.movies import MoviesService

from movies.models import *


logger = logging.getLogger(__name__)

def update_unreleased_movies():
    """Update information for all movies that haven't been released yet."""
    today = date.today()
    
    # Get all unreleased movies (either explicitly marked as unreleased or with future release date)
    unreleased_movies = Movie.objects.filter(
        status__in=['In Production', 'Post Production', 'Planned'],
    ) | Movie.objects.filter(release_date__gt=today)
    
    # Log how many movies will be updated
    logger.info(f"Updating {unreleased_movies.count()} unreleased movies")
    
    # Update each movie
    for movie in unreleased_movies:
        try:
            # Schedule individual movie updates as separate tasks
            # This allows for better error isolation and parallel processing
            async_task(update_single_movie, movie.id)
        except Exception as e:
            logger.error(f"Error scheduling update for movie {movie.title} (ID: {movie.id}): {e}")
    
    return f"Scheduled updates for {unreleased_movies.count()} movies"

def update_random_movies():
    """Update information for 10 oldest updated movies in the database."""
    # Get 10 movies with the oldest updated dates
    movies = Movie.objects.order_by('date_updated')[:10]
    
    # Log how many movies will be updated
    logger.info(f"Updating {len(movies)} movies with oldest update dates")
    
    # Update each movie
    updates_count = 0
    for movie in movies:
        try:
            # Schedule individual movie updates as separate tasks
            # This allows for better error isolation and parallel processing
            async_task(update_single_movie, movie.id)
            updates_count += 1
        except Exception as e:
            logger.error(f"Error scheduling update for movie {movie.title} (ID: {movie.id}): {e}")
    
    return f"Scheduled updates for {updates_count} movies with oldest update dates"

def update_single_movie(movie_id):
    """Update a single movie from TMDB."""
    try:
        movie = Movie.objects.get(id=movie_id)
        
        # Use MoviesService instead of direct requests
        movies_service = MoviesService()
        data = movies_service.get_movie_details(movie.tmdb_id, append_to_response="videos,keywords")
        
        if not data:
            logger.error(f"TMDB API error for movie {movie.title} (ID: {movie_id}): Failed to retrieve data")
            return f"Failed to update movie {movie_id}"
            
        # Update movie fields if they've changed
        updates = {}
        
        # Check and update basic fields
        if data.get('status') != movie.status:
            updates['status'] = data.get('status')
            
        if data.get('overview') and data.get('overview') != movie.description:
            updates['description'] = data.get('overview')
            
        if data.get('vote_average') and data.get('vote_average') != movie.rating:
            updates['rating'] = data.get('vote_average')
        
        # Update trailer if available
        if 'videos' in data and data['videos'].get('results'):
            trailers = [v for v in data['videos']['results'] 
                       if v['type'] == 'Trailer' and v['site'] == 'YouTube']
            if trailers:
                newest_trailer = sorted(trailers, key=lambda x: x['published_at'], reverse=True)[0]
                trailer_url = f"https://www.youtube.com/embed/{newest_trailer['key']}"
                if trailer_url != movie.trailer:
                    updates['trailer'] = trailer_url
        
        if not movie.poster and data.get('poster_path'):
            movie.poster = f"https://image.tmdb.org/t/p/original{data['poster_path']}"
            updates['poster'] = movie.poster
        
        if not movie.backdrop and data.get('backdrop_path'):
            movie.backdrop = f"https://image.tmdb.org/t/p/original{data['backdrop_path']}"
            updates['backdrop'] = movie.backdrop

        if data.get('title') and data.get('title') != movie.title:
            updates['title'] = data.get('title')
        
        if data.get('original_title') and data.get('original_title') != movie.original_title:
            updates['original_title'] = data.get('original_title')
        
        if data.get('belongs_to_collection'):
            print(f"Collection data: {data['belongs_to_collection']}")
            collection_id = data['belongs_to_collection'].get('id')
            if collection_id and movie.collection is None:
                try:
                    collection = Collection.objects.get(tmdb_id=collection_id)
                    print(f"Found existing collection: {collection.name}")
                    updates['collection'] = collection
                except Collection.DoesNotExist:
                    # create collection
                    movies_service = MoviesService()
                    collection_data = movies_service.get_collection_details(collection_id)
                    if collection_data:
                        poster_path = collection_data.get('poster_path')
                        backdrop_path = collection_data.get('backdrop_path')
                        
                        collection = Collection.objects.create(
                            name=collection_data.get('name'),
                            description=collection_data.get('overview'),
                            tmdb_id=collection_data.get('id'),
                            poster=f"https://image.tmdb.org/t/p/original{poster_path}" if poster_path else None,
                            backdrop=f"https://image.tmdb.org/t/p/original{backdrop_path}" if backdrop_path else None,
                        )
                        updates['collection'] = collection

        # Update release date if it changed
        if data.get('release_date') and data.get('release_date') != str(movie.release_date):
            try:
                # Parse and validate the date format (YYYY-MM-DD)
                new_date = date.fromisoformat(data.get('release_date'))
                updates['release_date'] = new_date
            except (ValueError, TypeError):
                logger.error(f"Invalid release date format for movie {movie.title}: {data.get('release_date')}")
        
        # Apply updates if there are any
        if updates:
            logger.info(f"Updating movie {movie.title} (ID: {movie_id}) with: {updates}")
            for field, value in updates.items():
                setattr(movie, field, value)
            movie.save()
            async_task(update_single_collection, collection.id)
            return f"Updated movie {movie.title} with {len(updates)} changes: {', '.join([f'{k}={v}' for k, v in updates.items()])}"
        else:
            return f"No updates needed for movie {movie.title}"
            
    except Movie.DoesNotExist:
        logger.error(f"Movie with ID {movie_id} not found")
        return f"Movie {movie_id} not found"
    except Exception as e:
        logger.error(f"Error updating movie {movie_id}: {e}")
        return f"Error updating movie {movie_id}: {str(e)}"

def update_movie_collections():
    """Update collections by adding any missing movies from TMDB."""
    
    # Get all collections in the database
    collections = Collection.objects.all()
    
    # Log how many collections will be checked
    logger.info(f"Checking {collections.count()} movie collections for updates")
    
    # Update each collection
    updates_count = 0
    for collection in collections:
        try:
            # Schedule individual collection updates as separate tasks
            async_task(update_single_collection, collection.id)
            updates_count += 1
        except Exception as e:
            logger.error(f"Error scheduling update for collection {collection.name} (ID: {collection.id}): {e}")
    
    return f"Scheduled updates for {updates_count} movie collections"

def update_single_collection(collection_id):
    """Update a single collection by fetching its details and adding missing movies."""
    try:        
        collection = Collection.objects.get(id=collection_id)
        
        # Use MoviesService to get collection details
        movies_service = MoviesService()
        data = movies_service.get_collection_details(collection.tmdb_id)
        
        if not data or 'parts' not in data:
            logger.error(f"TMDB API error for collection {collection.name} (ID: {collection_id}): Failed to retrieve data")
            return f"Failed to update collection {collection_id}"
            
        # Get all movies in this collection from our database
        existing_movie_tmdb_ids = set(Movie.objects.filter(collection=collection).values_list('tmdb_id', flat=True))
        
        # Check for missing movies
        added_count = 0
        for movie_data in data['parts']:
            if movie_data['id'] not in existing_movie_tmdb_ids:
                try:
                    # Add this movie to our database
                    async_task(add_movie_from_collection, movie_data['id'], collection.id)
                    added_count += 1
                except Exception as e:
                    logger.error(f"Error adding movie {movie_data.get('title', 'Unknown')} (TMDB ID: {movie_data['id']}) to collection: {e}")
        
        return f"Added {added_count} new movies to collection {collection.name}"
        
    except Collection.DoesNotExist:
        logger.error(f"Collection with ID {collection_id} not found")
        return f"Collection {collection_id} not found"
    except Exception as e:
        logger.error(f"Error updating collection {collection_id}: {e}")
        return f"Error updating collection {collection_id}: {str(e)}"

def add_movie_from_collection(tmdb_id, collection_id):
    """Add a single movie from a collection to the database using the existing parser."""
    try:
        from movies.parsers import create_movie
        
        collection = Collection.objects.get(id=collection_id)
        
        # Check if movie already exists
        if Movie.objects.filter(tmdb_id=tmdb_id).exists():
            # If it exists but isn't linked to this collection, update it
            movie = Movie.objects.get(tmdb_id=tmdb_id)
            if movie.collection != collection:
                movie.collection = collection
                movie.save(update_fields=['collection'])
                return f"Updated collection for existing movie: {movie.title}"
            return f"Movie already exists and is in collection: {movie.title}"
        
        # Get movie details from TMDB
        movies_service = MoviesService()
        data = movies_service.get_movie_details(tmdb_id, append_to_response="videos,keywords")
        
        if not data:
            return f"Failed to retrieve data for movie with TMDB ID {tmdb_id}"
        
        movie = create_movie(tmdb_id, add_to_watchlist=False)
        
        return f"Added new movie to collection: {movie.title}"
        
    except Exception as e:
        logger.error(f"Error adding movie {tmdb_id} to collection {collection_id}: {e}")
        return f"Error adding movie with TMDB ID {tmdb_id}: {str(e)}"

def setup_scheduled_tasks():
    """Set up scheduled tasks if they don't exist already."""
    # Schedule the task to run every minute
    Schedule.objects.get_or_create(
        name='Update unreleased movies',
        defaults={
            'func': 'movies.tasks.update_unreleased_movies',
            'schedule_type': Schedule.HOURLY,
            'repeats': -1,  # Repeat forever
        }
    )
    # Schedule the random movie updates to run daily
    Schedule.objects.get_or_create(
        name='Update random movies',
        defaults={
            'func': 'movies.tasks.update_random_movies',
            'schedule_type': Schedule.DAILY,
            'repeats': -1,  # Repeat forever
        }
    )
    # Schedule collection updates to run weekly
    Schedule.objects.get_or_create(
        name='Update movie collections',
        defaults={
            'func': 'movies.tasks.update_movie_collections',
            'schedule_type': Schedule.WEEKLY,
            'repeats': -1,  # Repeat forever
        }
    )
    return "Scheduled task setup complete"

def create_movie_async(movie_id, movie_poster=None, movie_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """
    Queue movie creation as an async task using Django Q
    This returns the task ID which can be used to check status later
    """
    # Queue the task with Django Q
    task_id = async_task(
        'movies.tasks.create_movie_task',
        movie_id, 
        movie_poster, 
        movie_backdrop, 
        is_anime, 
        add_to_watchlist, 
        user_id,
        hook='movies.tasks.movie_task_complete_hook'
    )
    return task_id

def create_movie_task(movie_id, movie_poster=None, movie_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """
    Task function that is called by Django Q worker
    """
    # Use a local import to avoid circular imports
    from .parsers import create_movie
    
    return create_movie(
        movie_id=movie_id, 
        movie_poster=movie_poster, 
        movie_backdrop=movie_backdrop, 
        is_anime=is_anime, 
        add_to_watchlist=add_to_watchlist, 
        user_id=user_id
    )

def movie_task_complete_hook(task):
    """
    Optional hook that runs when the movie creation task completes
    You can use this to send notifications or update status
    """
    if task.success:
        # Task completed successfully
        movie = task.result
        if movie:
            print(f"Movie '{movie.title}' was successfully created.")
    else:
        # Task failed
        print(f"Movie creation failed: {task.result}")