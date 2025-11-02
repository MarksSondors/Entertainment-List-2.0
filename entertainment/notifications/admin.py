from django.contrib import admin
from .models import PushSubscription, NotificationLog, NotificationPreference, QueuedNotification


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_name', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['user__username', 'device_name', 'endpoint']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('User Information', {
            'fields': ('user', 'device_name', 'is_active')
        }),
        ('Subscription Details', {
            'fields': ('endpoint', 'p256dh', 'auth')
        }),
        ('Device Info', {
            'fields': ('user_agent',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ['user', 'notification_type', 'title', 'sent_at', 'was_successful']
    list_filter = ['notification_type', 'was_successful', 'sent_at']
    search_fields = ['user__username', 'title', 'body']
    readonly_fields = ['sent_at']
    date_hierarchy = 'sent_at'
    
    fieldsets = (
        ('Notification Details', {
            'fields': ('user', 'subscription', 'notification_type', 'title', 'body')
        }),
        ('Display Options', {
            'fields': ('icon', 'url')
        }),
        ('Related Content', {
            'fields': ('content_type', 'object_id')
        }),
        ('Delivery Status', {
            'fields': ('sent_at', 'was_successful', 'error_message')
        }),
    )


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'new_releases', 'watchlist_updates', 'recommendations', 'quiet_hours_enabled']
    list_filter = ['new_releases', 'watchlist_updates', 'recommendations', 'quiet_hours_enabled']
    search_fields = ['user__username']
    
    fieldsets = (
        ('User', {
            'fields': ('user',)
        }),
        ('Notification Types', {
            'fields': ('new_releases', 'watchlist_updates', 'recommendations', 'system_notifications')
        }),
        ('Quiet Hours', {
            'fields': ('quiet_hours_enabled', 'quiet_hours_start', 'quiet_hours_end')
        }),
    )


@admin.register(QueuedNotification)
class QueuedNotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'notification_type', 'title', 'created_at', 'is_sent', 'sent_at']
    list_filter = ['is_sent', 'notification_type', 'created_at']
    search_fields = ['user__username', 'title', 'body']
    readonly_fields = ['created_at', 'sent_at']
    date_hierarchy = 'created_at'
    actions = ['mark_as_sent', 'process_selected']
    
    fieldsets = (
        ('Notification Details', {
            'fields': ('user', 'notification_type', 'title', 'body')
        }),
        ('Display Options', {
            'fields': ('icon', 'url')
        }),
        ('Related Content', {
            'fields': ('content_type', 'object_id')
        }),
        ('Extra Data', {
            'fields': ('extra_data',),
            'classes': ('collapse',)
        }),
        ('Queue Status', {
            'fields': ('is_sent', 'created_at', 'sent_at')
        }),
    )
    
    def mark_as_sent(self, request, queryset):
        """Mark selected notifications as sent without actually sending them."""
        from django.utils import timezone
        count = queryset.update(is_sent=True, sent_at=timezone.now())
        self.message_user(request, f'{count} notification(s) marked as sent.')
    mark_as_sent.short_description = 'Mark selected as sent'
    
    def process_selected(self, request, queryset):
        """Process selected queued notifications if users are not in quiet hours."""
        from notifications.utils import is_in_quiet_hours, send_notification_to_user
        from django.utils import timezone
        
        sent_count = 0
        skipped_count = 0
        
        for notification in queryset.filter(is_sent=False):
            if is_in_quiet_hours(notification.user):
                skipped_count += 1
                continue
            
            result = send_notification_to_user(
                user_id=notification.user.id,
                title=notification.title,
                body=notification.body,
                notification_type=notification.notification_type,
                icon=notification.icon,
                url=notification.url,
                content_type=notification.content_type,
                object_id=notification.object_id,
                **notification.extra_data
            )
            
            notification.is_sent = True
            notification.sent_at = timezone.now()
            notification.save()
            
            if result.get('success', 0) > 0:
                sent_count += 1
        
        self.message_user(
            request, 
            f'Processed: {sent_count} sent, {skipped_count} still in quiet hours.'
        )
    process_selected.short_description = 'Process selected notifications'
