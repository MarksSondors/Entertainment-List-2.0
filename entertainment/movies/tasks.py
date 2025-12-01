import logging
import random
from datetime import date
from django.conf import settings
from django_q.models import Schedule
from django_q.tasks import async_task, schedule

from api.services.movies import MoviesService

from movies.models import *


logger = logging.getLogger(__name__)


def check_new_movies_today():
    """
    Scheduled task to check for new movies releasing today and notify users.
    Should be run daily (e.g., at 9 AM).
    """
    from django.utils import timezone
    from django.contrib.contenttypes.models import ContentType
    from custom_auth.models import Watchlist
    from notifications.utils import send_notification_to_user
    
    logger.info("Starting daily new movie release check")
    
    check_date = timezone.now().date()
    
    # Get all movies releasing today
    movies_today = Movie.objects.filter(
        release_date=check_date
    ).prefetch_related('genres')
    
    if not movies_today.exists():
        logger.info(f'No movies found releasing on {check_date}')
        return {'notified_users': 0, 'movies_count': 0}
    
    logger.info(f'Found {movies_today.count()} movie(s) releasing today')
    
    # Get ContentType for Movie
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Track notification stats
    total_notifications = 0
    total_users = set()
    
    # For each movie releasing today
    for movie in movies_today:
        logger.info(f'Processing: {movie.title}')
        
        # Get all users who have this movie in their watchlist
        watchlist_users = Watchlist.objects.filter(
            content_type=movie_content_type,
            object_id=movie.id
        ).select_related('user')
        
        if not watchlist_users.exists():
            logger.info(f'  No users watching {movie.title}')
            continue
        
        logger.info(f'  Notifying {watchlist_users.count()} user(s) about {movie.title}')
        
        # Create notification message
        title = f"🎬 New Release: {movie.title}"
        
        # Add genre info if available
        genres = movie.genres.all()[:2]  # First 2 genres
        if genres:
            genre_text = ", ".join([g.name for g in genres])
            body = f"{movie.title} is now available! ({genre_text})"
        else:
            body = f"{movie.title} is now available!"
        
        # Add runtime if available
        if movie.runtime:
            body += f" • {movie.minutes_to_hours()}"
        
        url = movie.get_absolute_url()
        
        # Send notification to each user
        for watchlist_item in watchlist_users:
            result = send_notification_to_user(
                user_id=watchlist_item.user.id,
                title=title,
                body=body,
                notification_type='new_release',
                url=url,
                icon=movie.poster or '/static/favicon/web-app-manifest-192x192.png',
                content_type=movie_content_type,
                object_id=movie.id,
            )
            
            if result.get('success', 0) > 0 or result.get('queued'):
                total_notifications += 1
                total_users.add(watchlist_item.user.id)
    
    result = {
        'notified_users': len(total_users),
        'notifications_sent': total_notifications,
        'movies_released': movies_today.count()
    }
    
    logger.info(f'Movie release notification complete: {result}')
    return result


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
    """Update information for 10% of the movies in the database (oldest updated first).
    Also updates the people (cast and crew) associated with each movie.
    """
    # Calculate 10% of total movies (minimum 1 movie)
    total_movies = Movie.objects.count()
    movies_to_update = max(1, int(total_movies * 0.1))
    
    # Get movies with the oldest updated dates
    movies = Movie.objects.order_by('date_updated')[:movies_to_update]
    
    # Log how many movies will be updated
    logger.info(f"Updating {len(movies)} movies ({movies_to_update} = 10% of {total_movies} total movies) with oldest update dates (including people)")
    
    # Update each movie
    updates_count = 0
    for movie in movies:
        try:
            # Schedule individual movie updates as separate tasks
            # This allows for better error isolation and parallel processing
            # Pass update_people=True as positional arg since async_task doesn't handle kwargs well
            async_task(update_single_movie, movie.id, True)
            updates_count += 1
        except Exception as e:
            logger.error(f"Error scheduling update for movie {movie.title} (ID: {movie.id}): {e}")
    
    return f"Scheduled updates for {updates_count} movies (including associated people) with oldest update dates"

