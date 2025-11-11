from rest_framework import serializers
from .models import PushSubscription, NotificationPreference


class PushSubscriptionSerializer(serializers.ModelSerializer):
    """
    Serializer for creating/managing push subscriptions.
    """
    class Meta:
        model = PushSubscription
        fields = ['id', 'endpoint', 'p256dh', 'auth', 'device_name', 'is_active', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def create(self, validated_data):
        # Get user from request context
        user = self.context['request'].user
        validated_data['user'] = user
        
        # Get user agent from request
        request = self.context.get('request')
        if request:
            validated_data['user_agent'] = request.META.get('HTTP_USER_AGENT', '')
        
        # Update existing subscription if endpoint exists, otherwise create new
        subscription, created = PushSubscription.objects.update_or_create(
            endpoint=validated_data['endpoint'],
            defaults=validated_data
        )
        return subscription


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    """
    Serializer for managing notification preferences.
    """
    class Meta:
        model = NotificationPreference
        fields = [
            'new_releases', 'watchlist_updates', 'recommendations', 
            'new_reviews', 'movie_of_week', 'system_notifications',
            'quiet_hours_enabled', 'quiet_hours_start', 'quiet_hours_end'
        ]
