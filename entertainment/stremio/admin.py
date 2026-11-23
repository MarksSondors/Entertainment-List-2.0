from django.contrib import admin

from .models import AddonEvent


@admin.register(AddonEvent)
class AddonEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'kind', 'action', 'media_type', 'item_id', 'progress', 'user_agent')
    list_filter = ('kind', 'action', 'media_type', 'user')
    search_fields = ('item_id', 'extra')
    readonly_fields = [f.name for f in AddonEvent._meta.fields]

    @admin.display(description='progress')
    def progress(self, obj):
        if obj.current_time is not None and obj.duration:
            return f"{obj.current_time / obj.duration:.0%} of {obj.duration // 60000} min"
        return ''

    def has_add_permission(self, request):
        return False
