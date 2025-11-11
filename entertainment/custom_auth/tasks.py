"""
Background tasks for custom_auth using Django Q2.
"""
import logging
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger(__name__)


def process_review_notification(review_id):
    """
    Process notification for a new review.
    Notifies users who have the reviewed item in their watchlist.
    
    Args:
        review_id: ID of the newly created Review
    """
    logger.info(f"🔔 Starting process_review_notification for review_id={review_id}")
    
    from custom_auth.models import Review, Watchlist
    from notifications.utils import send_notification_to_user
    
    try:
        review = Review.objects.select_related('user', 'content_type').get(id=review_id)
        logger.info(f"✅ Found review: {review}")
    except Review.DoesNotExist:
        logger.warning(f"❌ Review {review_id} not found")
        return {'error': 'Review not found'}
    
    reviewer = review.user
    media = review.media
    
    # Don't process if media doesn't exist
    if not media:
        logger.warning(f"❌ Review {review_id} has no associated media")
        return {'error': 'No associated media'}
    
    logger.info(f"📺 Processing review notification for '{media}' by {reviewer.username}")
    
    # Find all users who have new_reviews notifications enabled (excluding the reviewer)
    from notifications.models import NotificationPreference
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    
    # Get all users with new_reviews enabled, excluding the reviewer
    users_with_preference = User.objects.filter(
        notification_preferences__new_reviews=True
    ).exclude(
        id=reviewer.id
    ).select_related('notification_preferences')
    
    logger.info(f"👥 Found {users_with_preference.count()} user(s) with new_reviews enabled (excluding reviewer)")
    
    if not users_with_preference.exists():
        logger.info(f"ℹ️ No users have new_reviews notifications enabled")
        return {'notified_users': 0}
    
    logger.info(f"Notifying {users_with_preference.count()} user(s) about review of {media}")
    
    # Build notification content
    media_title = getattr(media, 'title', str(media))
    
    # Format rating nicely
    rating_str = f"{review.rating:.1f}/10"
    
    # Create title and body based on whether there's review text
    if review.season:
        title = f"⭐ New Review: {media_title} - {review.season}"
        context = f"{review.season}"
    elif review.episode_subgroup:
        title = f"⭐ New Review: {media_title} - {review.episode_subgroup}"
        context = f"{review.episode_subgroup}"
    else:
        title = f"⭐ New Review: {media_title}"
        context = media_title
    
    if review.review_text and len(review.review_text.strip()) > 0:
        # Truncate review text for notification (first 100 chars)
        review_snippet = review.review_text[:100]
        if len(review.review_text) > 100:
            review_snippet += "..."
        body = f'{reviewer.username} rated {context} {rating_str}: "{review_snippet}"'
    else:
        body = f"{reviewer.username} rated {context} {rating_str}"
    
    # Get media URL (assuming media has get_absolute_url method)
    url = getattr(media, 'get_absolute_url', lambda: '/')()
    
    # Get poster/icon if available
    icon = getattr(media, 'poster', None) or '/static/favicon/web-app-manifest-192x192.png'
    
    # Track notification stats
    total_notifications = 0
    total_users = set()
    
    # Send notification to each user
    for user in users_with_preference:
        logger.info(f"📤 Sending notification to user: {user.username}")
        result = send_notification_to_user(
            user_id=user.id,
            title=title,
            body=body,
            notification_type='new_review',
            url=url,
            icon=icon,
            content_type=review.content_type,
            object_id=review.object_id,
        )
        logger.info(f"📬 Notification result for {user.username}: {result}")
        
        if result.get('success', 0) > 0 or result.get('queued'):
            total_notifications += 1
            total_users.add(user.id)
    
    result = {
        'notified_users': len(total_users),
        'notifications_sent': total_notifications,
    }
    
    logger.info(f"Review notification complete: {result}")
    return result
