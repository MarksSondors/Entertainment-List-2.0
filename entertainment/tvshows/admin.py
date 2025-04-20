from django.contrib import admin
from django.utils.html import format_html
from .models import *
from django.contrib.contenttypes.admin import GenericTabularInline
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.safestring import mark_safe

class MediaPersonInline(GenericTabularInline):
    model = MediaPerson
    extra = 1
    fields = ('person', 'role', 'character_name', 'order')
    autocomplete_fields = ['person']

class SeasonInline(admin.TabularInline):
    model = Season
    extra = 0
    fields = ('season_number', 'title', 'air_date', 'episode_count', 'view_episodes')
    readonly_fields = ('season_number', 'title', 'air_date', 'episode_count', 'view_episodes')
    can_delete = False
    show_change_link = True
    
    def has_add_permission(self, request, obj=None):
        return False
    
    def episode_count(self, obj):
        count = obj.episodes.count()
        return count
    episode_count.short_description = "Episodes"
    
    def view_episodes(self, obj):
        url = reverse('admin:tvshows_episode_changelist')
        return format_html('<a href="{}?season__id__exact={}" class="button">View Episodes</a>', url, obj.id)
    view_episodes.short_description = "Episodes"

class TVShowAdmin(admin.ModelAdmin):
    list_display = ('title', 'first_air_date', 'rating', 'seasons_count', 'episodes_count', 'status', 'date_updated')
    list_filter = ('genres', 'countries', 'first_air_date', 'status')
    search_fields = ('title', 'original_title')
    filter_horizontal = ('genres', 'countries', 'keywords')
    readonly_fields = ('display_creators', 'display_seasons', 'tmdb_link', 'update_button')
    inlines = [MediaPersonInline, SeasonInline]
    
    def display_creators(self, obj):
        return self._format_people_list(obj.creators)
    display_creators.short_description = "Creators"
    
    def seasons_count(self, obj):
        count = obj.seasons.count()
        url = reverse('admin:tvshows_season_changelist')
        return format_html('<a href="{}?show__id__exact={}">{} seasons</a>', url, obj.id, count)
    seasons_count.short_description = "Seasons"
    
    def episodes_count(self, obj):
        count = Episode.objects.filter(season__show=obj).count()
        url = reverse('admin:tvshows_episode_changelist')
        return format_html('<a href="{}?season__show__id__exact={}">{} episodes</a>', url, obj.id, count)
    episodes_count.short_description = "Episodes"
    
    def display_seasons(self, obj):
        seasons = obj.seasons.all().order_by('season_number')
        if not seasons:
            return "-"
            
        html = "<div style='display: flex; flex-wrap: wrap; gap: 10px;'>"
        for season in seasons:
            episodes = season.episodes.count()
            url = reverse('admin:tvshows_season_change', args=[season.id])
            html += f"""
                <div style='border: 1px solid #ccc; border-radius: 5px; padding: 8px; min-width: 180px;'>
                    <strong><a href='{url}'>Season {season.season_number}</a></strong>
                    <div>{season.title}</div>
                    <div>{episodes} episodes</div>
                    <div>{season.air_date or 'No air date'}</div>
                </div>
            """
        html += "</div>"
        return mark_safe(html)
    display_seasons.short_description = "Seasons"
    
    def tmdb_link(self, obj):
        if obj.tmdb_id:
            url = f"https://www.themoviedb.org/tv/{obj.tmdb_id}"
            return format_html('<a href="{}" target="_blank">View on TMDB</a>', url)
        return "-"
    tmdb_link.short_description = "TMDB"
    
    def update_button(self, obj):
        if obj.id:
            url = reverse('admin:tvshows_tvshow_changelist')
            return format_html(
                '<a href="/admin/tvshows/update/?id={}" class="button">Update from TMDB</a>', 
                obj.id
            )
        return ""
    update_button.short_description = "Actions"
    
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
    fields = ('episode_number', 'title', 'air_date', 'runtime', 'rating')
    readonly_fields = ('episode_number', 'title', 'air_date', 'runtime', 'rating')
    can_delete = False
    show_change_link = True
    
    def has_add_permission(self, request, obj=None):
        return False

class SeasonAdmin(admin.ModelAdmin):
    list_display = ('title', 'show_link', 'season_number', 'air_date', 'episode_count')
    list_filter = ('show', 'air_date')
    search_fields = ('title', 'show__title')
    readonly_fields = ('show', 'season_number', 'display_episodes', 'tmdb_link')
    inlines = [EpisodeInline]
    
    def episode_count(self, obj):
        count = obj.episodes.count()
        url = reverse('admin:tvshows_episode_changelist')
        return format_html('<a href="{}?season__id__exact={}">{} episodes</a>', url, obj.id, count)
    episode_count.short_description = "Episodes"
    
    def show_link(self, obj):
        url = reverse('admin:tvshows_tvshow_change', args=[obj.show.id])
        return format_html('<a href="{}">{}</a>', url, obj.show.title)
    show_link.short_description = "Show"
    show_link.admin_order_field = "show__title"
    
    def display_episodes(self, obj):
        episodes = obj.episodes.all().order_by('episode_number')
        if not episodes:
            return "No episodes"
            
        html = "<div style='display: flex; flex-wrap: wrap; gap: 10px;'>"
        for episode in episodes:
            url = reverse('admin:tvshows_episode_change', args=[episode.id])
            watched_count = episode.watched_by.count()
            html += f"""
                <div style='border: 1px solid #ccc; border-radius: 5px; padding: 8px; margin-bottom: 10px; width: 220px;'>
                    <strong><a href='{url}'>E{episode.episode_number}: {episode.title}</a></strong>
                    <div>{episode.air_date or 'No air date'}</div>
                    <div>Rating: {episode.rating or 'N/A'}</div>
                    <div>Watched by: {watched_count} users</div>
                </div>
            """
        html += "</div>"
        return mark_safe(html)
    display_episodes.short_description = "Episodes"
    
    def tmdb_link(self, obj):
        if obj.tmdb_id:
            url = f"https://www.themoviedb.org/tv/{obj.show.tmdb_id}/season/{obj.season_number}"
            return format_html('<a href="{}" target="_blank">View on TMDB</a>', url)
        return "-"
    tmdb_link.short_description = "TMDB"

