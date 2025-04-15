from .models import *
from api.services.tvshows import TVShowsService
from api.services.movies import MoviesService
from django.contrib.contenttypes.models import ContentType
from custom_auth.models import CustomUser

def extract_tvshow_data(tvshow_details, tvshow_poster=None, tvshow_backdrop=None, is_anime=False):
    """Extract and format basic TV show data from API response"""
    if not tvshow_poster:
        tvshow_poster = f"https://image.tmdb.org/t/p/original{tvshow_details.get('poster_path')}" if tvshow_details.get('poster_path') else None
    if not tvshow_backdrop:
        tvshow_backdrop = f"https://image.tmdb.org/t/p/original{tvshow_details.get('backdrop_path')}" if tvshow_details.get('backdrop_path') else None
    
    youtube_videos = [video for video in tvshow_details.get('videos', {}).get('results', []) 
                    if video.get('site') == 'YouTube' and video.get('type') == 'Trailer']
    trailer_key = youtube_videos[0].get('key') if youtube_videos else None
    trailer_link = f'https://www.youtube.com/embed/{trailer_key}' if trailer_key else None

    if is_anime == True:
        production_countries = tvshow_details.get('origin_country', [])
        is_anime = 'JP' in production_countries

    return {
        'title': tvshow_details.get('name'),
        'original_title': tvshow_details.get('original_name'),
        'poster': tvshow_poster,
        'backdrop': tvshow_backdrop,
        'first_air_date': tvshow_details.get('first_air_date'),
        'last_air_date': tvshow_details.get('last_air_date'),
        'tmdb_id': tvshow_details.get('id'),
        'imdb_id': tvshow_details.get('external_ids', {}).get('imdb_id'),
        'description': tvshow_details.get('overview'),
        'rating': tvshow_details.get('vote_average'),
        'trailer': trailer_link,
        'is_anime': is_anime,
        'status': tvshow_details.get('status'),
    }

def process_genres_countries_keywords(tvshow_details):
    """Process and create genre, country and keyword instances"""
    # Process genres
    genres = tvshow_details.get('genres', [])
    genre_instances = []
    for genre in genres:
        genre_instance, _ = Genre.objects.get_or_create(
            tmdb_id=genre.get('id'),
            defaults={'name': genre.get('name')}
        )
        genre_instances.append(genre_instance)

    # Process countries
    countries = tvshow_details.get('origin_country', [])
    country_instances = []
    for country_code in countries:
        country_instance, _ = Country.objects.get_or_create(
            iso_3166_1=country_code
        )
        country_instances.append(country_instance)

    # Process keywords
    keywords = tvshow_details.get('keywords', {}).get('results', [])
    keyword_instances = []
    for keyword in keywords:
        keyword_instance, _ = Keyword.objects.get_or_create(
            tmdb_id=keyword.get('id'),
            defaults={'name': keyword.get('name')}
        )
        keyword_instances.append(keyword_instance)

    return genre_instances, country_instances, keyword_instances

def process_cast(tvshow, cast, tvshows_service):
    """Process cast members from TV show credits"""
    tvshow_content_type = ContentType.objects.get_for_model(tvshow)
    
    for index, person in enumerate(cast):
        person_details = MoviesService().get_person_details(person.get('id'))
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

        person_instance.is_actor = True
        person_instance.save()

        # characters can be more than one, so we need to handle that
        # you can find the character names in list roles
        roles = person.get('roles', [])
        # combine all character names into a single string
        character_names = ', '.join([role.get('character') for role in roles if role.get('character')])
        
        MediaPerson.objects.create(
            content_type=tvshow_content_type,
            object_id=tvshow.id,
            person=person_instance,
            role="Actor",
            character_name=character_names,
            order=person.get('order', index)
        )