def update_single_movie(movie_id, update_people=False):
    """Update a single movie from TMDB.
    
    Args:
        movie_id: The database ID of the movie to update
        update_people: If True, also update the cast and crew associated with the movie
    """
    try:
        movie = Movie.objects.get(id=movie_id)
        logger.info(f"Updating movie {movie.title} (ID: {movie_id}), update_people={update_people}")
        
        # Use MoviesService instead of direct requests
        movies_service = MoviesService()
        # Include credits if we're updating people
        append_to_response = "videos,keywords,credits" if update_people else "videos,keywords"
        logger.info(f"Fetching TMDB data with append_to_response={append_to_response}")
        data = movies_service.get_movie_details(movie.tmdb_id, append_to_response=append_to_response)
        
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
        
        # if the release date is not set, set the release date to None
        if data.get('release_date'):
            if data['release_date'] == '':
                updates['release_date'] = None
            elif data['release_date'] != str(movie.release_date):
                try:
                    # Parse and validate the date format (YYYY-MM-DD)
                    new_date = date.fromisoformat(data['release_date'])
                    updates['release_date'] = new_date
                except (ValueError, TypeError):
                    logger.error(f"Invalid release date format for movie {movie.title}: {data['release_date']}")
                    updates['release_date'] = None
        
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
                        updates['collection'] = collection.id

        # Update release date if it changed
        if data.get('release_date') and data.get('release_date') != str(movie.release_date):
            try:
                # Parse and validate the date format (YYYY-MM-DD)
                new_date = date.fromisoformat(data.get('release_date'))
                updates['release_date'] = new_date
            except (ValueError, TypeError):
                logger.error(f"Invalid release date format for movie {movie.title}: {data.get('release_date')}")

        # update keywords
        if 'keywords' in data and data['keywords'].get('keywords', []):
            # Get keyword names from current movie for comparison
            current_keyword_names = set(movie.keywords.values_list('name', flat=True))
            # Get keyword names from API data
            new_keyword_names = {k['name'] for k in data['keywords']['keywords']}
            
            if current_keyword_names != new_keyword_names:
                # Get or create keyword instances
                keyword_instances = []
                for keyword in data['keywords']['keywords']:
                    keyword_instance, _ = Keyword.objects.get_or_create(
                        tmdb_id=keyword.get('id'),
                        defaults={'name': keyword.get('name')}
                    )
                    keyword_instances.append(keyword_instance)
                
                # Set new keywords directly on the movie
                movie.keywords.set(keyword_instances)
                logger.info(f"Updated keywords for {movie.title}")
        
        # update genres
        if 'genres' in data and data['genres']:
            # Get genre names from current movie for comparison
            current_genre_names = set(movie.genres.values_list('name', flat=True))
            # Get genre names from API data
            new_genre_names = {g['name'] for g in data['genres']}
            
            if current_genre_names != new_genre_names:
                # Get or create genre instances
                genre_instances = []
                for genre in data['genres']:
                    genre_instance, _ = Genre.objects.get_or_create(
                        tmdb_id=genre.get('id'),
                        defaults={'name': genre.get('name')}
                    )
                    genre_instances.append(genre_instance)
                
                # Set new genres directly on the movie
                movie.genres.set(genre_instances)
                logger.info(f"Updated genres for {movie.title}")
        
        # update production countries
        if 'production_countries' in data and data['production_countries']:
            # Get country names from current movie for comparison
            current_country_codes = set(movie.countries.values_list('iso_3166_1', flat=True))
            # Get country codes from API data
            new_country_codes = {c['iso_3166_1'] for c in data['production_countries']}
            
            if current_country_codes != new_country_codes:
                # Get or create country instances
                country_instances = []
                for country in data['production_countries']:
                    country_instance, _ = Country.objects.get_or_create(
                        iso_3166_1=country.get('iso_3166_1'),
                        defaults={'name': country.get('name')}
                    )
                    country_instances.append(country_instance)
                
                # Set new countries directly on the movie
                movie.countries.set(country_instances)
                logger.info(f"Updated countries for {movie.title}")
            
        if 'production_companies' in data and data['production_companies']:
            # Get company names from current movie for comparison
            current_company_names = set(movie.production_companies.values_list('name', flat=True))
            # Get company names from API data
            new_company_names = {c['name'] for c in data['production_companies']}
            
            if current_company_names != new_company_names:
                # Get or create company instances
                company_instances = []
                for company in data['production_companies']:
                    origin_country = None
                    if company.get('origin_country') and company.get('origin_country') != "":
                        try:
                            origin_country = Country.objects.get(iso_3166_1=company.get('origin_country'))
                        except Country.DoesNotExist:
                            logger.warning(f"Country with code {company.get('origin_country')} not found")
                            
                    logo_path = company.get('logo_path')
                    company_instance, _ = ProductionCompany.objects.get_or_create(
                        tmdb_id=company.get('id'),
                        defaults={
                            'name': company.get('name'),
                            'country': origin_country,
                            'logo_path': f"https://image.tmdb.org/t/p/original{logo_path}" if logo_path else None
                        }
                    )
                    company_instances.append(company_instance)
                
                # Set new companies directly on the movie
                movie.production_companies.set(company_instances)
                logger.info(f"Updated production companies for {movie.title}")
        
        # Apply updates if there are any
        if updates:
            logger.info(f"Updating movie {movie.title} (ID: {movie_id}) with: {updates}")
            for field, value in updates.items():
                setattr(movie, field, value)
            movie.save()
            
            # Only update collection if we're working with one
            if 'collection' in updates and updates['collection'] is not None:
                if isinstance(updates['collection'], Collection):
                    async_task(update_single_collection, updates['collection'].id)
                elif isinstance(updates['collection'], int):
                    async_task(update_single_collection, updates['collection'])
        else:
            # Even when no content changes, update the date_updated field
            # This ensures rotation in the update_random_movies function
            movie.save(update_fields=['date_updated'])
        
        # Update associated people and MediaPerson entries if requested
        people_updated = 0
        media_persons_updated = 0
        media_persons_added = 0
        
        logger.info(f"update_people={update_people}, 'credits' in data={'credits' in data}")
        
        if update_people and 'credits' in data:
            from custom_auth.models import MediaPerson, Person
            from django.contrib.contenttypes.models import ContentType
            
            movie_content_type = ContentType.objects.get_for_model(Movie)
            
            # Get all persons currently associated with this movie
            existing_person_ids = set(
                MediaPerson.objects.filter(
                    content_type=movie_content_type,
                    object_id=movie.id
                ).values_list('person__tmdb_id', flat=True)
            )
            
            # Collect unique TMDB IDs from cast and crew
            cast = data['credits'].get('cast', [])
            crew = data['credits'].get('crew', [])
            
            logger.info(f"Found {len(cast)} cast members and {len(crew)} crew members from TMDB")
            logger.info(f"Existing person TMDB IDs in DB: {len(existing_person_ids)}")
            
            # Get TMDb IDs of people in the credits
            api_person_ids = set()
            for person in cast:
                api_person_ids.add(person.get('id'))
            for person in crew:
                if person.get('department') in ['Directing', 'Writing', 'Sound']:
                    api_person_ids.add(person.get('id'))
            
            # Update existing persons that are both in our DB and in the credits
            persons_to_update = api_person_ids & existing_person_ids
            for tmdb_id in persons_to_update:
                try:
                    person = Person.objects.get(tmdb_id=tmdb_id)
                    async_task(update_single_person, person.id)
                    people_updated += 1
                except Person.DoesNotExist:
                    pass
            
            # Update or add MediaPerson entries for cast
            for index, cast_member in enumerate(cast):
                tmdb_id = cast_member.get('id')
                cast_name = cast_member.get('name', 'Unknown')
                
                # Try to get existing person or create new one
                person = None
                try:
                    person = Person.objects.get(tmdb_id=tmdb_id)
                    logger.debug(f"Found existing person: {person.name} (TMDB ID: {tmdb_id})")
                except Person.DoesNotExist:
                    # Person doesn't exist in DB, create them
                    logger.info(f"Person {cast_name} (TMDB ID: {tmdb_id}) not in DB, fetching details...")
                    person_details = movies_service.get_person_details(tmdb_id)
                    if person_details:
                        person = Person.objects.create(
                            tmdb_id=person_details.get('id'),
                            name=person_details.get('name'),
                            profile_picture=f"https://image.tmdb.org/t/p/original{person_details.get('profile_path')}" if person_details.get('profile_path') else None,
                            date_of_birth=person_details.get('birthday'),
                            date_of_death=person_details.get('deathday'),
                            bio=person_details.get('biography'),
                            imdb_id=person_details.get('imdb_id'),
                            is_actor=True
                        )
                        logger.info(f"Created new person: {person.name}")
                    else:
                        logger.warning(f"Could not fetch details for person TMDB ID: {tmdb_id}")
                        continue
                
                if not person:
                    continue
                
                # Check if MediaPerson entry exists
                media_person = MediaPerson.objects.filter(
                    content_type=movie_content_type,
                    object_id=movie.id,
                    person=person,
                    role="Actor"
                ).first()
                
                if media_person:
                    # Update existing entry
                    mp_updates = {}
                    new_order = cast_member.get('order', index)
                    new_character = cast_member.get('character')
                    
                    if media_person.order != new_order:
                        mp_updates['order'] = new_order
                    if new_character and media_person.character_name != new_character:
                        mp_updates['character_name'] = new_character
                    
                    if mp_updates:
                        for field, value in mp_updates.items():
                            setattr(media_person, field, value)
                        media_person.save()
                        media_persons_updated += 1
                        logger.info(f"Updated MediaPerson for {person.name} in {movie.title}")
                else:
                    # Create new MediaPerson entry
                    logger.info(f"Creating new MediaPerson entry for {person.name} as Actor in {movie.title}")
                    new_mp = MediaPerson.objects.create(
                        content_type=movie_content_type,
                        object_id=movie.id,
                        person=person,
                        role="Actor",
                        character_name=cast_member.get('character'),
                        order=cast_member.get('order', index)
                    )
                    logger.info(f"Created MediaPerson ID: {new_mp.id}")
                    # Ensure person has is_actor flag
                    if not person.is_actor:
                        person.is_actor = True
                        person.save(update_fields=['is_actor'])
                    media_persons_added += 1
                    logger.info(f"Added {person.name} as Actor in {movie.title}")
            
            # Update or add MediaPerson entries for crew
            for crew_member in crew:
                department = crew_member.get('department')
                job = crew_member.get('job')
                tmdb_id = crew_member.get('id')
                
                # Skip if not a relevant department/job
                if department not in ['Directing', 'Writing', 'Sound']:
                    continue
                if (department == 'Directing' and job != 'Director') or \
                   (department == 'Writing' and job not in ['Original Story', 'Screenplay', 'Writer', 'Story', 'Novel', 'Comic Book', 'Graphic Novel', 'Book']) or \
                   (department == 'Sound' and job != 'Original Music Composer'):
                    continue
                
                # Try to get existing person or create new one
                try:
                    person = Person.objects.get(tmdb_id=tmdb_id)
                except Person.DoesNotExist:
                    # Person doesn't exist in DB, create them
                    person_details = movies_service.get_person_details(tmdb_id)
                    if person_details:
                        person = Person.objects.create(
                            tmdb_id=person_details.get('id'),
                            name=person_details.get('name'),
                            profile_picture=f"https://image.tmdb.org/t/p/original{person_details.get('profile_path')}" if person_details.get('profile_path') else None,
                            date_of_birth=person_details.get('birthday'),
                            date_of_death=person_details.get('deathday'),
                            bio=person_details.get('biography'),
                            imdb_id=person_details.get('imdb_id')
                        )
                        logger.info(f"Created new crew person: {person.name}")
                    else:
                        logger.warning(f"Could not fetch details for crew person TMDB ID: {tmdb_id}")
                        continue
                
                # Check if MediaPerson entry exists for this role
                media_person = MediaPerson.objects.filter(
                    content_type=movie_content_type,
                    object_id=movie.id,
                    person=person,
                    role=job
                ).first()
                
                if not media_person:
                    # Check if person exists with a different crew role
                    existing_mp = MediaPerson.objects.filter(
                        content_type=movie_content_type,
                        object_id=movie.id,
                        person=person
                    ).exclude(role="Actor").first()
                    
                    if existing_mp and existing_mp.role != job:
                        # Role has changed, update it
                        logger.info(f"Updating role for {person.name} from {existing_mp.role} to {job}")
                        existing_mp.role = job
                        existing_mp.save()
                        media_persons_updated += 1
                    elif not existing_mp:
                        # Create new MediaPerson entry for crew
                        logger.info(f"Creating new MediaPerson entry for {person.name} as {job} in {movie.title}")
                        new_mp = MediaPerson.objects.create(
                            content_type=movie_content_type,
                            object_id=movie.id,
                            person=person,
                            role=job
                        )
                        logger.info(f"Created crew MediaPerson ID: {new_mp.id}")
                        # Update person role flags
                        role_flag_updated = False
                        if job == 'Director' and not person.is_director:
                            person.is_director = True
                            role_flag_updated = True
                        elif job == 'Original Music Composer' and not person.is_original_music_composer:
                            person.is_original_music_composer = True
                            role_flag_updated = True
                        elif job == 'Screenplay' and not person.is_screenwriter:
                            person.is_screenwriter = True
                            role_flag_updated = True
                        elif job == 'Writer' and not person.is_writer:
                            person.is_writer = True
                            role_flag_updated = True
                        elif job == 'Story' and not person.is_story:
                            person.is_story = True
                            role_flag_updated = True
                        elif job == 'Original Story' and not person.is_original_story:
                            person.is_original_story = True
                            role_flag_updated = True
                        elif job == 'Novel' and not person.is_novelist:
                            person.is_novelist = True
                            role_flag_updated = True
                        elif job == 'Comic Book' and not person.is_comic_artist:
                            person.is_comic_artist = True
                            role_flag_updated = True
                        elif job == 'Graphic Novel' and not person.is_graphic_novelist:
                            person.is_graphic_novelist = True
                            role_flag_updated = True
                        elif job == 'Book' and not person.is_book:
                            person.is_book = True
                            role_flag_updated = True
                        
                        if role_flag_updated:
                            person.save()
                        
                        media_persons_added += 1
                        logger.info(f"Added {person.name} as {job} in {movie.title}")
            
            logger.info(f"For {movie.title}: {people_updated} people scheduled for update, {media_persons_updated} entries updated, {media_persons_added} entries added")
        
        if updates:
            return f"Updated movie {movie.title} with {len(updates)} changes. {people_updated} people scheduled, {media_persons_updated} updated, {media_persons_added} added."
        else:
            return f"No movie field updates for {movie.title}. {people_updated} people scheduled, {media_persons_updated} updated, {media_persons_added} added."
            
    except Movie.DoesNotExist:
        logger.error(f"Movie with ID {movie_id} not found")
        return f"Movie {movie_id} not found"
    except Exception as e:
        logger.error(f"Error updating movie {movie_id}: {e}")
        return f"Error updating movie {movie_id}: {str(e)}"

