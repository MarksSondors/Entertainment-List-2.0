from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

User = get_user_model()


class PushSubscription(models.Model):
    """
    Stores Web Push subscription information for a user's device.
    Each device (browser) gets its own subscription.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(unique=True, max_length=500)
    p256dh = models.CharField(max_length=255)  # Public key
    auth = models.CharField(max_length=255)  # Authentication secret
    
    # Device info for management
    user_agent = models.TextField(blank=True, null=True)
    device_name = models.CharField(max_length=100, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        db_table = 'push_subscriptions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.device_name or 'Unknown Device'}"


class NotificationLog(models.Model):
    """
    Tracks sent notifications for debugging and analytics.
    """
    NOTIFICATION_TYPES = [
        ('new_release', 'New Release'),
        ('watchlist_update', 'Watchlist Update'),
        ('recommendation', 'Recommendation'),
        ('new_review', 'New Review'),
        ('system', 'System Notification'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    subscription = models.ForeignKey(PushSubscription, on_delete=models.SET_NULL, null=True, blank=True)
    
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=100)
    body = models.TextField()
    icon = models.URLField(blank=True, null=True)
    url = models.URLField(blank=True, null=True)  # Where to navigate when clicked
    
    # Link to related content (movie, show, book, etc.)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    related_content = GenericForeignKey('content_type', 'object_id')
    
    sent_at = models.DateTimeField(auto_now_add=True)
    was_successful = models.BooleanField(default=True)
    error_message = models.TextField(blank=True, null=True)
    
    class Meta:
        db_table = 'notification_logs'
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['user', '-sent_at']),
            models.Index(fields=['notification_type']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.title}"


class NotificationPreference(models.Model):
    """
    User preferences for which types of notifications to receive.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notification_preferences')
    
    # Notification toggles
    new_releases = models.BooleanField(default=True, help_text="Notify when tracked items get new releases")
    watchlist_updates = models.BooleanField(default=True, help_text="Updates about watchlist items")
    recommendations = models.BooleanField(default=True, help_text="Personalized recommendations")
    new_reviews = models.BooleanField(default=True, help_text="Notify when users post reviews")
    movie_of_week = models.BooleanField(default=True, help_text="Community Movie of the Week reminders")
    system_notifications = models.BooleanField(default=True, help_text="System announcements")
    
    # Quiet hours
    quiet_hours_enabled = models.BooleanField(default=True)
    quiet_hours_start = models.TimeField(default='22:00', help_text="Don't send notifications after this time")
    quiet_hours_end = models.TimeField(default='08:00', help_text="Resume notifications after this time")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'notification_preferences'
    
    def __str__(self):
        return f"{self.user.username}'s preferences"


class QueuedNotification(models.Model):
    """
    Stores notifications that are queued during quiet hours.
    Will be sent when quiet hours end.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='queued_notifications')
    
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=100)
    body = models.TextField()
    icon = models.URLField(blank=True, null=True)
    url = models.URLField(blank=True, null=True)
    
    # Link to related content (movie, show, book, etc.)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    related_content = GenericForeignKey('content_type', 'object_id')
    
    # Additional notification data (stored as JSON)
    extra_data = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    is_sent = models.BooleanField(default=False)
    sent_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'queued_notifications'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['user', 'is_sent']),
            models.Index(fields=['is_sent', 'created_at']),
        ]
    
    def __str__(self):
        return f"Queued: {self.user.username} - {self.title}"
