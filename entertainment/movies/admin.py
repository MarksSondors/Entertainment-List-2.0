from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from .models import *

admin.site.register(Collection)
admin.site.register(MovieOfWeekPick)


class MovieAdmin(admin.ModelAdmin):
    list_display = ('title', 'release_date', 'rating')
    search_fields = ('title', 'original_title')
    list_filter = ('release_date',)
    
    # Use raw_id_fields for FK lookups
    raw_id_fields = ('collection', 'added_by')
    filter_horizontal = ('genres', 'countries')
    
    readonly_fields = ('minutes_to_hours', 'display_cast_crew')
    
    # Performance settings
    show_full_result_count = False
    list_per_page = 25
    
    # Exclude heavy M2M fields
    exclude = ('keywords', 'production_companies')
    
    # NO INLINES - they cause N+1 queries that can't be optimized
    inlines = []
    
    fieldsets = (
        (None, {
            'fields': ('title', 'original_title', 'description')
        }),
        ('Media', {
            'fields': ('poster', 'backdrop', 'trailer')
        }),
        ('Details', {
            'fields': ('release_date', 'runtime', 'minutes_to_hours', 'rating', 'status', 'is_anime')
        }),
        ('IDs', {
            'fields': ('tmdb_id', 'imdb_id'),
            'classes': ('collapse',)
        }),
        ('Relations', {
            'fields': ('genres', 'countries', 'collection', 'added_by')
        }),
        ('Cast & Crew (Read Only)', {
            'fields': ('display_cast_crew',),
            'description': 'To edit cast/crew, use the MediaPerson admin directly.'
        }),
    )
    
    def get_queryset(self, request):
        """Optimize list view queryset."""
        return super().get_queryset(request).only(
            'id', 'title', 'release_date', 'rating', 'tmdb_id'
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
        ).order_by('role', 'order')[:30]  # Limit to 30
        
        if not media_persons:
            return "-"
        
        # Group by role
        from collections import defaultdict
        by_role = defaultdict(list)
        for mp in media_persons:
            by_role[mp.role].append(mp)
        
        parts = []
        for role, people in by_role.items():
            parts.append(format_html("<strong>{}:</strong><br>", role))
            for mp in people[:10]:  # Max 10 per role
                try:
                    url = reverse('admin:custom_auth_person_change', args=[mp.person.id])
                    person_link = format_html('<a href="{}">{}</a>', url, mp.person.name)
                except Exception:
                    person_link = format_html('{}', mp.person.name)

                if mp.character_name:
                    parts.append(format_html("&nbsp;&nbsp;• {} as {}<br>", person_link, mp.character_name))
                else:
                    parts.append(format_html("&nbsp;&nbsp;• {}<br>", person_link))
            if len(people) > 10:
                parts.append(format_html("&nbsp;&nbsp;<em>... and {} more</em><br>", len(people) - 10))
            parts.append(mark_safe("<br>"))
        
        return mark_safe("".join(parts))
    display_cast_crew.short_description = "Cast & Crew"

admin.site.register(Movie, MovieAdmin)


# Separate admin for MediaPerson - use this to edit cast/crew
class MediaPersonAdmin(admin.ModelAdmin):
    list_display = ('person', 'role', 'character_name', 'content_type', 'object_id')
    list_filter = ('role', 'content_type')
    search_fields = ('person__name', 'character_name')
    raw_id_fields = ('person',)
    list_per_page = 50
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('person', 'content_type')

admin.site.register(MediaPerson, MediaPersonAdmin)