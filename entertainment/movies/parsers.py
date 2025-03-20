from .models import *
from api.services.movies import MoviesService

def create_movie(movie_id, movie_poster=None, movie_backdrop=None):
    movie_details = MoviesService().get_movie_details(movie_id)
    if not movie_details:
        return None
    if not movie_poster:
        movie_poster = f"https://image.tmdb.org/t/p/original{movie_details.get('poster_path')}"
    if not movie_backdrop:
        movie_backdrop = f"https://image.tmdb.org/t/p/original{movie_details.get('backdrop_path')}"
    movie_dict = {
        'title': movie_details.get('title'),
        'original_title': movie_details.get('original_title'),
        'poster': movie_poster,
        'backdrop': movie_backdrop,
        'release_date': movie_details.get('release_date'),
        'tmdb_id': movie_details.get('id'),
        'runtime': movie_details.get('runtime'),
        'plot': movie_details.get('overview'),
        'rating': movie_details.get('vote_average'),
        'trailer': movie_details.get('videos', {}).get('results', [{}])[0].get('key') if movie_details.get('videos', {}).get('results') else None,
    }
    genres = movie_details.get('genres', [])
    genre_instances = []
    for genre in genres:
        genre_instance, _ = Genre.objects.get_or_create(
            tmdb_id=genre.get('id'),
            defaults={'name': genre.get('name')}
        )
        genre_instances.append(genre_instance)

    countries = movie_details.get('production_countries', [])
    country_instances = []
    for country in countries:
        country_instance, _ = Country.objects.get_or_create(
            iso_3166_1=country.get('iso_3166_1'),
            defaults={'name': country.get('name')}
        )
        country_instances.append(country_instance)

    keywords = MoviesService().get_movie_keywords(movie_id).get('keywords', [])
    keyword_instances = []
    for keyword in keywords:
        keyword_instance, _ = Keyword.objects.get_or_create(
            tmdb_id=keyword.get('id'),
            defaults={'name': keyword.get('name')}
        )
        keyword_instances.append(keyword_instance)

    credits = MoviesService().get_movie_credits(movie_id)
    cast = credits.get('cast', [])
    cast_instances = []
    for person in cast:
        person_details = MoviesService().get_person_details(person.get('id'))
        person_instance, created = Person.objects.get_or_create(
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
        if not created:
            # Update roles if the person already exists
            person_instance.is_actor = person_instance.is_actor or True
        else:
            person_instance.is_actor = True
        person_instance.save()
        cast_instances.append(person_instance)

    crew = credits.get('crew', [])
    writers = []
    directors = []
    producers = []
    sound = []
    crew = [person for person in crew if person.get('department') in ['Directing', 'Writing', 'Production', 'Sound']]
    for person in crew:
        if person.get('department') == 'Directing' and person.get('job') == 'Director':
            person_details = MoviesService().get_person_details(person.get('id'))
            person_instance, created = Person.objects.get_or_create(
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
            person_instance.is_director = person_instance.is_director or True
            person_instance.save()
            directors.append(person_instance)
        elif person.get('department') == 'Writing' and (person.get('job') == 'Original Story' or person.get('job') == 'Screenplay'):
            person_details = MoviesService().get_person_details(person.get('id'))
            person_instance, created = Person.objects.get_or_create(
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
            person_instance.is_writer = person_instance.is_writer or True
            person_instance.save()
            writers.append(person_instance)
        elif person.get('department') == 'Production' and person.get('job') == 'Executive Producer':
            person_details = MoviesService().get_person_details(person.get('id'))
            person_instance, created = Person.objects.get_or_create(
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
            person_instance.is_producer = person_instance.is_producer or True
            person_instance.save()
            producers.append(person_instance)
        elif person.get('department') == 'Sound' and person.get('job') == 'Original Music Composer':
            person_details = MoviesService().get_person_details(person.get('id'))
            person_instance, created = Person.objects.get_or_create(
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
            person_instance.is_composer = person_instance.is_composer or True
            person_instance.save()
            sound.append(person_instance)

    movie = Movie.objects.create(**movie_dict)
    movie.genres.set(genre_instances)
    movie.countries.set(country_instances)
    movie.keywords.set(keyword_instances)
    movie.cast.set(cast_instances)
    movie.directors.set(directors)
    movie.writers.set(writers)
    movie.producers.set(producers)
    movie.composer.set(sound)
    
    return movie