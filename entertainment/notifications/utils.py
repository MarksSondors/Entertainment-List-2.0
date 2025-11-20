"""
Utility functions for sending push notifications.
Uses Django Q2 for background processing.
"""
from pywebpush import webpush, WebPushException
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
import json
import logging
import uuid

from .models import PushSubscription, NotificationLog, NotificationPreference, QueuedNotification

User = get_user_model()
logger = logging.getLogger(__name__)


def send_push_notification(subscription_id, title, body, **kwargs):
    """
    Send a push notification to a specific subscription.
    
    Args:
        subscription_id: PushSubscription ID
        title: Notification title
        body: Notification body
        **kwargs: Additional notification data (icon, url, badge, etc.)
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        subscription = PushSubscription.objects.get(id=subscription_id, is_active=True)
    except PushSubscription.DoesNotExist:
        logger.warning(f"Subscription {subscription_id} not found or inactive")
        return False
    
    # Build notification payload
    notification_data = {
        'title': title,
        'body': body,
        'icon': kwargs.get('icon', '/static/favicon/web-app-manifest-192x192.png'),
        'badge': kwargs.get('badge', '/static/favicon/favicon-96x96.png'),
        'vibrate': kwargs.get('vibrate', [200, 100, 200]),
        'tag': kwargs.get('tag', str(uuid.uuid4())),
        'url': kwargs.get('url', '/'),
        'requireInteraction': kwargs.get('require_interaction', False),
    }
    
    # Add action buttons if provided
    if 'actions' in kwargs:
        notification_data['actions'] = kwargs['actions']
    
    # Prepare subscription info for pywebpush
    subscription_info = {
        'endpoint': subscription.endpoint,
        'keys': {
            'p256dh': subscription.p256dh,
            'auth': subscription.auth
        }
    }
    
    # VAPID claims
    vapid_claims = {
        'sub': f'mailto:{settings.WEBPUSH_VAPID_ADMIN_EMAIL}'
    }
    
    success = False
    error_message = None
    
    try:
        # Send the push notification
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(notification_data),
            vapid_private_key=settings.WEBPUSH_VAPID_PRIVATE_KEY,
            vapid_claims=vapid_claims
        )
        success = True
        logger.info(f"Push notification sent to subscription {subscription_id}")
        
    except WebPushException as e:
        error_message = str(e)
        logger.error(f"WebPushException for subscription {subscription_id}: {e}")
        
        # If subscription is invalid (410 Gone), deactivate it
        if e.response and e.response.status_code == 410:
            subscription.is_active = False
            subscription.save()
            logger.info(f"Deactivated invalid subscription {subscription_id}")
    
    except Exception as e:
        error_message = str(e)
        logger.error(f"Error sending push notification to subscription {subscription_id}: {e}")
    
    # Log the notification
    NotificationLog.objects.create(
        user=subscription.user,
        subscription=subscription,
        notification_type=kwargs.get('notification_type', 'system'),
        title=title,
        body=body,
        icon=notification_data['icon'],
        url=notification_data['url'],
        content_type=kwargs.get('content_type'),
        object_id=kwargs.get('object_id'),
        was_successful=success,
        error_message=error_message
    )
    
    return success


def send_notification_to_user(user_id, title, body, **kwargs):
    """
    Send push notification to all active subscriptions for a user.
    If user is in quiet hours, queue the notification for later.
    
    Args:
        user_id: User ID
        title: Notification title
        body: Notification body
        **kwargs: Additional notification data
    
    Returns:
        dict: Results with counts of successful/failed sends or queued status
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning(f"User {user_id} not found")
        return {'success': 0, 'failed': 0}
    
    # Check user preferences
    notification_type = kwargs.get('notification_type', 'system')
    
    # Check if user has this type of notification enabled
    if not is_notification_type_enabled(user, notification_type):
        logger.info(f"User {user_id} has disabled {notification_type} notifications")
        return {'success': 0, 'failed': 0, 'skipped': True}
    
    # Check if we're in quiet hours
    if is_in_quiet_hours(user):
        # Queue the notification instead of sending
        # Remove notification_type from kwargs to avoid duplicate argument
        kwargs_copy = {k: v for k, v in kwargs.items() if k != 'notification_type'}
        queue_notification(user, title, body, notification_type, **kwargs_copy)
        logger.info(f"Queued notification for user {user_id} (quiet hours)")
        return {'success': 0, 'failed': 0, 'queued': True}
    
    # Get all active subscriptions
    subscriptions = PushSubscription.objects.filter(user=user, is_active=True)
    
    results = {'success': 0, 'failed': 0}
    
    for subscription in subscriptions:
        if send_push_notification(subscription.id, title, body, **kwargs):
            results['success'] += 1
        else:
            results['failed'] += 1
    
    logger.info(f"Sent notifications to user {user_id}: {results}")
    return results


