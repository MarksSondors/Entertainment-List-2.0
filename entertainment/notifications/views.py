from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404, render
from .models import PushSubscription, NotificationPreference
from .serializers import PushSubscriptionSerializer, NotificationPreferenceSerializer


def notification_test_page(request):
    """Test page for push notifications."""
    return render(request, 'notifications/test.html')


class PushSubscriptionViewSet(viewsets.ModelViewSet):
    """
    API endpoints for managing push notification subscriptions.
    """
    serializer_class = PushSubscriptionSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        return PushSubscription.objects.filter(user=self.request.user)
    
    def destroy(self, request, *args, **kwargs):
        """Unsubscribe from push notifications."""
        instance = self.get_object()
        instance.is_active = False
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=False, methods=['post'])
    def unsubscribe_by_endpoint(self, request):
        """Unsubscribe using endpoint URL."""
        endpoint = request.data.get('endpoint')
        if not endpoint:
            return Response(
                {'error': 'Endpoint is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            subscription = PushSubscription.objects.get(
                user=request.user, 
                endpoint=endpoint
            )
            subscription.is_active = False
            subscription.save()
            return Response({'message': 'Unsubscribed successfully'})
        except PushSubscription.DoesNotExist:
            return Response(
                {'error': 'Subscription not found'}, 
                status=status.HTTP_404_NOT_FOUND
            )


class NotificationPreferenceViewSet(viewsets.ModelViewSet):
    """
    API endpoints for managing notification preferences.
    """
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'put']  # No delete or create
    
    def get_queryset(self):
        return NotificationPreference.objects.filter(user=self.request.user)
    
    def get_object(self):
        """Get or create user's notification preferences."""
        obj, created = NotificationPreference.objects.get_or_create(
            user=self.request.user
        )
        return obj
    
    @action(detail=False, methods=['get'])
    def me(self, request):
        """Get current user's notification preferences."""
        preferences = self.get_object()
        serializer = self.get_serializer(preferences)
        return Response(serializer.data)
    
    @action(detail=False, methods=['patch'])
    def update_preferences(self, request):
        """Update current user's notification preferences."""
        preferences = self.get_object()
        serializer = self.get_serializer(preferences, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_vapid_public_key(request):
    """
    Return the VAPID public key for browser subscription.
    """
    from django.conf import settings
    import logging
    
    logger = logging.getLogger(__name__)
    public_key = settings.WEBPUSH_VAPID_PUBLIC_KEY
    
    logger.info(f'VAPID Public Key requested. Key present: {bool(public_key)}, Length: {len(public_key) if public_key else 0}')
    
    if not public_key:
        logger.error('VAPID public key is not configured! Run: python manage.py generate_vapid_keys')
        return Response(
            {
                'error': 'VAPID keys not configured',
                'message': 'Please generate VAPID keys using: python manage.py generate_vapid_keys'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    return Response({
        'public_key': public_key
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_test_notification(request):
    """
    Send a test notification to the current user.
    Only available to admin users.
    """
    # Restrict to admin users only
    if not request.user.is_staff:
        return Response(
            {'error': 'This feature is only available to administrators.'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    from .utils import send_notification_to_user
    
    notification_type = request.data.get('type', 'movie')
    
    test_data = {
        'movie': {
            'title': '🎬 New Movie Alert!',
            'body': 'Dune: Part Two is now available to watch',
            'url': '/movies/',
        },
        'tvshow': {
            'title': '📺 New Episode Released!',
            'body': 'The latest episode of your favorite show is out',
            'url': '/tvshows/',
        },
        'book': {
            'title': '📚 New Book Available!',
            'body': 'The next book in your series just released',
            'url': '/books/',
        },
        'music': {
            'title': '🎵 New Album Drop!',
            'body': 'Your favorite artist just released a new album',
            'url': '/music/',
        },
    }
    
    data = test_data.get(notification_type, test_data['movie'])
    
    result = send_notification_to_user(
        user_id=request.user.id,
        title=data['title'],
        body=data['body'],
        notification_type='system',
        url=data['url'],
    )
    
    return Response({
        'message': 'Test notification sent',
        'result': result
    })
