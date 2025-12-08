from .models import *
from api.services.tvshows import TVShowsService
from api.services.movies import MoviesService
from api.services.tvdb import TVDBService
from django.contrib.contenttypes.models import ContentType
from custom_auth.models import CustomUser
from datetime import datetime, date
import zoneinfo
import pytz

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
        'tvdb_id': tvshow_details.get('external_ids', {}).get('tvdb_id'),
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
    for keyword_data in keywords:
        keyword_name = keyword_data.get('name')
        keyword_id = keyword_data.get('id')
        
        if not keyword_name:
            continue
            
        # Try to find by TMDB ID first
        keyword_instance = Keyword.objects.filter(tmdb_id=keyword_id).first()
        
        if not keyword_instance:
            # Try to find by name (case insensitive)
            keyword_instance = Keyword.objects.filter(name__iexact=keyword_name).first()
            if keyword_instance:
                # Update TMDB ID if found by name and missing
                if not keyword_instance.tmdb_id:
                    keyword_instance.tmdb_id = keyword_id
                    keyword_instance.save(update_fields=['tmdb_id'])
            else:
                # Create new if not found
                keyword_instance = Keyword.objects.create(
                    name=keyword_name,
                    tmdb_id=keyword_id
                )
                
        keyword_instances.append(keyword_instance)

    return genre_instances, country_instances, keyword_instances

