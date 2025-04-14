from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html

# Register your models here.
from .models import CustomUser, Genre, Keyword, Country, Watchlist, Review, Person, MediaPerson
from movies.models import Movie

admin.site.register(CustomUser)
admin.site.register(Genre)
admin.site.register(Keyword)
admin.site.register(Country)
admin.site.register(Watchlist)


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
                url = reverse('admin:movies_movie_change', args=[movie_id])
                html += f'<li><a href="{url}">{movie.title}</a> ({", ".join(movie_roles[movie_id])})</li>'
        html += "</ul>"
        return format_html(html)
    
    display_connected_movies.short_description = "Connected Movies"


admin.site.register(Person, PersonAdmin)

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    # Fields to display in the list view
    list_display = ('user', 'get_content_name', 'rating', 'date_added')
    
    # Add filters on the right side
    list_filter = ('rating', 'content_type', 'date_added')
    
    # Add search capability
    search_fields = ('user__username', 'review_text')
    
    # Default ordering
    ordering = ('-date_added',)
    
    # Fields to organize in the detail view
    fieldsets = (
        ('Review Information', {
            'fields': ('user', 'content_type', 'object_id', 'season', 'rating')
        }),
        ('Content', {
            'fields': ('review_text',)
        }),
    )
    
    def get_content_name(self, obj):
        """Return a string representation of the content being reviewed"""
        if hasattr(obj, 'content_object') and obj.content_object:
            return str(obj.content_object)
        return f"{obj.content_type} #{obj.object_id}"
    
    get_content_name.short_description = 'Content'