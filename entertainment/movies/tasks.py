import logging
from datetime import date
from django.conf import settings
from django_q.models import Schedule
from django_q.tasks import async_task, schedule

from custom_auth.models import Movie
from api.services.movies import MoviesService

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
    """Update information for 10 random movies in the database."""
    # Get 10 random movies from the database
    movies = Movie.objects.order_by('?')[:10]
    
    # Log how many movies will be updated
    logger.info(f"Updating {len(movies)} random movies")
    
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
    
    return f"Scheduled updates for {updates_count} random movies"

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
            return f"Updated movie {movie.title} with {len(updates)} changes: {', '.join([f'{k}={v}' for k, v in updates.items()])}"
        else:
            return f"No updates needed for movie {movie.title}"
            
    except Movie.DoesNotExist:
        logger.error(f"Movie with ID {movie_id} not found")
        return f"Movie {movie_id} not found"
    except Exception as e:
        logger.error(f"Error updating movie {movie_id}: {e}")
        return f"Error updating movie {movie_id}: {str(e)}"

def setup_scheduled_tasks():
    """Set up scheduled tasks if they don't exist already."""
    # Schedule the task to run every minute
    Schedule.objects.get_or_create(
        name='Update unreleased movies',
        defaults={
            'func': 'movies.tasks.update_unreleased_movies',
            'schedule_type': Schedule.MINUTES,
            'minutes': 1,  # Run every minute
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
    
    return "Scheduled task setup complete"