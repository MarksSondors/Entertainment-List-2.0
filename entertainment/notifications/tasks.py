"""
Background tasks for notifications using Django Q2.
"""
import logging
from datetime import timedelta
from django.utils import timezone

logger = logging.getLogger(__name__)


def process_notification_queue():
    """
    Scheduled task to process queued notifications.
    Should be run periodically (e.g., every hour).
    """
    # Import here to avoid circular import issues
    from notifications.utils import process_queued_notifications
    
    logger.info("Starting scheduled task: process_notification_queue")
    results = process_queued_notifications()
    logger.info(f"Queued notification processing complete: {results}")
    return results


def cleanup_inactive_subscriptions():
    """
    Clean up inactive push subscriptions.
    Deletes subscriptions that have been inactive for more than 30 days.
    """
    from notifications.models import PushSubscription
    
    logger.info("Starting cleanup of inactive push subscriptions")
    
    # Calculate cutoff date (30 days ago)
    cutoff_date = timezone.now() - timedelta(days=30)
    
    # Find inactive subscriptions older than 30 days
    inactive_subscriptions = PushSubscription.objects.filter(
        is_active=False,
        created_at__lt=cutoff_date
    )
    
    count = inactive_subscriptions.count()
    
    if count > 0:
        # Delete them
        inactive_subscriptions.delete()
        logger.info(f"Deleted {count} inactive push subscriptions older than 30 days")
    else:
        logger.info("No inactive subscriptions to clean up")
    
    return {'deleted_count': count}


def cleanup_old_queued_notifications():
    """
    Clean up old queued notifications that failed to send.
    Deletes queued notifications older than 7 days that haven't been sent.
    """
    from notifications.models import QueuedNotification
    
    logger.info("Starting cleanup of old queued notifications")
    
    # Calculate cutoff date (7 days ago)
    cutoff_date = timezone.now() - timedelta(days=7)
    
    # Find unsent notifications older than 7 days
    old_queued = QueuedNotification.objects.filter(
        is_sent=False,
        created_at__lt=cutoff_date
    )
    
    count = old_queued.count()
    
    if count > 0:
        # Delete them
        old_queued.delete()
        logger.info(f"Deleted {count} old queued notifications older than 7 days")
    else:
        logger.info("No old queued notifications to clean up")
    
    return {'deleted_count': count}


def cleanup_old_notification_logs():
    """
    Clean up old notification logs.
    Deletes notification logs older than 90 days.
    """
    from notifications.models import NotificationLog
    
    logger.info("Starting cleanup of old notification logs")
    
    # Calculate cutoff date (90 days ago)
    cutoff_date = timezone.now() - timedelta(days=90)
    
    # Find logs older than 90 days
    old_logs = NotificationLog.objects.filter(
        sent_at__lt=cutoff_date
    )
    
    count = old_logs.count()
    
    if count > 0:
        # Delete them
        old_logs.delete()
        logger.info(f"Deleted {count} notification logs older than 90 days")
    else:
        logger.info("No old notification logs to clean up")
    
    return {'deleted_count': count}


def cleanup_all_notifications():
    """
    Run all notification cleanup tasks.
    This is the main task that should be scheduled.
    """
    logger.info("Starting comprehensive notification cleanup")
    
    results = {
        'inactive_subscriptions': cleanup_inactive_subscriptions(),
        'old_queued_notifications': cleanup_old_queued_notifications(),
        'old_notification_logs': cleanup_old_notification_logs(),
    }
    
    total_deleted = sum(r['deleted_count'] for r in results.values())
    logger.info(f"Notification cleanup complete. Total items deleted: {total_deleted}")
    
    return results
