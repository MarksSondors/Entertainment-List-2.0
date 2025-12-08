from .models import *
from api.services.movies import MoviesService
from django.contrib.contenttypes.models import ContentType

def extract_movie_data(movie_details, movie_poster=None, movie_backdrop=None, is_anime=False):
    """Extract and format basic movie data from API response"""
    if not movie_poster:
        movie_poster = f"https://image.tmdb.org/t/p/original{movie_details.get('poster_path')}"
    if not movie_backdrop:
        movie_backdrop = f"https://image.tmdb.org/t/p/original{movie_details.get('backdrop_path')}"
    
    youtube_videos = [video for video in movie_details.get('videos', {}).get('results', []) 
                    if video.get('site') == 'YouTube' and video.get('type') == 'Trailer']
    trailer_key = youtube_videos[0].get('key') if youtube_videos else None
    trailer_link = f'https://www.youtube.com/embed/{trailer_key}' if trailer_key else None

    if is_anime == True:
        production_countries = movie_details.get('production_countries', [])
        is_anime = any(country.get('iso_3166_1') == 'JP' for country in production_countries)

    release_date = movie_details.get('release_date')
    if release_date and len(release_date) < 10:
        release_date = None

    return {
        'title': movie_details.get('title'),
        'original_title': movie_details.get('original_title'),
        'poster': movie_poster,
        'backdrop': movie_backdrop,
        'release_date': movie_details.get('release_date'),
        'tmdb_id': movie_details.get('id'),
        'imdb_id': movie_details.get('imdb_id'),
        'runtime': movie_details.get('runtime'),
        'description': movie_details.get('overview'),
        'rating': movie_details.get('vote_average'),
        'trailer': trailer_link,
        'is_anime': is_anime,
        'status': movie_details.get('status'),
    }

def process_genres_countries_keywords(movie_details):
    """Process and create genre, country and keyword instances"""
    # Process genres
    genres = movie_details.get('genres', [])
    genre_instances = []
    for genre in genres:
        genre_instance, _ = Genre.objects.get_or_create(
            tmdb_id=genre.get('id'),
            defaults={'name': genre.get('name')}
        )
        genre_instances.append(genre_instance)

    # Process countries
    countries = movie_details.get('production_countries', [])
    country_instances = []
    for country in countries:
        country_instance, _ = Country.objects.get_or_create(
            iso_3166_1=country.get('iso_3166_1'),
            defaults={'name': country.get('name')}
        )
        country_instances.append(country_instance)

    # Process keywords
    keywords = movie_details.get('keywords', {}).get('keywords', [])
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

def process_production_companies(movie_details):
    """Process and create production company instances"""
    companies = movie_details.get('production_companies', [])
    company_instances = []
    
    for company in companies:
        origin_country = None
        if company.get('origin_country') and company.get('origin_country') != "":
            try:
                origin_country = Country.objects.get(iso_3166_1=company.get('origin_country'))
            except Country.DoesNotExist:
                # If country doesn't exist, we can skip it or create it
                pass
                
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
    
    return company_instances

def process_collection(movie, movie_details, movies_service):
    """Process and associate collection if applicable"""
    belongs_to_collection = movie_details.get('belongs_to_collection')
    collection_id = belongs_to_collection.get('id') if belongs_to_collection else None
    
    if collection_id:
        collection_details = movies_service.get_collection_details(collection_id)
        if collection_details:
            collection, _ = Collection.objects.get_or_create(
                tmdb_id=collection_details.get('id'),
                defaults={
                    'name': collection_details.get('name'),
                    'description': collection_details.get('overview'),
                    'poster': f"https://image.tmdb.org/t/p/original{collection_details.get('poster_path')}" if collection_details.get('poster_path') else None,
                    'backdrop': f"https://image.tmdb.org/t/p/original{collection_details.get('backdrop_path')}" if collection_details.get('backdrop_path') else None
                }
            )
            movie.collection = collection
            movie.save()

def process_cast(movie, cast, movies_service):
    """Process cast members from movie credits"""
    movie_content_type = ContentType.objects.get_for_model(movie)
    
    for index, person in enumerate(cast):
        # Check if the person already exists in the database
        person_instance = Person.objects.filter(tmdb_id=person.get('id')).first()
        
        if not person_instance:
            # Fetch person details and create if not found
            person_details = movies_service.get_person_details(person.get('id'))
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
        
        # Create MediaPerson entry
        MediaPerson.objects.create(
            content_type=movie_content_type,
            object_id=movie.id,
            person=person_instance,
            role="Actor",
            character_name=person.get('character'),
            order=person.get('order', index)
        )

