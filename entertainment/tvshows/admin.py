from django.contrib import admin
from django.utils.html import format_html
from .models import *
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

class MediaPersonInline(GenericTabularInline):
    model = MediaPerson
    extra = 1
    fields = ('person', 'role', 'character_name', 'order')
    autocomplete_fields = ['person']

class TVShowAdmin(admin.ModelAdmin):
    list_display = ('title', 'first_air_date', 'rating')
    search_fields = ('title', 'original_title')
    list_filter = ('genres', 'countries', 'first_air_date')
    filter_horizontal = ('genres', 'countries', 'keywords')
    readonly_fields = ('display_creators', 'display_writers', 
                      'display_cast', 'display_seasons_count')
    inlines = [MediaPersonInline]
    
    def display_creators(self, obj):
        return self._format_people_list(obj.creators)
    display_creators.short_description = "Creators"
    
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
    
    def display_seasons_count(self, obj):
        return f"{obj.seasons_count} season(s)"
    display_seasons_count.short_description = "Seasons"
    
    def _format_people_list(self, people):
        if not people:
            return "-"
        html = "<ul>"
        for person in people:
            html += f"<li>{person.name}</li>"
        html += "</ul>"
        return format_html(html)

admin.site.register(TVShow, TVShowAdmin)