def process_crew(tvshow, crew, tvshows_service):
    """Process crew members from TV show credits"""
    tvshow_content_type = ContentType.objects.get_for_model(tvshow)
    
    for person in crew:
        jobs = person.get('jobs', [])
        
        # If jobs list is empty, try to use the direct job field
        if not jobs and person.get('job'):
            jobs = [{'job': person.get('job')}]
        
        # Skip if no relevant jobs
        relevant_jobs = [job for job in jobs if job.get('job') in ['Novel', 'Comic Book', 'Graphic Novel']]
        if not relevant_jobs:
            continue
        
        person_details = MoviesService().get_person_details(person.get('id'))
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
        
        # Set role flags based on all jobs
        for job_data in relevant_jobs:
            job = job_data.get('job')
            
            if job == 'Novel':
                person_instance.is_novelist = True
            if job == 'Comic Book':
                person_instance.is_comic_artist = True
            if job == 'Graphic Novel':
                person_instance.is_graphic_novelist = True
        
        # Save the person instance with updated flags
        person_instance.save()
        
        # Create MediaPerson entries for each job
        for job_data in relevant_jobs:
            job = job_data.get('job')
            
            MediaPerson.objects.create(
                content_type=tvshow_content_type,
                object_id=tvshow.id,
                person=person_instance,
                role=job
            )

def process_seasons(tvshow, seasons_data, tvshows_service):
    """Process seasons and episodes for a TV show"""
    for season_data in seasons_data:
        season_number = season_data.get('season_number')
        
        # Get detailed season information
        season_details = tvshows_service.get_season_details(tvshow.tmdb_id, season_number)
        if not season_details:
            continue
        
        season = Season.objects.create(
            show=tvshow,
            title=season_details.get('name'),
            season_number=season_number,
            air_date=season_details.get('air_date'),
            overview=season_details.get('overview'),
            poster=f"https://image.tmdb.org/t/p/original{season_details.get('poster_path')}" if season_details.get('poster_path') else None,
            tmdb_id=season_details.get('id')
        )
        
        # Process episodes for this season
        episodes = season_details.get('episodes', [])
        for episode_data in episodes:
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

def process_creators(tvshow, creators):
    """Process creators for a TV show"""
    tvshow_content_type = ContentType.objects.get_for_model(tvshow)
    
    for person in creators:
        person_details = MoviesService().get_person_details(person.get('id'))
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
        
        MediaPerson.objects.create(
            content_type=tvshow_content_type,
            object_id=tvshow.id,
            person=person_instance,
            role="Creator"
        )

def add_to_tvshow_watchlist(tvshow, user_id):
    """Add TV show to watchlist"""
    Watchlist.objects.create(
        user_id=user_id,
        content_type=ContentType.objects.get_for_model(tvshow),
        object_id=tvshow.id
    )

def create_tvshow(tvshow_id, tvshow_poster=None, tvshow_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """Main function to create a TV show from TMDB ID"""
    tvshows_service = TVShowsService()
    
    # Get TV show details from API
    tvshow_details = tvshows_service.get_show_details(tvshow_id, append_to_response='videos,credits,keywords,external_ids')
    if not tvshow_details:
        return None
    
    # Extract basic TV show data
    tvshow_dict = extract_tvshow_data(tvshow_details, tvshow_poster, tvshow_backdrop, is_anime)
    
    # Process metadata
    genre_instances, country_instances, keyword_instances = process_genres_countries_keywords(tvshow_details)

    # Create the TV show instance with proper user assignment
    user = CustomUser.objects.filter(id=user_id).first() if user_id else None
    tvshow = TVShow.objects.create(**tvshow_dict, added_by=user)
    tvshow.genres.set(genre_instances)
    tvshow.countries.set(country_instances)
    tvshow.keywords.set(keyword_instances)
    
    # Add to watchlist if specified
    if add_to_watchlist and user_id:
        add_to_tvshow_watchlist(tvshow, user_id)
    
    # process creators
    creators = tvshow_details.get('created_by', [])
    process_creators(tvshow, creators)

    # Process cast and crew
    credits = tvshows_service.get_show_credits(tvshow_id)
    process_cast(tvshow, credits.get('cast', []), tvshows_service)
    process_crew(tvshow, credits.get('crew', []), tvshows_service)
    
    # Process seasons and episodes
    process_seasons(tvshow, tvshow_details.get('seasons', []), tvshows_service)
    
    return tvshow