def is_notification_type_enabled(user, notification_type):
    """
    Check if user has enabled this type of notification.
    
    Args:
        user: User instance
        notification_type: Type of notification
    
    Returns:
        bool: True if notification type is enabled
    """
    try:
        prefs = user.notification_preferences
    except NotificationPreference.DoesNotExist:
        prefs = NotificationPreference.objects.create(user=user)
    
    type_map = {
        'new_release': prefs.new_releases,
        'watchlist_update': prefs.watchlist_updates,
        'recommendation': prefs.recommendations,
        'new_review': prefs.new_reviews,
        'system': prefs.system_notifications,
    }
    
    return type_map.get(notification_type, True)


def is_in_quiet_hours(user):
    """
    Check if current time is within user's quiet hours.
    
    Args:
        user: User instance
    
    Returns:
        bool: True if in quiet hours
    """
    try:
        prefs = user.notification_preferences
    except NotificationPreference.DoesNotExist:
        return False
    
    if not prefs.quiet_hours_enabled or not prefs.quiet_hours_start or not prefs.quiet_hours_end:
        return False
    
    current_time = timezone.localtime().time()
    
    # Handle quiet hours that cross midnight
    if prefs.quiet_hours_start <= prefs.quiet_hours_end:
        # Normal case: 22:00 - 08:00
        return prefs.quiet_hours_start <= current_time <= prefs.quiet_hours_end
    else:
        # Crosses midnight: 22:00 - 02:00
        return current_time >= prefs.quiet_hours_start or current_time <= prefs.quiet_hours_end


def queue_notification(user, title, body, notification_type, **kwargs):
    """
    Queue a notification to be sent later when quiet hours end.
    
    Args:
        user: User instance
        title: Notification title
        body: Notification body
        notification_type: Type of notification
        **kwargs: Additional notification data
    """
    QueuedNotification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title,
        body=body,
        icon=kwargs.get('icon'),
        url=kwargs.get('url'),
        content_type=kwargs.get('content_type'),
        object_id=kwargs.get('object_id'),
        extra_data={
            'vibrate': kwargs.get('vibrate', [200, 100, 200]),
            'tag': kwargs.get('tag', 'default'),
            'require_interaction': kwargs.get('require_interaction', False),
            'actions': kwargs.get('actions'),
        }
    )
    logger.info(f"Queued notification for {user.username}: {title}")


def should_send_notification(user, notification_type):
    """
    DEPRECATED: Use is_notification_type_enabled() and is_in_quiet_hours() instead.
    Check if notification should be sent based on user preferences.
    
    Args:
        user: User instance
        notification_type: Type of notification
    
    Returns:
        bool: True if notification should be sent
    """
    return is_notification_type_enabled(user, notification_type) and not is_in_quiet_hours(user)


def send_notification_to_all_users(title, body, **kwargs):
    """
    Send push notification to all users (admin broadcast feature).
    Only sends to users who have system notifications enabled.
    Queues notifications for users in quiet hours.
    
    Args:
        title: Notification title
        body: Notification body
        **kwargs: Additional notification data
    
    Returns:
        dict: Results with counts of successful/failed/queued sends
    """
    notification_type = kwargs.get('notification_type', 'system')
    
    # Get all users who have system notifications enabled
    users = User.objects.filter(
        notification_preferences__system_notifications=True,
        push_subscriptions__is_active=True
    ).distinct()
    
    results = {'success': 0, 'failed': 0, 'queued': 0, 'total_users': users.count()}
    
    logger.info(f"Broadcasting notification to {users.count()} users: {title}")
    
    for user in users:
        user_result = send_notification_to_user(user.id, title, body, **kwargs)
        results['success'] += user_result.get('success', 0)
        results['failed'] += user_result.get('failed', 0)
        if user_result.get('queued'):
            results['queued'] += 1
    
    logger.info(f"Broadcast complete: {results}")
    return results


def process_queued_notifications():
    """
    Process all queued notifications for users who are no longer in quiet hours.
    This should be run periodically (e.g., every hour via Django Q2 scheduled task).
    
    Returns:
        dict: Results with counts of processed notifications
    """
    from django.utils import timezone
    
    # Get all unsent queued notifications
    queued = QueuedNotification.objects.filter(is_sent=False).select_related('user')
    
    results = {'sent': 0, 'failed': 0, 'still_queued': 0}
    
    for notification in queued:
        # Check if user is still in quiet hours
        if is_in_quiet_hours(notification.user):
            results['still_queued'] += 1
            continue
        
        # Check if notification type is still enabled
        if not is_notification_type_enabled(notification.user, notification.notification_type):
            # User disabled this type - mark as sent to remove from queue
            notification.is_sent = True
            notification.sent_at = timezone.now()
            notification.save()
            logger.info(f"Skipped queued notification {notification.id} - type disabled")
            continue
        
        # Send the notification
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
        
        # Mark as sent
        notification.is_sent = True
        notification.sent_at = timezone.now()
        notification.save()
        
        if result.get('success', 0) > 0:
            results['sent'] += 1
            logger.info(f"Sent queued notification {notification.id} to {notification.user.username}")
        else:
            results['failed'] += 1
            logger.warning(f"Failed to send queued notification {notification.id}")
    
    logger.info(f"Processed queued notifications: {results}")
    return results


# Integration functions removed - will be added when implementing specific features
# See integration_examples.py.template for integration patterns