class EpisodeAdmin(admin.ModelAdmin):
    list_display = ('episode_title_with_number', 'show_title', 'season_info', 'air_date', 'runtime', 'rating', 'watched_count')
    list_filter = ('season__show', 'season', 'air_date')
    search_fields = ('title', 'season__show__title', 'overview')
    readonly_fields = ('season', 'episode_number', 'tmdb_link', 'show_navigation', 'watched_by_users')
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('season__show')
    
    def episode_title_with_number(self, obj):
        return f"E{obj.episode_number}: {obj.title}"
    episode_title_with_number.short_description = "Episode"
    
    def show_title(self, obj):
        url = reverse('admin:tvshows_tvshow_change', args=[obj.season.show.id])
        return format_html('<a href="{}">{}</a>', url, obj.season.show.title)
    show_title.short_description = "Show"
    show_title.admin_order_field = "season__show__title"
    
    def season_info(self, obj):
        url = reverse('admin:tvshows_season_change', args=[obj.season.id])
        return format_html('<a href="{}">Season {}</a>', url, obj.season.season_number)
    season_info.short_description = "Season"
    
    def watched_count(self, obj):
        return obj.watched_by.count()
    watched_count.short_description = "Watched By"
    
    def tmdb_link(self, obj):
        if obj.tmdb_id:
            show = obj.season.show
            url = f"https://www.themoviedb.org/tv/{show.tmdb_id}/season/{obj.season.season_number}/episode/{obj.episode_number}"
            return format_html('<a href="{}" target="_blank">View on TMDB</a>', url)
        return "-"
    tmdb_link.short_description = "TMDB"
    
    def show_navigation(self, obj):
        show = obj.season.show
        show_url = reverse('admin:tvshows_tvshow_change', args=[show.id])
        season_url = reverse('admin:tvshows_season_change', args=[obj.season.id])
        
        # Get previous and next episodes
        try:
            prev_ep = Episode.objects.filter(
                season=obj.season, 
                episode_number__lt=obj.episode_number
            ).order_by('-episode_number')[0]
            prev_url = reverse('admin:tvshows_episode_change', args=[prev_ep.id])
            prev_link = f'<a href="{prev_url}" class="button">← E{prev_ep.episode_number}: {prev_ep.title}</a>'
        except (IndexError, Episode.DoesNotExist):
            prev_link = ""
            
        try:
            next_ep = Episode.objects.filter(
                season=obj.season, 
                episode_number__gt=obj.episode_number
            ).order_by('episode_number')[0]
            next_url = reverse('admin:tvshows_episode_change', args=[next_ep.id])
            next_link = f'<a href="{next_url}" class="button">E{next_ep.episode_number}: {next_ep.title} →</a>'
        except (IndexError, Episode.DoesNotExist):
            next_link = ""
            
        html = f"""
        <div style="margin-bottom: 15px;">
            <a href="{show_url}" class="button">Show: {show.title}</a>
            <a href="{season_url}" class="button">Season {obj.season.season_number}</a>
        </div>
        <div style="display: flex; justify-content: space-between;">
            <div>{prev_link}</div>
            <div>{next_link}</div>
        </div>
        """
        return mark_safe(html)
    show_navigation.short_description = "Navigation"
    
    def watched_by_users(self, obj):
        watched = obj.watched_by.all()
        if not watched:
            return "Not watched by any users"
            
        html = "<ul>"
        for watch in watched:
            html += f"<li>{watch.user.username} - {watch.watched_date}</li>"
        html += "</ul>"
        return mark_safe(html)
    watched_by_users.short_description = "Watched By"

class EpisodeSubGroupInline(admin.TabularInline):
    model = EpisodeSubGroup
    extra = 1
    fields = ('name', 'order', 'description', 'episode_count')
    readonly_fields = ('episode_count',)
    
    def episode_count(self, obj):
        if obj.pk:
            count = obj.episodes.count()
            return count
        return "-"
    episode_count.short_description = "Episodes"

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
    list_display = ('name', 'parent_group', 'show_title', 'order', 'episodes_count')
    list_filter = ('parent_group__show', 'parent_group')
    search_fields = ('name', 'parent_group__name')
    ordering = ('parent_group__order', 'order', 'name')
    filter_horizontal = ('episodes',)
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('parent_group__show')
    
    def episodes_count(self, obj):
        return obj.episodes.count()
    episodes_count.short_description = "Episodes"
    
    def show_title(self, obj):
        show = obj.parent_group.show
        url = reverse('admin:tvshows_tvshow_change', args=[show.id])
        return format_html('<a href="{}">{}</a>', url, show.title)
    show_title.short_description = "Show"
    show_title.admin_order_field = "parent_group__show__title"

# Register models
admin.site.register(TVShow, TVShowAdmin)
admin.site.register(Season, SeasonAdmin)
admin.site.register(Episode, EpisodeAdmin)
admin.site.register(EpisodeGroup, EpisodeGroupAdmin)
admin.site.register(EpisodeSubGroup, EpisodeSubGroupAdmin)
admin.site.register(WatchedEpisode)

