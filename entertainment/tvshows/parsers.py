from custom_auth.models import *
from api.services.tv_shows import TVShowsService
from api.services.movies import MoviesService
from django.contrib.contenttypes.models import ContentType

def create_tv_show(show_id, show_poster=None, show_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=1, include_seasons=True):
    """
    Create a TV show and its related entities from TMDB API data.
    
    Args:
        show_id: TMDB ID for the TV show
        show_poster: URL for the poster image (if None, uses TMDB URL)
        show_backdrop: URL for the backdrop image (if None, uses TMDB URL)
        is_anime: Boolean flag to mark the show as anime
        add_to_watchlist: Boolean flag to add the show to the user's watchlist
        user_id: User ID for the watchlist (default is 1)
        include_seasons: Whether to create seasons and episodes (can be time-consuming for large shows)
    
    Returns:
        The created TVShow instance, or None if creation failed
    """
    # Get TV show details from TMDB API
    show_details = TVShowsService().get_show_details(show_id, append_to_response='videos,credits,keywords')
    if not show_details:
        return None
        
    # Set poster and backdrop URLs
    if not show_poster:
        show_poster = f"https://image.tmdb.org/t/p/original{show_details.get('poster_path')}" if show_details.get('poster_path') else None
    if not show_backdrop:
        show_backdrop = f"https://image.tmdb.org/t/p/original{show_details.get('backdrop_path')}" if show_details.get('backdrop_path') else None
    
    # Find trailer
    youtube_videos = [video for video in show_details.get('videos', {}).get('results', []) 
                     if video.get('site') == 'YouTube' and video.get('type') == 'Trailer']
    trailer_key = youtube_videos[0].get('key') if youtube_videos else None
    trailer_link = f'https://www.youtube.com/embed/{trailer_key}' if trailer_key else None

    # Detect if anime (by production country)
    if is_anime:
        production_countries = show_details.get('production_countries', []) or show_details.get('origin_country', [])
        is_anime = any(country == 'JP' or country.get('iso_3166_1') == 'JP' for country in production_countries)

    # Create the TV show dictionary with basic properties
    show_dict = {
        'title': show_details.get('name'),
        'original_title': show_details.get('original_name') or show_details.get('name'),
        'poster': show_poster,
        'backdrop': show_backdrop,
        'first_air_date': show_details.get('first_air_date'),
        'last_air_date': show_details.get('last_air_date'),
        'tmdb_id': show_details.get('id'),
        'description': show_details.get('overview'),
        'rating': show_details.get('vote_average'),
        'trailer': trailer_link,
        'is_anime': is_anime,
        'status': show_details.get('status'),
    }
    
    # Create genres, countries and keywords
    genres = show_details.get('genres', [])
    genre_instances = []
    for genre in genres:
        genre_instance, _ = Genre.objects.get_or_create(
            tmdb_id=genre.get('id'),
            defaults={'name': genre.get('name')}
        )
        genre_instances.append(genre_instance)

    # Handle country data which might be in different formats
    countries = show_details.get('production_countries', []) or show_details.get('origin_country', [])
    country_instances = []
    for country in countries:
        if isinstance(country, dict):  # Full country object
            country_instance, _ = Country.objects.get_or_create(
                iso_3166_1=country.get('iso_3166_1'),
                defaults={'name': country.get('name')}
            )
        else:  # Just country code
            country_instance, _ = Country.objects.get_or_create(
                iso_3166_1=country,
                defaults={'name': country}  # Will be updated later if needed
            )
        country_instances.append(country_instance)

    # Keywords
    keywords = show_details.get('keywords', {}).get('results', [])
    keyword_instances = []
    for keyword in keywords:
        keyword_instance, _ = Keyword.objects.get_or_create(
            tmdb_id=keyword.get('id'),
            defaults={'name': keyword.get('name')}
        )
        keyword_instances.append(keyword_instance)

    # Create the TV show instance
    tv_show = TVShow.objects.create(**show_dict)
    tv_show.genres.set(genre_instances)
    tv_show.countries.set(country_instances)
    tv_show.keywords.set(keyword_instances)

    # Add to watchlist if specified
    if add_to_watchlist:
        Watchlist.objects.create(
            user_id=user_id,
            content_type=ContentType.objects.get_for_model(tv_show),
            object_id=tv_show.id
        )
    
    # Get the TV show's content type for MediaPerson
    tv_show_content_type = ContentType.objects.get_for_model(tv_show)
    
    # Process cast
    credits = TVShowsService().get_show_credits(show_id)
    cast = credits.get('cast', [])
    for index, person in enumerate(cast):
        person_details = MoviesService().get_person_details(person.get('id'))
        if not person_details:
            continue
            
        person_instance, _ = Person.objects.get_or_create(
            tmdb_id=person_details.get('id'),
            defaults={
                'name': person_details.get('name'),
                'profile_picture': f"https://image.tmdb.org/t/p/original{person_details.get('profile_path')}" if person_details.get('profile_path') else None,
                'date_of_birth': person_details.get('birthday'),
                'date_of_death': person_details.get('deathday'),
                'bio': person_details.get('biography'),
                'imdb_id': person_details.get('imdb_id'),
            }
        )

        person_instance.is_actor = True
        person_instance.save()
        
        roles = person.get('roles')
        if roles:
            # combie them into a single string
            roles = ', '.join([role.get('character') for role in roles])
        else:
            roles = person.get('character')
        # Create MediaPerson for cast member
        MediaPerson.objects.create(
            content_type=tv_show_content_type,
            object_id=tv_show.id,
            person=person_instance,
            role="Actor",
            character_name=roles,
            order=person.get('order', index)
        )
    
    
    # Process created_by field for creators
    for creator in show_details.get('created_by', []):
        person_details = MoviesService().get_person_details(creator.get('id'))
        if not person_details:
            continue
            
        person_instance, _ = Person.objects.get_or_create(
            tmdb_id=person_details.get('id'),
            defaults={
                'name': person_details.get('name'),
                'profile_picture': f"https://image.tmdb.org/t/p/original{person_details.get('profile_path')}" if person_details.get('profile_path') else None,
                'date_of_birth': person_details.get('birthday'),
                'date_of_death': person_details.get('deathday'),
                'bio': person_details.get('biography'),
                'imdb_id': person_details.get('imdb_id')
            }
        )
        
        # Set creator flag
        person_instance.is_tv_creator = True
        person_instance.save()
        
        # Create MediaPerson entry
        MediaPerson.objects.create(
            content_type=tv_show_content_type,
            object_id=tv_show.id,
            person=person_instance,
            role="Creator"
        )
    
    # Create seasons and episodes if requested
    if include_seasons:
        for season_data in show_details.get('seasons', []):
            season_number = season_data.get('season_number')
                
            # Get detailed season info
            season_details = TVShowsService().get_season_details(
                show_id=show_id, 
                season_number=season_number
            )
            
            if not season_details:
                continue
                
            # Create season
            season = Season.objects.create(
                show=tv_show,
                title=season_details.get('name'),
                season_number=season_number,
                air_date=season_details.get('air_date'),
                overview=season_details.get('overview'),
                poster=f"https://image.tmdb.org/t/p/original{season_details.get('poster_path')}" if season_details.get('poster_path') else None,
                tmdb_id=season_details.get('id')
            )
            
            # Create episodes for this season
            for episode_data in season_details.get('episodes', []):
                Episode.objects.create(
                    season=season,
                    title=episode_data.get('name'),
                    episode_number=episode_data.get('episode_number'),
                    air_date=episode_data.get('air_date'),
                    overview=episode_data.get('overview'),
                    still=f"https://image.tmdb.org/t/p/original{episode_data.get('still_path')}" if episode_data.get('still_path') else None,
                    runtime=episode_data.get('runtime'),
                    rating=episode_data.get('vote_average'),
                    tmdb_id=episode_data.get('id')
                )
    
    return tv_show