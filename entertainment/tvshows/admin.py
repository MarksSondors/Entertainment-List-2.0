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
    list_display = ('title', 'first_air_date', 'rating',
                    'date_added', 'date_updated'
                    )
    search_fields = ('title', 'original_title')
    list_filter = ('genres', 'countries', 'first_air_date')
    filter_horizontal = ('genres', 'countries', 'keywords')
    readonly_fields = ('display_creators', 'display_seasons_count')
    inlines = [MediaPersonInline]
    
    def display_creators(self, obj):
        return self._format_people_list(obj.creators)
    display_creators.short_description = "Creators"
    
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

class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 0
    fields = ('season', 'episode_number', 'title', 'air_date')
    readonly_fields = ('season', 'episode_number', 'title', 'air_date')
    can_delete = False
    show_change_link = True
    
    def has_add_permission(self, request, obj=None):
        return False

class EpisodeSubGroupInline(admin.TabularInline):
    model = EpisodeSubGroup
    extra = 1
    fields = ('name', 'order', 'description')

class EpisodeGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'show', 'order', 'subgroups_count')
    list_filter = ('show',)
    search_fields = ('name', 'show__title')
    ordering = ('order', 'name')
    inlines = [EpisodeSubGroupInline]
    
    def subgroups_count(self, obj):
        return obj.sub_groups.count()
    subgroups_count.short_description = "Subgroups"

class EpisodeSubGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent_group', 'order', 'episodes_count')
    list_filter = ('parent_group__show', 'parent_group')
    search_fields = ('name', 'parent_group__name')
    ordering = ('parent_group__order', 'order', 'name')
    filter_horizontal = ('episodes',)
    
    def episodes_count(self, obj):
        return obj.episodes.count()
    episodes_count.short_description = "Episodes"
    
    def get_show(self, obj):
        return obj.parent_group.show
    get_show.short_description = "Show"
    get_show.admin_order_field = "parent_group__show"

# Register models
admin.site.register(TVShow, TVShowAdmin)
admin.site.register(Season)
admin.site.register(Episode)
admin.site.register(EpisodeGroup, EpisodeGroupAdmin)
admin.site.register(EpisodeSubGroup, EpisodeSubGroupAdmin)
admin.site.register(WatchedEpisode)

