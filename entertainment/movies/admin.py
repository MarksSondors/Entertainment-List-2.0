from django.contrib import admin
from django.utils.html import format_html
from custom_auth.models import Movie, MediaPerson, Person
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

class PersonAdmin(admin.ModelAdmin):
    search_fields = ['name', 'imdb_id']
    list_display = ('name', 'is_director', 'is_writer', 'is_original_music_composer')
    list_filter = ('is_director', 'is_writer', 'is_screenwriter', 'is_story', 'is_original_music_composer')
    readonly_fields = ('display_connected_movies',)
    
    def display_connected_movies(self, obj):
        movie_content_type = ContentType.objects.get_for_model(Movie)
        
        # Get all MediaPerson entries for this person related to movies
        media_persons = MediaPerson.objects.filter(
            person=obj,
            content_type=movie_content_type
        )
        
        if not media_persons.exists():
            return "-"
        
        # Get all movies in one query
        movie_ids = media_persons.values_list('object_id', flat=True).distinct()
        movies = {movie.id: movie for movie in Movie.objects.filter(id__in=movie_ids)}
        
        # Organize roles by movie
        movie_roles = {}
        for mp in media_persons:
            if mp.object_id not in movie_roles:
                movie_roles[mp.object_id] = []
            
            role_info = mp.role
            if mp.character_name:
                role_info += f" as {mp.character_name}"
            movie_roles[mp.object_id].append(role_info)
        
        # Build HTML list with clickable links to movie admin pages
        html = "<ul>"
        for movie_id in sorted(movie_roles.keys(), key=lambda x: movies[x].title if x in movies else ""):
            if movie_id in movies:
                movie = movies[movie_id]
                url = reverse('admin:custom_auth_movie_change', args=[movie_id])
                html += f'<li><a href="{url}">{movie.title}</a> ({", ".join(movie_roles[movie_id])})</li>'
        html += "</ul>"
        return format_html(html)
    
    display_connected_movies.short_description = "Connected Movies"


admin.site.register(Person, PersonAdmin)

class MediaPersonInline(GenericTabularInline):
    model = MediaPerson
    extra = 1
    fields = ('person', 'role', 'character_name', 'order')
    autocomplete_fields = ['person']

class MovieAdmin(admin.ModelAdmin):
    list_display = ('title', 'release_date', 'rating')
    search_fields = ('title', 'original_title')
    list_filter = ('genres', 'countries', 'release_date')
    filter_horizontal = ('genres', 'countries', 'keywords')
    readonly_fields = ('display_directors', 'display_writers', 
                      'display_cast', 'display_composers', 'minutes_to_hours')
    inlines = [MediaPersonInline]
    
    def display_directors(self, obj):
        return self._format_people_list(obj.directors)
    display_directors.short_description = "Directors"
    
    def display_writers(self, obj):
        return self._format_people_list(obj.writers)
    display_writers.short_description = "Writers"
    
    def display_cast(self, obj):
        cast = obj.get_media_persons(role="Actor")
        html = "<ul>"
        for media_person in cast:
            character = f" as {media_person.character_name}" if media_person.character_name else ""
            html += f"<li>{media_person.person.name}{character}</li>"
        html += "</ul>"
        return format_html(html)
    display_cast.short_description = "Cast"
    
    def display_composers(self, obj):
        return self._format_people_list(obj.composers)
    display_composers.short_description = "Composers"
    
    def _format_people_list(self, people):
        if not people:
            return "-"
        html = "<ul>"
        for person in people:
            html += f"<li>{person.name}</li>"
        html += "</ul>"
        return format_html(html)

admin.site.register(Movie, MovieAdmin)