def process_cast(tvshow, cast, tvshows_service):
    """Process cast members from TV show credits"""
    tvshow_content_type = ContentType.objects.get_for_model(tvshow)
    
    for index, person in enumerate(cast):
        # Check if person already exists in the database
        person_instance = Person.objects.filter(tmdb_id=person.get('id')).first()
        
        # If person doesn't exist, fetch details and create a new instance
        if not person_instance:
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
        
        # Set as actor if not already set
        if not person_instance.is_actor:
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
        relevant_jobs = [job for job in jobs if job.get('job') in ['Novel', 'Comic Book', 'Graphic Novel', 'Book']]
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
            
            if job == 'Book':
                person_instance.is_book = True
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

        person_instance.is_tv_creator = True
        person_instance.save()
        
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

    # If we have a TVDB ID, try to fetch accurate air dates and update episodes
    if tvshow.tvdb_id:
        try:
            tvdb_service = TVDBService()
            series_extended = tvdb_service.get_series_extended(tvshow.tvdb_id)
            
            if series_extended and 'data' in series_extended and 'episodes' in series_extended['data']:
                tvdb_episodes = series_extended['data']['episodes']
                
                # Get series air time
                series_air_time = series_extended['data'].get('airsTime')

                # Create a map of (season_number, episode_number) -> tvdb_episode_data
                tvdb_ep_map = {}
                for ep in tvdb_episodes:
                    s_num = ep.get('seasonNumber')
                    e_num = ep.get('number')
                    if s_num is not None and e_num is not None:
                        tvdb_ep_map[(s_num, e_num)] = ep
                
                # Iterate over our episodes and update if needed
                for season in tvshow.seasons.all():
                    for episode in season.episodes.all():
                        tvdb_ep = tvdb_ep_map.get((season.season_number, episode.episode_number))
                        if tvdb_ep:
                            updated = False
                            # Update TVDB ID
                            if not episode.tvdb_id:
                                episode.tvdb_id = tvdb_ep.get('id')
                                updated = True
                            
                            # Update air date if missing or if we want to trust TVDB more
                            if tvdb_ep.get('aired'):
                                try:
                                    tvdb_date = date.fromisoformat(tvdb_ep.get('aired'))
                                    if episode.air_date != tvdb_date:
                                        episode.air_date = tvdb_date
                                        updated = True
                                    
                                    # Update air time (datetime)
                                    if series_air_time:
                                        # Try parsing common formats
                                        parsed_time = None
                                        for fmt in ["%H:%M", "%I:%M %p"]:
                                            try:
                                                parsed_time = datetime.strptime(series_air_time, fmt).time()
                                                break
                                            except ValueError:
                                                continue
                                        
                                        if parsed_time:
                                            # Determine timezone
                                            tz_name = "UTC"
                                            
                                            # Manual map for major countries
                                            MANUAL_TIMEZONES = {
                                                'US': 'America/New_York',
                                                'GB': 'Europe/London',
                                                'JP': 'Asia/Tokyo',
                                                'KR': 'Asia/Seoul',
                                                'CN': 'Asia/Shanghai',
                                                'IN': 'Asia/Kolkata',
                                                'CA': 'America/Toronto',
                                                'AU': 'Australia/Sydney',
                                                'FR': 'Europe/Paris',
                                                'DE': 'Europe/Berlin',
                                                'IT': 'Europe/Rome',
                                                'ES': 'Europe/Madrid',
                                                'BR': 'America/Sao_Paulo',
                                                'RU': 'Europe/Moscow',
                                            }

                                            # Get countries
                                            countries = list(tvshow.countries.values_list('iso_3166_1', flat=True))
                                            
                                            # Check for manual overrides first
                                            found_manual = False
                                            for code in countries:
                                                if code in MANUAL_TIMEZONES:
                                                    tz_name = MANUAL_TIMEZONES[code]
                                                    found_manual = True
                                                    break
                                            
                                            # If no manual override, try pytz
                                            if not found_manual and countries:
                                                try:
                                                    # Use the first country
                                                    country_code = countries[0]
                                                    timezones = pytz.country_timezones.get(country_code, [])
                                                    if timezones:
                                                        tz_name = timezones[0]
                                                except Exception:
                                                    pass

                                            try:
                                                tz = zoneinfo.ZoneInfo(tz_name)
                                                dt = datetime.combine(tvdb_date, parsed_time)
                                                from django.utils import timezone
                                                dt_aware = timezone.make_aware(dt, timezone=tz)
                                                
                                                if episode.air_time != dt_aware:
                                                    episode.air_time = dt_aware
                                                    updated = True
                                            except Exception as e:
                                                print(f"Error setting timezone {tz_name} for {tvshow.title}: {e}")
                                except (ValueError, TypeError):
                                    pass
                            
                            if updated:
                                episode.save()
        except Exception as e:
            print(f"Error updating episodes from TVDB: {e}")

    # Type	Name
    # 1	Original air date
    # 2	Absolute
    # 3	DVD
    # 4	Digital
    # 5	Story arc
    # 6	Production
    # 7	TV
    # if is_anime is on lets try and look for episode groups
    if is_anime:
        # lets only get the episode groups that are useful like: Production and story arc
        episode_groups_response = tvshows_service.get_episode_groups(tvshow_id)
        
        # Check if we have a proper response and extract the results array
        if episode_groups_response and isinstance(episode_groups_response, dict):
            episode_groups = episode_groups_response.get('results', [])
            filtered_groups = [group for group in episode_groups if group.get('type') in [6, 5]]
            
            for group in filtered_groups:
                # Create the top-level episode group
                episode_group = EpisodeGroup.objects.create(
                    show=tvshow,
                    name=group.get('name'),
                    tmdb_id=group.get('id')
                )
                
                # get the episodes and subgroups
                group_details = tvshows_service.get_episode_group_details(group.get('id'))
                if group_details and 'groups' in group_details:
                    for idx, season_group in enumerate(group_details.get('groups', [])):
                        # Create a subgroup for each group returned from the API
                        subgroup_name = season_group.get('name', f"Part {idx+1}")
                        subgroup = EpisodeSubGroup.objects.create(
                            parent_group=episode_group,
                            name=subgroup_name,
                            tmdb_id=season_group.get('id', None),
                            order=idx  # Maintain the order from the API
                        )
                        
                        # Find and associate episodes with this subgroup
                        episode_instances = []
                        for episode_item in season_group.get('episodes', []):
                            try:
                                season = Season.objects.get(show=tvshow, season_number=episode_item.get('season_number'))
                                episode = Episode.objects.get(
                                    season=season, 
                                    episode_number=episode_item.get('episode_number')
                                )
                                episode_instances.append(episode)
                            except (Season.DoesNotExist, Episode.DoesNotExist):
                                # Skip if we can't find the corresponding episode
                                continue
                        
                        # Associate all found episodes with this subgroup
                        if episode_instances:
                            subgroup.episodes.add(*episode_instances)
    return tvshow