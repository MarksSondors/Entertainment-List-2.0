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


def import_imdb_data(user_id, items):
    """
    Background task to import IMDb data (fetch new items + create reviews/watchlist).
    
    Args:
        user_id: ID of the user
        items: List of dictionaries with keys:
               - imdb_id
               - type ('movie' or 'tv')
               - rating (optional)
               - watchlist (boolean, optional - implied if not rating)
    """
    from django.contrib.auth import get_user_model
    from movies.models import Movie
    from tvshows.models import TVShow
    from custom_auth.models import Review, Watchlist
    from api.services.movies import MoviesService
    from django.contrib.contenttypes.models import ContentType
    
    # We need to import create functions here to avoid circular imports if they assume models are ready
    from movies.tasks import create_movie_fast
    
    logger.info(f"Starting IMDb import for user {user_id} with {len(items)} items")
    
    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for IMDb import")
        return

    movie_service = MoviesService()
    movie_ct = ContentType.objects.get_for_model(Movie)
    tv_ct = ContentType.objects.get_for_model(TVShow)
    
    success_count = 0
    failed_count = 0
    
    for item in items:
        imdb_id = item.get('imdb_id')
        media_type = item.get('type') # 'movie' or 'tv'
        rating = item.get('rating')
        is_watchlist = item.get('watchlist', False)
        
        if not imdb_id:
            continue
            
        try:
            # 1. Resolve IMDb ID -> TMDB ID if needed
            tmdb_id = None
            media_object = None
            ct = None
            
            if media_type == 'movie':
                ct = movie_ct
                # Check if exists locally first
                media_object = Movie.objects.filter(imdb_id=imdb_id).first()
                
                if not media_object:
                    # Fetch TMDB ID
                    # Use _get directly to avoid potential worker reload issues with new methods
                    results = movie_service._get(f'find/{imdb_id}', params={'external_source': 'imdb_id'})
                    movie_results = results.get('movie_results', []) if results else []
                    if movie_results:
                        tmdb_id = movie_results[0]['id']
                        # Create Movie
                        media_object = create_movie_fast(tmdb_id, user_id=user.id, add_to_watchlist=False)
                    else:
                        logger.warning(f"Could not find movie for IMDb ID {imdb_id}")
                        failed_count += 1
                        continue
            
            elif media_type == 'tv':
                ct = tv_ct
                # Check if exists locally
                media_object = TVShow.objects.filter(imdb_id=imdb_id).first()
                
                if not media_object:
                    # Fetch TMDB ID
                    # Use _get directly to avoid potential worker reload issues with new methods
                    results = movie_service._get(f'find/{imdb_id}', params={'external_source': 'imdb_id'})
                    tv_results = results.get('tv_results', []) if results else []
                    
                    if tv_results:
                        tmdb_id = tv_results[0]['id']
                        
                        # Check if TV Show already exists by TMDb ID
                        media_object = TVShow.objects.filter(tmdb_id=tmdb_id).first()
                        
                        if media_object:
                            # Update IMDb ID if missing
                            if not media_object.imdb_id:
                                media_object.imdb_id = imdb_id
                                media_object.save(update_fields=['imdb_id'])
                        else:
                            # Try to create TV Show inline using logic similar to tasks
                            try:
                                from tvshows.parsers import create_tvshow
                                media_object = create_tvshow(tmdb_id, user_id=user.id, add_to_watchlist=False)
                            except ImportError:
                                logger.error("Could not import create_tvshow from tvshows.parsers")
                                failed_count += 1
                                continue
                            except Exception as e:
                                logger.error(f"Error creating TV Show {tmdb_id}: {e}")
                                failed_count += 1
                                continue
                    else:
                        logger.warning(f"Could not find TV show for IMDb ID {imdb_id}")
                        failed_count += 1
                        continue

            if not media_object:
                failed_count += 1
                continue

            # 2. Add Rating (Review)
            if rating:
                # Force strictly no watchlist if rated
                is_watchlist = False 
                
                # Only if Review doesn't exist
                if not Review.objects.filter(user=user, content_type=ct, object_id=media_object.id).exists():
                    review = Review.objects.create(
                        user=user,
                        content_type=ct,
                        object_id=media_object.id,
                        rating=rating,
                        review_text="Imported from IMDb"
                    )
                    if item.get('date'):
                        review.date_added = item['date']
                        review.save(update_fields=['date_added'])

            # 3. Add to Watchlist
            if is_watchlist:
                 watchlist_item, created = Watchlist.objects.get_or_create(
                    user=user,
                    content_type=ct,
                    object_id=media_object.id
                )
                 if item.get('date'):
                    watchlist_item.date_added = item['date']
                    watchlist_item.save(update_fields=['date_added'])
            
            success_count += 1
            
        except Exception as e:
            logger.error(f"Error processing IMDb import item {imdb_id}: {e}")
            failed_count += 1

    from notifications.utils import send_notification_to_user
    send_notification_to_user(
        user_id=user_id,
        title="IMDb Import Complete",
        body=f"Processed {len(items)} items. Success: {success_count}, Failed: {failed_count}",
        notification_type="system"
    )