def update_single_person(person_id):
    """Update a single person's details from TMDB.
    
    Args:
        person_id: The database ID of the Person to update
    """
    from custom_auth.models import Person
    
    try:
        person = Person.objects.get(id=person_id)
        
        if not person.tmdb_id:
            logger.warning(f"Person {person.name} (ID: {person_id}) has no TMDB ID, skipping update")
            return f"Person {person.name} has no TMDB ID"
        
        movies_service = MoviesService()
        data = movies_service.get_person_details(person.tmdb_id)
        
        if not data:
            logger.error(f"TMDB API error for person {person.name} (ID: {person_id}): Failed to retrieve data")
            return f"Failed to update person {person_id}"
        
        updates = {}
        
        # Update basic fields if they've changed
        if data.get('name') and data.get('name') != person.name:
            updates['name'] = data.get('name')
        
        if data.get('biography') and data.get('biography') != person.bio:
            updates['bio'] = data.get('biography')
        
        if data.get('profile_path'):
            new_profile = f"https://image.tmdb.org/t/p/original{data['profile_path']}"
            if new_profile != person.profile_picture:
                updates['profile_picture'] = new_profile
        
        if data.get('birthday') and data.get('birthday') != str(person.date_of_birth):
            try:
                updates['date_of_birth'] = date.fromisoformat(data['birthday'])
            except (ValueError, TypeError):
                pass
        
        if data.get('deathday'):
            if data['deathday'] != str(person.date_of_death):
                try:
                    updates['date_of_death'] = date.fromisoformat(data['deathday'])
                except (ValueError, TypeError):
                    pass
        elif person.date_of_death:
            # If API says person is alive but we have a death date, clear it
            updates['date_of_death'] = None
        
        if data.get('imdb_id') and data.get('imdb_id') != person.imdb_id:
            updates['imdb_id'] = data.get('imdb_id')
        
        # Apply updates if there are any
        if updates:
            logger.info(f"Updating person {person.name} (ID: {person_id}) with: {list(updates.keys())}")
            for field, value in updates.items():
                setattr(person, field, value)
            person.save()
            return f"Updated person {person.name} with {len(updates)} changes"
        else:
            return f"No updates needed for person {person.name}"
    
    except Person.DoesNotExist:
        logger.error(f"Person with ID {person_id} not found")
        return f"Person {person_id} not found"
    except Exception as e:
        logger.error(f"Error updating person {person_id}: {e}")
        return f"Error updating person {person_id}: {str(e)}"

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
    from django.utils import timezone
    from datetime import time, timedelta, datetime
    
    # Schedule the unreleased movies task to run hourly at 15 minutes past the hour
    Schedule.objects.get_or_create(
        name='Update unreleased movies',
        defaults={
            'func': 'movies.tasks.update_unreleased_movies',
            'schedule_type': Schedule.HOURLY,
            'repeats': -1,  # Repeat forever
            'next_run': timezone.now().replace(minute=15, second=0, microsecond=0) + timedelta(hours=1),
        }
    )
    
    # Schedule the random movie updates to run daily at 03:30 AM
    daily_time = time(hour=3, minute=30)  # 3:30 AM
    Schedule.objects.get_or_create(
        name='Update random movies',
        defaults={
            'func': 'movies.tasks.update_random_movies',
            'schedule_type': Schedule.DAILY,
            'repeats': -1,  # Repeat forever
            'next_run': timezone.make_aware(
                datetime.combine(timezone.now().date(), daily_time)
            ),
        }
    )
    
    # Schedule collection updates to run weekly on Monday at 02:00 AM
    monday_time = time(hour=2, minute=0)  # 2:00 AM
    next_monday = timezone.now()
    while next_monday.weekday() != 0:  # 0 is Monday
        next_monday += timedelta(days=1)
    
    Schedule.objects.get_or_create(
        name='Update movie collections',
        defaults={
            'func': 'movies.tasks.update_movie_collections',
            'schedule_type': Schedule.WEEKLY,
            'repeats': -1,  # Repeat forever
            'next_run': timezone.make_aware(
                datetime.combine(next_monday.date(), monday_time)
            ),
        }
    )
    return "Scheduled task setup complete"