def process_crew(movie, crew, movies_service):
    """Process crew members from movie credits"""
    movie_content_type = ContentType.objects.get_for_model(movie)
    relevant_crew = [person for person in crew if person.get('department') in ['Directing', 'Writing', 'Sound']]
    
    for person in relevant_crew:
        job = person.get('job')
        department = person.get('department')
        
        # Skip if not one of our supported roles
        if (department == 'Directing' and job != 'Director') or \
           (department == 'Writing' and job not in ['Original Story', 'Screenplay', 'Writer', 'Story', 'Novel', 'Comic Book', 'Graphic Novel', 'Book']) or \
           (department == 'Sound' and job != 'Original Music Composer'):
            continue
        
        person_details = movies_service.get_person_details(person.get('id'))
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
        
        # Set the appropriate role
        role = job
        if job == 'Director':
            person_instance.is_director = True
        elif job in ['Original Story', 'Screenplay', 'Writer', 'Story', 'Novel', 'Comic Book', 'Graphic Novel','Book']:
            if job == 'Book':
                person_instance.is_book = True
            if job == 'Writer':
                person_instance.is_writer = True
            if job == 'Story':
                person_instance.is_story = True
            if job == 'Original Story':
                person_instance.is_original_story = True
            if job == 'Screenplay':
                person_instance.is_screenwriter = True
            if job == 'Novel':
                person_instance.is_novelist = True
            if job == 'Comic Book':
                person_instance.is_comic_artist = True
            if job == 'Graphic Novel':
                person_instance.is_graphic_novelist = True
        elif job == 'Original Music Composer':
            person_instance.is_original_music_composer = True
        
        person_instance.save()
        
        MediaPerson.objects.create(
            content_type=movie_content_type,
            object_id=movie.id,
            person=person_instance,
            role=role
        )

def add_to_movie_watchlist(movie, user_id):
    """Add movie to watchlist"""
    Watchlist.objects.create(
        user_id=user_id,
        content_type=ContentType.objects.get_for_model(movie),
        object_id=movie.id
    )

def create_movie_basic(movie_id, movie_poster=None, movie_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """Create a movie with basic information only - fast creation for immediate user feedback"""
    movies_service = MoviesService()
    
    # Get movie details from API
    movie_details = movies_service.get_movie_details(movie_id, append_to_response='videos,keywords')
    if not movie_details:
        return None
    
    # Extract basic movie data
    movie_dict = extract_movie_data(movie_details, movie_poster, movie_backdrop, is_anime)
    
    # Process only essential metadata (genres, countries, keywords - quick operations)
    genre_instances, country_instances, keyword_instances = process_genres_countries_keywords(movie_details)
    
    # Create the movie instance with basic information
    movie = Movie.objects.create(**movie_dict, added_by=CustomUser.objects.filter(id=user_id).first() if user_id else None)
    movie.genres.set(genre_instances)
    movie.countries.set(country_instances)
    movie.keywords.set(keyword_instances)
    
    # Add to watchlist if specified
    if add_to_watchlist:
        add_to_movie_watchlist(movie, user_id)
    
    return movie

def create_movie(movie_id, movie_poster=None, movie_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """Main function to create a movie from TMDB ID - complete with all foreign objects"""
    movies_service = MoviesService()
    
    # Get movie details from API
    movie_details = movies_service.get_movie_details(movie_id, append_to_response='videos,credits,keywords')
    if not movie_details:
        return None
    
    # Extract basic movie data
    movie_dict = extract_movie_data(movie_details, movie_poster, movie_backdrop, is_anime)
    
    # Process metadata
    genre_instances, country_instances, keyword_instances = process_genres_countries_keywords(movie_details)
    company_instances = process_production_companies(movie_details)
    
    # Create the movie instance
    movie = Movie.objects.create(**movie_dict, added_by=CustomUser.objects.filter(id=user_id).first() if user_id else None)
    movie.genres.set(genre_instances)
    movie.countries.set(country_instances)
    movie.keywords.set(keyword_instances)
    movie.production_companies.set(company_instances)
    
    # Handle collection
    process_collection(movie, movie_details, movies_service)
    
    # Add to watchlist if specified
    if add_to_watchlist:
        add_to_movie_watchlist(movie, user_id)
    
    # Process cast and crew
    credits = movie_details.get('credits', {})
    process_cast(movie, credits.get('cast', []), movies_service)
    process_crew(movie, credits.get('crew', []), movies_service)
    
    return movie

def enrich_movie_with_details(movie_id):
    """Enrich an existing movie with production companies, collections, cast and crew"""
    try:
        movie = Movie.objects.get(id=movie_id)
        movies_service = MoviesService()
        
        # Get detailed movie information including credits
        movie_details = movies_service.get_movie_details(movie.tmdb_id, append_to_response='credits')
        if not movie_details:
            return movie
        
        # Process production companies
        company_instances = process_production_companies(movie_details)
        movie.production_companies.set(company_instances)
        
        # Handle collection
        process_collection(movie, movie_details, movies_service)
        
        # Process cast and crew
        credits = movie_details.get('credits', {})
        if credits:
            process_cast(movie, credits.get('cast', []), movies_service)
            process_crew(movie, credits.get('crew', []), movies_service)
        
        return movie
        
    except Movie.DoesNotExist:
        return None