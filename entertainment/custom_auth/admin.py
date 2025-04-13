from django.contrib import admin

# Register your models here.
from .models import CustomUser, Genre, Keyword, Country, Watchlist, Review

admin.site.register(CustomUser)
admin.site.register(Genre)
admin.site.register(Keyword)
admin.site.register(Country)
admin.site.register(Watchlist)

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