def create_movie_fast(movie_id, movie_poster=None, movie_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """
    Create movie with basic information immediately, then enrich with details in background
    This returns the movie object immediately for faster user feedback
    """
    # Use a local import to avoid circular imports
    from .parsers import create_movie_basic
    
    # Create movie with basic information first
    movie = create_movie_basic(
        movie_id=movie_id, 
        movie_poster=movie_poster, 
        movie_backdrop=movie_backdrop, 
        is_anime=is_anime, 
        add_to_watchlist=add_to_watchlist, 
        user_id=user_id
    )
    
    if movie:
        # Queue background task to enrich with cast, crew, collections, etc.
        async_task(
            'movies.tasks.enrich_movie_task',
            movie.id,
            hook='movies.tasks.movie_enrichment_complete_hook'
        )
    
    return movie

def enrich_movie_task(movie_id):
    """
    Background task to enrich movie with cast, crew, collections, and production companies
    """
    from .parsers import enrich_movie_with_details
    
    return enrich_movie_with_details(movie_id)

def movie_enrichment_complete_hook(task):
    """
    Optional hook that runs when the movie enrichment task completes
    """
    if task.success:
        movie = task.result
        if movie:
            logger.info(f"Movie '{movie.title}' was successfully enriched with detailed information.")
    else:
        logger.error(f"Movie enrichment failed: {task.result}")

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


def send_biweekly_recommendations():
    """
    Send bi-weekly movie recommendations to users.
    Alternates between personalized recommendations and previous Movie of the Week picks.
    """
    # Lazy imports
    from django.contrib.auth import get_user_model
    from notifications.utils import send_notification_to_user
    from movies.services.recommendation import MovieRecommender
    from django.contrib.contenttypes.models import ContentType
    from custom_auth.models import Review
    from django.db.models import Avg
    
    User = get_user_model()
    logger.info("Starting bi-weekly recommendation notifications")
    
    # Decide whether to use Movie of the Week or personalized recommendations
    # Use random choice weighted 70% personalized, 30% movie of the week
    use_movie_of_week = random.random() < 0.3
    
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Get all users with recommendations enabled
    users = User.objects.filter(
        notification_preferences__recommendations=True
    ).select_related('notification_preferences')
    
    recommender = MovieRecommender()
    
    total_sent = 0
    for user in users:
        try:
            if use_movie_of_week:
                # Get previous Movie of the Week picks that user hasn't watched/reviewed
                recommended_movie = _get_unwatched_movie_of_week(user, movie_content_type)
                source = "community pick"
            else:
                # Get personalized recommendation
                recommendations = recommender.get_recommendations_for_user(user.id, max_recommendations=5)
                if recommendations:
                    # Pick a random movie from top 5 recommendations
                    recommended_movie = random.choice(recommendations)
                    source = "personalized recommendation"
                else:
                    # Fallback to Movie of the Week if no personalized recommendations
                    recommended_movie = _get_unwatched_movie_of_week(user, movie_content_type)
                    source = "community pick"
            
            if not recommended_movie:
                logger.info(f"No recommendations available for {user.username}")
                continue
            
            # Build notification
            title = f"🎥 Your bi-weekly movie recommendation"
            
            body_parts = [f"Check out {recommended_movie.title}"]
            
            # Add genres
            if recommended_movie.genres.exists():
                genres = ", ".join([g.name for g in recommended_movie.genres.all()[:2]])
                body_parts.append(f"{genres}")
            
            # Add average user rating from reviews
            avg_rating = Review.objects.filter(
                content_type=movie_content_type,
                object_id=recommended_movie.id
            ).aggregate(Avg('rating'))['rating__avg']
            
            if avg_rating:
                body_parts.append(f"⭐ {avg_rating:.1f}/10")
            
            body = " • ".join(body_parts)
            url = recommended_movie.get_absolute_url()
            
            send_notification_to_user(
                user_id=user.id,
                title=title,
                body=body,
                notification_type='recommendations',
                url=url
            )
            total_sent += 1
            logger.info(f"Sent {source} to {user.username}: {recommended_movie.title}")
            
        except Exception as e:
            logger.error(f"Error sending recommendation to {user.username}: {e}", exc_info=True)
    
    logger.info(f"Completed bi-weekly recommendation notifications: {total_sent} sent")
    return {'recommendations_sent': total_sent, 'users_count': users.count()}


def _get_unwatched_movie_of_week(user, movie_content_type):
    """
    Get a previous Movie of the Week that the user hasn't watched or reviewed.
    Returns None if no unwatched movies found.
    """
    from custom_auth.models import Review
    
    # Get user's watched movie IDs (from reviews)
    reviewed_movie_ids = set(
        Review.objects.filter(
            user=user,
            content_type=movie_content_type
        ).values_list('object_id', flat=True)
    )
    
    # Get completed Movie of the Week picks (excluding active/queued ones)
    # that the user hasn't watched and hasn't reviewed
    completed_picks = MovieOfWeekPick.objects.filter(
        status='completed'
    ).exclude(
        watched_by=user
    ).exclude(
        movie_id__in=reviewed_movie_ids
    ).select_related('movie').order_by('-end_date')
    
    if completed_picks.exists():
        # Return a random pick from the most recent 10 unwatched
        recent_unwatched = list(completed_picks[:10])
        return random.choice(recent_unwatched).movie
    
    return None


def notify_active_movie_of_week():
    """
    Send notification about the current active Movie of the Week to users who haven't watched it yet.
    Runs every other day at 5 PM.
    """
    # Lazy imports
    from django.contrib.auth import get_user_model
    from notifications.utils import send_notification_to_user
    from django.contrib.contenttypes.models import ContentType
    from custom_auth.models import Review
    from django.db.models import Avg
    
    User = get_user_model()
    logger.info("Checking for active Movie of the Week to notify users")
    
    # Get the current active Movie of the Week
    active_pick = MovieOfWeekPick.objects.filter(status='active').select_related('movie').first()
    
    if not active_pick:
        logger.info("No active Movie of the Week found")
        return {'notified_users': 0, 'reason': 'no_active_movie'}
    
    movie = active_pick.movie
    movie_content_type = ContentType.objects.get_for_model(Movie)
    
    # Get users who have movie_of_week notifications enabled
    users = User.objects.filter(
        notification_preferences__movie_of_week=True
    ).select_related('notification_preferences')
    
    total_sent = 0
    for user in users:
        try:
            # Check if user has already watched this movie
            if active_pick.watched_by.filter(id=user.id).exists():
                logger.debug(f"User {user.username} already watched {movie.title}")
                continue
            
            # Check if user has already reviewed this movie
            has_reviewed = Review.objects.filter(
                user=user,
                content_type=movie_content_type,
                object_id=movie.id
            ).exists()
            
            if has_reviewed:
                logger.debug(f"User {user.username} already reviewed {movie.title}")
                continue
            
            # Build notification
            title = f"🏆 Community Movie of the Week: {movie.title}"
            
            body_parts = ["Don't miss this week's community pick"]
            
            # Add genres
            if movie.genres.exists():
                genres = ", ".join([g.name for g in movie.genres.all()[:2]])
                body_parts.append(f"{genres}")
            
            # Add average user rating from reviews
            avg_rating = Review.objects.filter(
                content_type=movie_content_type,
                object_id=movie.id
            ).aggregate(Avg('rating'))['rating__avg']
            
            if avg_rating:
                body_parts.append(f"⭐ {avg_rating:.1f}/10")
            
            # Add watched count
            watched_count = active_pick.watched_by.count()
            if watched_count > 0:
                body_parts.append(f"{watched_count} watched")
            
            body = " • ".join(body_parts)
            url = movie.get_absolute_url()
            
            send_notification_to_user(
                user_id=user.id,
                title=title,
                body=body,
                notification_type='movie_of_week',
                url=url
            )
            total_sent += 1
            logger.info(f"Sent Movie of the Week reminder to {user.username}: {movie.title}")
            
        except Exception as e:
            logger.error(f"Error sending Movie of the Week notification to {user.username}: {e}", exc_info=True)
    
    logger.info(f"Completed Movie of the Week notifications: {total_sent} sent")
    return {'notified_users': total_sent, 'movie_title': movie.title}