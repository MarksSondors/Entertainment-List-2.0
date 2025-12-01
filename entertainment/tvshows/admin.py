from django.contrib import admin
from django.utils.html import format_html
from .models import *
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.safestring import mark_safe
from custom_auth.models import MediaPerson


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
    list_display = ('title', 'first_air_date', 'rating', 'seasons_count', 'status', 'date_updated')
    list_filter = ('status', 'is_anime', 'first_air_date')
    search_fields = ('title', 'original_title')
    filter_horizontal = ('genres', 'countries')
    readonly_fields = ('display_cast_crew', 'display_seasons', 'tmdb_link', 'update_button')
    
    # NO MediaPersonInline - causes N+1 queries that can't be optimized
    inlines = [SeasonInline]
    
    # Performance settings
    show_full_result_count = False
    list_per_page = 25
    
    # Exclude heavy M2M fields from form
    exclude = ('keywords', 'production_companies')
    
    fieldsets = (
        (None, {
            'fields': ('title', 'original_title', 'description')
        }),
        ('Media', {
            'fields': ('poster', 'backdrop', 'trailer')
        }),
        ('Details', {
            'fields': ('first_air_date', 'last_air_date', 'rating', 'status', 'is_anime')
        }),
        ('IDs', {
            'fields': ('tmdb_id', 'tvdb_id', 'imdb_id'),
            'classes': ('collapse',)
        }),
        ('Relations', {
            'fields': ('genres', 'countries')
        }),
        ('Cast & Crew (Read Only)', {
            'fields': ('display_cast_crew',),
            'description': 'To edit cast/crew, use the MediaPerson admin directly.'
        }),
        ('Seasons', {
            'fields': ('display_seasons',)
        }),
        ('Actions', {
            'fields': ('tmdb_link', 'update_button')
        }),
    )
    
    def get_queryset(self, request):
        """Optimize list view queryset."""
        return super().get_queryset(request).only(
            'id', 'title', 'first_air_date', 'rating', 'status', 'date_updated', 'tmdb_id'
        )
    
    def display_cast_crew(self, obj):
        """Display cast and crew in a single optimized query."""
        if not obj.pk:
            return "-"
        
        content_type = ContentType.objects.get_for_model(obj)
        # Single query with select_related
        media_persons = MediaPerson.objects.filter(
            content_type=content_type,
            object_id=obj.id
        ).select_related('person').only(
            'role', 'character_name', 'order',
            'person__id', 'person__name'
        ).order_by('role', 'order')[:50]  # Limit to 50
        
        if not media_persons:
            return "-"
        
        # Group by role
        from collections import defaultdict
        by_role = defaultdict(list)
        for mp in media_persons:
            by_role[mp.role].append(mp)
        
        html = ""
        for role, people in by_role.items():
            html += f"<strong>{role}:</strong><br>"
            for mp in people[:15]:  # Max 15 per role
                if mp.character_name:
                    html += f"&nbsp;&nbsp;• {mp.person.name} as {mp.character_name}<br>"
                else:
                    html += f"&nbsp;&nbsp;• {mp.person.name}<br>"
            if len(people) > 15:
                html += f"&nbsp;&nbsp;<em>... and {len(people) - 15} more</em><br>"
            html += "<br>"
        
        return format_html(html)
    display_cast_crew.short_description = "Cast & Crew"
    
    def seasons_count(self, obj):
        return obj.seasons.count()
    seasons_count.short_description = "Seasons"
    
    def display_seasons(self, obj):
        """Display seasons with prefetched episode counts."""
        if not obj.pk:
            return "-"
        
        seasons = obj.seasons.all().order_by('season_number')[:20]  # Limit to 20 seasons
        if not seasons:
            return "-"
            
        html = "<div style='display: flex; flex-wrap: wrap; gap: 10px;'>"
        for season in seasons:
            url = reverse('admin:tvshows_season_change', args=[season.id])
            html += f"""
                <div style='border: 1px solid #ccc; border-radius: 5px; padding: 8px; min-width: 180px;'>
                    <strong><a href='{url}'>Season {season.season_number}</a></strong>
                    <div>{season.title or 'Untitled'}</div>
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
            return format_html(
                '<a href="/admin/tvshows/update/?id={}" class="button">Update from TMDB</a>', 
                obj.id
            )
        return ""
    update_button.short_description = "Actions"


class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 0
    fields = ('episode_number', 'title', 'air_date', 'runtime', 'rating')
    readonly_fields = ('episode_number', 'title', 'air_date', 'runtime', 'rating')
    can_delete = False
    show_change_link = True
    max_num = 30  # Limit to 30 episodes in inline
    
    def has_add_permission(self, request, obj=None):
        return False


class SeasonAdmin(admin.ModelAdmin):
    list_display = ('title', 'show_link', 'season_number', 'air_date', 'episode_count')
    list_filter = ('air_date',)
    search_fields = ('title', 'show__title')
    readonly_fields = ('display_episodes', 'tmdb_link')
    raw_id_fields = ('show',)
    inlines = [EpisodeInline]
    
    # Performance settings
    show_full_result_count = False
    list_per_page = 25
    
    def get_queryset(self, request):
        """Optimize list view queryset."""
        return super().get_queryset(request).select_related('show').only(
            'id', 'title', 'season_number', 'air_date', 'tmdb_id',
            'show__id', 'show__title', 'show__tmdb_id'
        )
    
    def episode_count(self, obj):
        return obj.episodes.count()
    episode_count.short_description = "Episodes"
    
    def show_link(self, obj):
        url = reverse('admin:tvshows_tvshow_change', args=[obj.show.id])
        return format_html('<a href="{}">{}</a>', url, obj.show.title)
    show_link.short_description = "Show"
    show_link.admin_order_field = "show__title"
    
    def display_episodes(self, obj):
        """Display episodes with limited query."""
        if not obj.pk:
            return "-"
        
        episodes = obj.episodes.all().order_by('episode_number')[:30]  # Limit to 30
        if not episodes:
            return "No episodes"
            
        html = "<div style='display: flex; flex-wrap: wrap; gap: 10px;'>"
        for episode in episodes:
            url = reverse('admin:tvshows_episode_change', args=[episode.id])
            html += f"""
                <div style='border: 1px solid #ccc; border-radius: 5px; padding: 8px; margin-bottom: 10px; width: 220px;'>
                    <strong><a href='{url}'>E{episode.episode_number}: {episode.title}</a></strong>
                    <div>{episode.air_date or 'No air date'}</div>
                    <div>Rating: {episode.rating or 'N/A'}</div>
                </div>
            """
        html += "</div>"
        return mark_safe(html)
    display_episodes.short_description = "Episodes"
    
    def tmdb_link(self, obj):
        if obj.tmdb_id and obj.show:
            url = f"https://www.themoviedb.org/tv/{obj.show.tmdb_id}/season/{obj.season_number}"
            return format_html('<a href="{}" target="_blank">View on TMDB</a>', url)
        return "-"
    tmdb_link.short_description = "TMDB"


class EpisodeAdmin(admin.ModelAdmin):
    list_display = ('episode_title_with_number', 'show_title', 'season_info', 'air_date', 'runtime', 'rating')
    list_filter = ('air_date',)
    search_fields = ('title', 'season__show__title')
    readonly_fields = ('tmdb_link', 'show_navigation')
    raw_id_fields = ('season',)
    
    # Performance settings
    show_full_result_count = False
    list_per_page = 50
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('season__show').only(
            'id', 'title', 'episode_number', 'air_date', 'runtime', 'rating', 'tmdb_id',
            'season__id', 'season__season_number', 'season__show__id', 'season__show__title', 'season__show__tmdb_id'
        )
    
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
        
        html = f"""
        <div style="margin-bottom: 15px;">
            <a href="{show_url}" class="button">Show: {show.title}</a>
            <a href="{season_url}" class="button">Season {obj.season.season_number}</a>
        </div>
        """
        return mark_safe(html)
    show_navigation.short_description = "Navigation"


class EpisodeSubGroupInline(admin.TabularInline):
    model = EpisodeSubGroup
    extra = 0
    fields = ('name', 'order', 'description')
    max_num = 20
    
    def has_add_permission(self, request, obj=None):
        return True


class EpisodeGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'show_link', 'order', 'subgroups_count')
    search_fields = ('name', 'show__title')
    ordering = ('order', 'name')
    raw_id_fields = ('show',)
    inlines = [EpisodeSubGroupInline]
    
    # Performance settings
    show_full_result_count = False
    list_per_page = 25
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('show')
    
    def show_link(self, obj):
        url = reverse('admin:tvshows_tvshow_change', args=[obj.show.id])
        return format_html('<a href="{}">{}</a>', url, obj.show.title)
    show_link.short_description = "Show"
    show_link.admin_order_field = "show__title"
    
    def subgroups_count(self, obj):
        return obj.sub_groups.count()
    subgroups_count.short_description = "Subgroups"


class EpisodeSubGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent_group', 'show_title', 'order', 'episodes_count')
    search_fields = ('name', 'parent_group__name', 'parent_group__show__title')
    ordering = ('parent_group__order', 'order', 'name')
    raw_id_fields = ('parent_group',)
    filter_horizontal = ('episodes',)
    
    # Performance settings
    show_full_result_count = False
    list_per_page = 25
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('parent_group__show')
    
    def episodes_count(self, obj):
        return obj.episodes.count()
    episodes_count.short_description = "Episodes"
    
    def show_title(self, obj):
        show = obj.parent_group.show
        url = reverse('admin:tvshows_tvshow_change', args=[show.id])
        return format_html('<a href="{}">{}</a>', url, show.title)
    show_title.short_description = "Show"
    show_title.admin_order_field = "parent_group__show__title"


class WatchedEpisodeAdmin(admin.ModelAdmin):
    list_display = ('user', 'episode_info', 'watched_date')
    list_filter = ('watched_date',)
    search_fields = ('user__username', 'episode__title', 'episode__season__show__title')
    raw_id_fields = ('user', 'episode')
    
    # Performance settings
    show_full_result_count = False
    list_per_page = 50
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'user', 'episode__season__show'
        )
    
    def episode_info(self, obj):
        return f"{obj.episode.season.show.title} - S{obj.episode.season.season_number}E{obj.episode.episode_number}"
    episode_info.short_description = "Episode"


# Register models
admin.site.register(TVShow, TVShowAdmin)
admin.site.register(Season, SeasonAdmin)
admin.site.register(Episode, EpisodeAdmin)
admin.site.register(EpisodeGroup, EpisodeGroupAdmin)
admin.site.register(EpisodeSubGroup, EpisodeSubGroupAdmin)
admin.site.register(WatchedEpisode, WatchedEpisodeAdmin)

