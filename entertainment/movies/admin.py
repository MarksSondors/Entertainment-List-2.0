from django.contrib import admin
from django.utils.html import format_html
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from .models import *

admin.site.register(Collection)
admin.site.register(MovieOfWeekPick)

class MediaPersonInline(GenericTabularInline):
    model = MediaPerson
    extra = 1
    fields = ('person', 'role', 'character_name', 'order')
    autocomplete_fields = ['person']

class MovieAdmin(admin.ModelAdmin):
    list_display = ('title', 'release_date', 'rating')
    search_fields = ('title', 'original_title')
    list_filter = ('genres', 'countries', 'release_date')
    filter_horizontal = ('genres', 'countries', 'keywords', 'production_companies')
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