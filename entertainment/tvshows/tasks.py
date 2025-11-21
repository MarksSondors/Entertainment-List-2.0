from django_q.tasks import async_task, schedule
from .parsers import create_tvshow
import logging
from datetime import date, datetime
from django.conf import settings
from django_q.models import Schedule

from api.services.tvshows import TVShowsService
from api.services.tvdb import TVDBService
from .models import TVShow, Season, Episode, EpisodeGroup, EpisodeSubGroup, Keyword, Genre, Country, ProductionCompany

logger = logging.getLogger(__name__)


def check_new_episodes_today():
    """
    Scheduled task to check for new episodes airing today and notify users.
    Should be run daily (e.g., at 9 AM).
    """
    from django.utils import timezone
    from collections import defaultdict
    
    logger.info("Starting daily new episode check")
    
    check_date = timezone.now().date()
    
    # Get all episodes airing today
    episodes_today = Episode.objects.filter(
        air_date=check_date
    ).select_related('season', 'season__show').order_by('season__show', 'season__season_number', 'episode_number')
    
    if not episodes_today.exists():
        logger.info(f'No episodes found for {check_date}')
        return {'notified_users': 0, 'episodes_count': 0}
    
    logger.info(f'Found {episodes_today.count()} episode(s) airing today')
    
    # Group episodes by show
    episodes_by_show = defaultdict(list)
    for episode in episodes_today:
        episodes_by_show[episode.season.show].append(episode)
    
    scheduled_count = 0
    
    # For each show with episodes today
    for show, episodes in episodes_by_show.items():
        logger.info(f'Scheduling notification for: {show.title} ({len(episodes)} episode(s))')
        
        # Determine air time
        air_time = None
        for ep in episodes:
            if ep.air_time:
                air_time = ep.air_time
                break # Use the first one found
        
        run_at = timezone.now()
        if air_time:
            # air_time is now a DateTimeField, so it's already a full datetime
            if air_time > timezone.now():
                run_at = air_time
                logger.info(f"  Scheduled for {run_at}")
            else:
                logger.info(f"  Air time {air_time} passed, sending immediately")
        
        # Schedule the task
        schedule(
            'tvshows.tasks.send_show_notification',
            show.id,
            [ep.id for ep in episodes],
            schedule_type=Schedule.ONCE,
            next_run=run_at
        )
        scheduled_count += 1
    
    result = {
        'scheduled_notifications': scheduled_count,
        'shows_with_episodes': len(episodes_by_show),
        'total_episodes': episodes_today.count()
    }
    
    logger.info(f'Episode notification scheduling complete: {result}')
    return result

def send_show_notification(show_id, episode_ids):
    """
    Task to send notifications for a specific show's episodes.
    """
    from django.contrib.contenttypes.models import ContentType
    from custom_auth.models import Watchlist
    from notifications.utils import send_notification_to_user
    
    try:
        show = TVShow.objects.get(id=show_id)
        episodes = Episode.objects.filter(id__in=episode_ids).order_by('episode_number')
        
        if not episodes.exists():
            return
            
        # Get ContentType for TVShow
        tvshow_content_type = ContentType.objects.get_for_model(TVShow)
        
        # Get all users who have this show in their watchlist
        watchlist_users = Watchlist.objects.filter(
            content_type=tvshow_content_type,
            object_id=show.id
        ).select_related('user')
        
        if not watchlist_users.exists():
            return
            
        # Create notification message
        if episodes.count() == 1:
            episode = episodes.first()
            title = f"📺 New Episode: {show.title}"
            body = f"S{episode.season.season_number:02d}E{episode.episode_number:02d} - {episode.title} is now available!"
        else:
            title = f"📺 New Episodes: {show.title}"
            episode_list = ", ".join([
                f"S{ep.season.season_number:02d}E{ep.episode_number:02d}"
                for ep in episodes
            ])
            body = f"{episodes.count()} new episodes are now available: {episode_list}"
        
        url = show.get_absolute_url()
        
        # Send notification to each user
        count = 0
        for watchlist_item in watchlist_users:
            send_notification_to_user(
                user_id=watchlist_item.user.id,
                title=title,
                body=body,
                notification_type='new_release',
                url=url,
                icon=show.poster or '/static/favicon/web-app-manifest-192x192.png',
                content_type=tvshow_content_type,
                object_id=show.id,
            )
            count += 1
            
        logger.info(f"Sent notifications to {count} users for {show.title}")
        
    except TVShow.DoesNotExist:
        logger.error(f"TVShow {show_id} not found during notification send")
    except Exception as e:
        logger.error(f"Error sending notifications for show {show_id}: {e}")


def create_tvshow_async(tvshow_id, tvshow_poster=None, tvshow_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """
    Queue TV show creation as an async task using Django Q
    This returns the task ID which can be used to check status later
    """
    # Queue the task with Django Q
    task_id = async_task(
        'tvshows.tasks.create_tvshow_task',
        tvshow_id, 
        tvshow_poster, 
        tvshow_backdrop, 
        is_anime, 
        add_to_watchlist, 
        user_id,
        hook='tvshows.tasks.task_complete_hook'
    )
    return task_id

def create_tvshow_task(tvshow_id, tvshow_poster=None, tvshow_backdrop=None, is_anime=False, add_to_watchlist=False, user_id=None):
    """
    Task function that is called by Django Q worker
    """
    # Since we're importing inside the function, we don't need the alias
    return create_tvshow(
        tvshow_id=tvshow_id, 
        tvshow_poster=tvshow_poster, 
        tvshow_backdrop=tvshow_backdrop, 
        is_anime=is_anime, 
        add_to_watchlist=add_to_watchlist, 
        user_id=user_id
    )

def task_complete_hook(task):
    """
    Optional hook that runs when the task completes
    You can use this to send notifications or update status
    """
    if task.success:
        # Task completed successfully, you could send a notification
        # If you have a notification system, you could use it here
        tvshow = task.result
        if tvshow:
            print(f"TV show '{tvshow.title}' was successfully created.")
    else:
        # Task failed
        print(f"TV show creation failed: {task.result}")

def update_ongoing_tvshows():
    """Update information for all TV shows that are currently airing or will air soon."""
    today = date.today()
    
    # Get all ongoing or upcoming TV shows
    ongoing_tvshows = TVShow.objects.filter(
        status__in=['Returning Series', 'In Production', 'Planned']
    )
    
    # Log how many shows will be updated
    logger.info(f"Updating {ongoing_tvshows.count()} ongoing/upcoming TV shows")
    
    # Update each TV show
    for tvshow in ongoing_tvshows:
        try:
            # Schedule individual TV show updates as separate tasks
            async_task(update_single_tvshow, tvshow.id)
        except Exception as e:
            logger.error(f"Error scheduling update for TV show {tvshow.title} (ID: {tvshow.id}): {e}")
    
    return f"Scheduled updates for {ongoing_tvshows.count()} TV shows"

def update_random_tvshows():
    """Update information for 10 oldest updated TV shows in the database."""
    # Get 10 TV shows with the oldest updated dates
    tvshows = TVShow.objects.order_by('date_updated')[:8]
    
    # Log how many shows will be updated
    logger.info(f"Updating {len(tvshows)} TV shows with oldest update dates")
    
    # Update each TV show
    updates_count = 0
    for tvshow in tvshows:
        try:
            # Schedule individual TV show updates as separate tasks
            async_task(update_single_tvshow, tvshow.id)
            updates_count += 1
        except Exception as e:
            logger.error(f"Error scheduling update for TV show {tvshow.title} (ID: {tvshow.id}): {e}")
    
    return f"Scheduled updates for {updates_count} TV shows with oldest update dates"

def update_single_tvshow(tvshow_id):
    """Update a single TV show from TMDB."""
    try:
        tvshow = TVShow.objects.get(id=tvshow_id)
        
        # Use TVShowsService instead of direct requests
        tvshows_service = TVShowsService()
        data = tvshows_service.get_show_details(tvshow.tmdb_id, append_to_response="videos,keywords,external_ids")
        
        if not data:
            logger.error(f"TMDB API error for TV show {tvshow.title} (ID: {tvshow_id}): Failed to retrieve data")
            return f"Failed to update TV show {tvshow_id}"
            
        # Update TV show fields if they've changed
        updates = {}
        
        # Check and update basic fields
        if 'external_ids' in data:
            tvdb_id = data['external_ids'].get('tvdb_id')
            if tvdb_id and tvdb_id != tvshow.tvdb_id:
                updates['tvdb_id'] = tvdb_id

        if data.get('status') != tvshow.status:
            updates['status'] = data.get('status')
            
        if data.get('overview') and data.get('overview') != tvshow.description:
            updates['description'] = data.get('overview')
            
        if data.get('vote_average') and data.get('vote_average') != tvshow.rating:
            updates['rating'] = data.get('vote_average')
        
        # Update trailer if available
        if 'videos' in data and data['videos'].get('results'):
            trailers = [v for v in data['videos']['results'] 
                       if v['type'] == 'Trailer' and v['site'] == 'YouTube']
            if trailers:
                newest_trailer = sorted(trailers, key=lambda x: x['published_at'], reverse=True)[0]
                trailer_url = f"https://www.youtube.com/embed/{newest_trailer['key']}"
                if trailer_url != tvshow.trailer:
                    updates['trailer'] = trailer_url
        
        if not tvshow.poster and data.get('poster_path'):
            updates['poster'] = f"https://image.tmdb.org/t/p/original{data['poster_path']}"
        
        if not tvshow.backdrop and data.get('backdrop_path'):
            updates['backdrop'] = f"https://image.tmdb.org/t/p/original{data['backdrop_path']}"

        if data.get('name') and data.get('name') != tvshow.title:
            updates['title'] = data.get('name')
        
        if data.get('original_name') and data.get('original_name') != tvshow.original_title:
            updates['original_title'] = data.get('original_name')
        
        if data.get('first_air_date'):
            if data['first_air_date'] == '':
                updates['first_air_date'] = None
            elif data['first_air_date'] != str(tvshow.first_air_date):
                try:
                    new_date = date.fromisoformat(data['first_air_date'])
                    updates['first_air_date'] = new_date
                except (ValueError, TypeError):
                    logger.error(f"Invalid first air date format for TV show {tvshow.title}: {data['first_air_date']}")
                    updates['first_air_date'] = None
        
        if data.get('last_air_date'):
            if data['last_air_date'] == '':
                updates['last_air_date'] = None
            elif data['last_air_date'] != str(tvshow.last_air_date):
                try:
                    new_date = date.fromisoformat(data['last_air_date'])
                    updates['last_air_date'] = new_date
                except (ValueError, TypeError):
                    logger.error(f"Invalid last air date format for TV show {tvshow.title}: {data['last_air_date']}")
                    updates['last_air_date'] = None
        
        # Update keywords
        if 'keywords' in data and data['keywords'].get('results', []):
            current_keyword_names = set(tvshow.keywords.values_list('name', flat=True))
            new_keyword_names = {k['name'] for k in data['keywords']['results']}

            if current_keyword_names != new_keyword_names:
                keyword_instances = []
                for keyword in data['keywords']['results']:
                    keyword_instance, _ = Keyword.objects.get_or_create(
                        tmdb_id=keyword.get('id'),
                        defaults={'name': keyword.get('name')}
                    )
                    keyword_instances.append(keyword_instance)

                tvshow.keywords.set(keyword_instances)
                logger.info(f"Updated keywords for {tvshow.title}")

        # Update genres
        if 'genres' in data and data['genres']:
            current_genre_names = set(tvshow.genres.values_list('name', flat=True))
            new_genre_names = {g['name'] for g in data['genres']}

            if current_genre_names != new_genre_names:
                genre_instances = []
                for genre in data['genres']:
                    genre_instance, _ = Genre.objects.get_or_create(
                        tmdb_id=genre.get('id'),
                        defaults={'name': genre.get('name')}
                    )
                    genre_instances.append(genre_instance)

                tvshow.genres.set(genre_instances)
                logger.info(f"Updated genres for {tvshow.title}")

        # Update production countries
        if 'production_countries' in data and data['production_countries']:
            current_country_codes = set(tvshow.countries.values_list('iso_3166_1', flat=True))
            new_country_codes = {c['iso_3166_1'] for c in data['production_countries']}

            if current_country_codes != new_country_codes:
                country_instances = []
                for country in data['production_countries']:
                    country_instance, _ = Country.objects.get_or_create(
                        iso_3166_1=country.get('iso_3166_1'),
                        defaults={'name': country.get('name')}
                    )
                    country_instances.append(country_instance)

                tvshow.countries.set(country_instances)
                logger.info(f"Updated countries for {tvshow.title}")

        # Update production companies
        if 'production_companies' in data and data['production_companies']:
            current_company_names = set(tvshow.production_companies.values_list('name', flat=True))
            new_company_names = {c['name'] for c in data['production_companies']}

            if current_company_names != new_company_names:
                company_instances = []
                for company in data['production_companies']:
                    logo_path = company.get('logo_path')
                    company_instance, _ = ProductionCompany.objects.get_or_create(
                        tmdb_id=company.get('id'),
                        defaults={
                            'name': company.get('name'),
                            'logo_path': f"https://image.tmdb.org/t/p/original{logo_path}" if logo_path else None
                        }
                    )
                    company_instances.append(company_instance)

                tvshow.production_companies.set(company_instances)
                logger.info(f"Updated production companies for {tvshow.title}")

        # Apply updates if there are any
        if updates:
            logger.info(f"Updating TV show {tvshow.title} (ID: {tvshow_id}) with: {updates}")
            for field, value in updates.items():
                setattr(tvshow, field, value)
            tvshow.save()
            
            # After updating the show's basic info, check for new seasons and episode groups
            async_task(update_tvshow_seasons, tvshow.id)
            if tvshow.is_anime:
                async_task(update_episode_groups, tvshow.id)
            
            # Update episodes from TVDB if we have a TVDB ID
            if tvshow.tvdb_id:
                async_task(update_episodes_from_tvdb, tvshow.id)
            
            return f"Updated TV show {tvshow.title} with {len(updates)} changes: {', '.join([f'{k}={v}' for k, v in updates.items()])}"
        else:
            # Even if no basic show details changed, we should still check for new episodes and episode groups
            # Update the date_updated field to ensure rotation in the update queue
            tvshow.save(update_fields=['date_updated'])
            async_task(update_tvshow_seasons, tvshow.id)
            if tvshow.is_anime:
                async_task(update_episode_groups, tvshow.id)
            
            # Update episodes from TVDB if we have a TVDB ID
            if tvshow.tvdb_id:
                async_task(update_episodes_from_tvdb, tvshow.id)

            return f"No basic updates needed for TV show {tvshow.title}, checking for new episodes and episode groups"
            
    except TVShow.DoesNotExist:
        logger.error(f"TV show with ID {tvshow_id} not found")
        return f"TV show {tvshow_id} not found"
    except Exception as e:
        logger.error(f"Error updating TV show {tvshow_id}: {e}")
        return f"Error updating TV show {tvshow_id}: {str(e)}"

def update_tvshow_seasons(tvshow_id):
    """Update all seasons for a TV show, adding any new seasons/episodes."""
    try:
        tvshow = TVShow.objects.get(id=tvshow_id)
        
        # Use TVShowsService to get seasons details
        tvshows_service = TVShowsService()
        data = tvshows_service.get_show_details(tvshow.tmdb_id)
        
        if not data or 'seasons' not in data:
            logger.error(f"TMDB API error for TV show {tvshow.title} (ID: {tvshow_id}): Failed to retrieve data")
            return f"Failed to update seasons for TV show {tvshow_id}"
            
        # Get all seasons in our database for this TV show
        existing_seasons = {s.season_number: s for s in Season.objects.filter(show=tvshow)}
        
        # Check for new or updated seasons
        for season_data in data['seasons']:
            season_number = season_data['season_number']
                
            if season_number in existing_seasons:
                # Season exists, check if it needs updates
                season = existing_seasons[season_number]
                async_task(update_single_season, season.id)
            else:
                # New season, add it
                try:
                    # Get detailed season info
                    season_details = tvshows_service.get_season_details(tvshow.tmdb_id, season_number)
                    if season_details:
                        # Create the new season
                        season = Season.objects.create(
                            show=tvshow,
                            season_number=season_number,
                            title=season_details.get('name', f"Season {season_number}"),
                            overview=season_details.get('overview', ''),
                            poster=f"https://image.tmdb.org/t/p/original{season_details['poster_path']}" if season_details.get('poster_path') else None,
                            air_date=date.fromisoformat(season_details['air_date']) if season_details.get('air_date') else None,
                            tmdb_id=season_details.get('id')
                        )
                        # Add episodes for this new season
                        async_task(update_single_season, season.id)
                except Exception as e:
                    logger.error(f"Error adding season {season_number} for TV show {tvshow.title}: {e}")
        
        return f"Updated seasons for TV show {tvshow.title}"
        
    except TVShow.DoesNotExist:
        logger.error(f"TV show with ID {tvshow_id} not found")
        return f"TV show {tvshow_id} not found"
    except Exception as e:
        logger.error(f"Error updating seasons for TV show {tvshow_id}: {e}")
        return f"Error updating seasons for TV show {tvshow_id}: {str(e)}"

def update_single_season(season_id):
    """Update a single season, adding any new episodes."""
    try:
        season = Season.objects.get(id=season_id)
        tvshow = season.show
        
        # Use TVShowsService to get detailed season info
        tvshows_service = TVShowsService()
        data = tvshows_service.get_season_details(tvshow.tmdb_id, season.season_number)

        if not season.poster and data.get('poster_path'):
            season.poster = f"https://image.tmdb.org/t/p/original{data['poster_path']}"
            season.save(update_fields=['poster'])
        
        if not data or 'episodes' not in data:
            logger.error(f"TMDB API error for season {season.season_number} of {tvshow.title}: Failed to retrieve data")
            return f"Failed to update season {season_id}"
            
        # Get all episodes in our database for this season
        existing_episodes = {e.episode_number: e for e in Episode.objects.filter(season=season)}
        
        # Check for new or updated episodes
        added_count = 0
        updated_count = 0
        
        for episode_data in data['episodes']:
            episode_number = episode_data['episode_number']
            
            if episode_number in existing_episodes:
                # Episode exists, check if it needs updates
                episode = existing_episodes[episode_number]
                updates = {}
                
                if episode_data.get('name') and episode_data['name'] != episode.title:
                    updates['title'] = episode_data['name']
                    
                if episode_data.get('overview') and episode_data['overview'] != episode.overview:
                    updates['overview'] = episode_data['overview']
                    
                if episode_data.get('still_path') and not episode.still:
                    updates['still'] = f"https://image.tmdb.org/t/p/original{episode_data['still_path']}"
                    
                if episode_data.get('air_date'):
                    try:
                        new_air_date = date.fromisoformat(episode_data['air_date'])
                        if new_air_date != episode.air_date:
                            updates['air_date'] = new_air_date
                    except (ValueError, TypeError):
                        # Invalid date format, don't update
                        pass
                        
                if episode_data.get('vote_average') and episode_data['vote_average'] != episode.rating:
                    updates['rating'] = episode_data['vote_average']
                    
                if episode_data.get('runtime') and episode_data['runtime'] != episode.runtime:
                    updates['runtime'] = episode_data['runtime']
                
                if updates:
                    for field, value in updates.items():
                        setattr(episode, field, value)
                    episode.save()
                    updated_count += 1
                    
            else:
                # New episode, add it
                try:
                    Episode.objects.create(
                        season=season,
                        episode_number=episode_number,
                        title=episode_data.get('name', f"Episode {episode_number}"),
                        overview=episode_data.get('overview', ''),
                        still=f"https://image.tmdb.org/t/p/original{episode_data['still_path']}" if episode_data.get('still_path') else None,
                        air_date=date.fromisoformat(episode_data['air_date']) if episode_data.get('air_date') else None,
                        rating=episode_data.get('vote_average', 0),
                        runtime=episode_data.get('runtime'),
                        tmdb_id=episode_data.get('id')
                    )
                    added_count += 1
                except Exception as e:
                    logger.error(f"Error adding episode {episode_number} for season {season.season_number} of {tvshow.title}: {e}")
        
        return f"Added {added_count} new episodes and updated {updated_count} existing episodes for season {season.season_number} of {tvshow.title}"
        
    except Season.DoesNotExist:
        logger.error(f"Season with ID {season_id} not found")
        return f"Season {season_id} not found"
    except Exception as e:
        logger.error(f"Error updating season {season_id}: {e}")
        return f"Error updating season {season_id}: {str(e)}"

def update_episode_groups(tvshow_id):
    """Update episode groups and subgroups for a TV show."""
    try:
        tvshow = TVShow.objects.get(id=tvshow_id)
        
        # Use TVShowsService to get episode groups
        tvshows_service = TVShowsService()
        data = tvshows_service.get_episode_groups(tvshow.tmdb_id)
        
        if not data or 'results' not in data:
            # Some shows might not have episode groups, this is normal
            return f"No episode groups found for TV show {tvshow.title}"
            
        # Get existing episode groups
        existing_groups = {group.tmdb_id: group for group in EpisodeGroup.objects.filter(show=tvshow)}
        
        # Update or create episode groups
        added_groups = 0
        updated_groups = 0
        
        for group_data in data['results']:
            # Only process groups with type 5 or 6
            group_type = group_data.get('type')
            if group_type not in [5, 6]:
                continue
                
            group_id = str(group_data['id'])
            
            if group_id in existing_groups:
                # Group exists, update it if needed
                group = existing_groups[group_id]
                updates = {}
                
                if group_data.get('name') and group_data['name'] != group.name:
                    updates['name'] = group_data['name']
                    
                if group_data.get('description') and group_data['description'] != group.description:
                    updates['description'] = group_data['description']
                
                if updates:
                    for field, value in updates.items():
                        setattr(group, field, value)
                    group.save()
                    updated_groups += 1
                    
                # Check subgroups for this group regardless of whether the main group was updated
                async_task(update_episode_subgroups, group.id, group_id)
                
            else:
                # New group, add it
                try:
                    group = EpisodeGroup.objects.create(
                        tmdb_id=group_id,
                        name=group_data.get('name', 'Episode Group'),
                        description=group_data.get('description', ''),
                        show=tvshow,
                        order=added_groups  # Use counter as default order
                    )
                    added_groups += 1
                    
                    # Get detailed subgroups for this new group
                    async_task(update_episode_subgroups, group.id, group_id)
                    
                except Exception as e:
                    logger.error(f"Error adding episode group {group_data.get('name')} for {tvshow.title}: {e}")
        
        return f"Added {added_groups} new episode groups and updated {updated_groups} existing groups for {tvshow.title}"
        
    except TVShow.DoesNotExist:
        logger.error(f"TV show with ID {tvshow_id} not found")
        return f"TV show {tvshow_id} not found"
    except Exception as e:
        logger.error(f"Error updating episode groups for TV show {tvshow_id}: {e}")
        return f"Error updating episode groups for TV show {tvshow_id}: {str(e)}"

def update_episode_subgroups(group_id, tmdb_group_id):
    """Update episode subgroups for a specific episode group."""
    try:
        group = EpisodeGroup.objects.get(id=group_id)
        tvshow = group.show
        
        # Use TVShowsService to get detailed episode group info
        tvshows_service = TVShowsService()
        data = tvshows_service.get_episode_group_details(tmdb_group_id)
        
        if not data or 'groups' not in data:
            logger.error(f"TMDB API error for episode group {group.name} of {tvshow.title}: Failed to retrieve data")
            return f"Failed to update subgroups for episode group {group_id}"
            
        # Get existing subgroups
        existing_subgroups = {subgroup.tmdb_id: subgroup for subgroup in EpisodeSubGroup.objects.filter(parent_group=group) if subgroup.tmdb_id}
        
        # Update or create subgroups
        added_subgroups = 0
        updated_subgroups = 0
        
        for subgroup_data in data['groups']:
            subgroup_id = str(subgroup_data['id']) if 'id' in subgroup_data else None
            
            if subgroup_id and subgroup_id in existing_subgroups:
                # Subgroup exists, update it if needed
                subgroup = existing_subgroups[subgroup_id]
                updates = {}
                
                if subgroup_data.get('name') and subgroup_data['name'] != subgroup.name:
                    updates['name'] = subgroup_data['name']
                    
                if subgroup_data.get('description') and subgroup_data['description'] != subgroup.description:
                    updates['description'] = subgroup_data['description']
                    
                if subgroup_data.get('order') is not None and subgroup_data['order'] != subgroup.order:
                    updates['order'] = subgroup_data['order']
                    
                if subgroup_data.get('poster_path') and not subgroup.poster:
                    updates['poster'] = f"https://image.tmdb.org/t/p/original{subgroup_data['poster_path']}"
                
                if updates:
                    for field, value in updates.items():
                        setattr(subgroup, field, value)
                    subgroup.save()
                    updated_subgroups += 1
                
                # Update episodes in this subgroup
                if 'episodes' in subgroup_data:
                    # Get all episodes for the TV show
                    episodes_mapping = {}
                    for season in Season.objects.filter(show=tvshow).prefetch_related('episodes'):
                        for episode in season.episodes.all():
                            if episode.tmdb_id:
                                episodes_mapping[episode.tmdb_id] = episode
                    
                    # Clear existing episodes and add current ones
                    subgroup.episodes.clear()
                    
                    # Add episodes from TMDB to subgroup
                    for episode_data in subgroup_data['episodes']:
                        tmdb_episode_id = episode_data.get('id')
                        if tmdb_episode_id and tmdb_episode_id in episodes_mapping:
                            subgroup.episodes.add(episodes_mapping[tmdb_episode_id])
                
            else:
                # New subgroup, add it
                try:
                    new_subgroup = EpisodeSubGroup.objects.create(
                        tmdb_id=subgroup_id,
                        name=subgroup_data.get('name', 'Episode Subgroup'),
                        description=subgroup_data.get('description', ''),
                        parent_group=group,
                        order=subgroup_data.get('order', added_subgroups),
                        poster=f"https://image.tmdb.org/t/p/original{subgroup_data['poster_path']}" if subgroup_data.get('poster_path') else None
                    )
                    added_subgroups += 1
                    
                    # Add episodes to this new subgroup
                    if 'episodes' in subgroup_data:
                        # Get all episodes for the TV show
                        episodes_mapping = {}
                        for season in Season.objects.filter(show=tvshow).prefetch_related('episodes'):
                            for episode in season.episodes.all():
                                if episode.tmdb_id:
                                    episodes_mapping[episode.tmdb_id] = episode
                        
                        # Add episodes from TMDB to subgroup
                        for episode_data in subgroup_data['episodes']:
                            tmdb_episode_id = episode_data.get('id')
                            if tmdb_episode_id and tmdb_episode_id in episodes_mapping:
                                new_subgroup.episodes.add(episodes_mapping[tmdb_episode_id])
                    
                except Exception as e:
                    logger.error(f"Error adding episode subgroup {subgroup_data.get('name')} for group {group.name}: {e}")
        
        return f"Added {added_subgroups} new episode subgroups and updated {updated_subgroups} existing subgroups for group {group.name}"
        
    except EpisodeGroup.DoesNotExist:
        logger.error(f"Episode group with ID {group_id} not found")
        return f"Episode group {group_id} not found"
    except Exception as e:
        logger.error(f"Error updating episode subgroups for group {group_id}: {e}")
        return f"Error updating episode subgroups for group {group_id}: {str(e)}"

def setup_scheduled_tasks():
    """Set up scheduled tasks for TV shows if they don't exist already."""
    from django.utils import timezone
    from datetime import time, timedelta, datetime
    
    # Schedule task to update ongoing shows daily at 04:30 AM
    ongoing_time = time(hour=4, minute=30)  # 4:30 AM
    Schedule.objects.get_or_create(
        name='Update ongoing TV shows',
        defaults={
            'func': 'tvshows.tasks.update_ongoing_tvshows',
            'schedule_type': Schedule.DAILY,
            'repeats': -1,  # Repeat forever
            'next_run': timezone.make_aware(
                datetime.combine(timezone.now().date(), ongoing_time)
            ),
        }
    )
    
    # Schedule random TV show updates to run daily at 05:30 AM
    random_time = time(hour=5, minute=30)  # 5:30 AM
    Schedule.objects.get_or_create(
        name='Update random TV shows',
        defaults={
            'func': 'tvshows.tasks.update_random_tvshows',
            'schedule_type': Schedule.DAILY,
            'repeats': -1,  # Repeat forever
            'next_run': timezone.make_aware(
                datetime.combine(timezone.now().date(), random_time)
            ),
        }
    )
    
    return "TV show scheduled task setup complete"

def update_episodes_from_tvdb(tvshow_id):
    """
    Update episodes with accurate air dates and IDs from TVDB.
    """
    try:
        tvshow = TVShow.objects.get(id=tvshow_id)
        if not tvshow.tvdb_id:
            return "No TVDB ID for show"

        tvdb_service = TVDBService()
        series_extended = tvdb_service.get_series_extended(tvshow.tvdb_id)
        
        if not series_extended or 'data' not in series_extended or 'episodes' not in series_extended['data']:
            return "No episodes found in TVDB response"

        tvdb_episodes = series_extended['data']['episodes']
        
        # Get series air time
        series_air_time = series_extended['data'].get('airsTime')
        
        # Create a map for faster lookup: (season_number, episode_number) -> tvdb_episode
        tvdb_map = {}
        for ep in tvdb_episodes:
            s_num = ep.get('seasonNumber')
            e_num = ep.get('number')
            if s_num is not None and e_num is not None:
                tvdb_map[(s_num, e_num)] = ep

        updated_count = 0
        # Iterate over all episodes of the show
        for season in tvshow.seasons.all():
            for episode in season.episodes.all():
                tvdb_ep = tvdb_map.get((season.season_number, episode.episode_number))
                if tvdb_ep:
                    updated = False
                    # Update TVDB ID
                    if not episode.tvdb_id:
                        episode.tvdb_id = tvdb_ep.get('id')
                        updated = True
                    
                    # Update air date if missing or different (trusting TVDB for air dates)
                    if tvdb_ep.get('aired'):
                        try:
                            tvdb_date = date.fromisoformat(tvdb_ep.get('aired'))
                            if episode.air_date != tvdb_date:
                                episode.air_date = tvdb_date
                                updated = True
                            
                            # Update air time (datetime)
                            if series_air_time:
                                from django.utils import timezone
                                import zoneinfo
                                import pytz
                                
                                # Try parsing common formats
                                parsed_time = None
                                for fmt in ["%H:%M", "%I:%M %p"]:
                                    try:
                                        parsed_time = datetime.strptime(series_air_time, fmt).time()
                                        break
                                    except ValueError:
                                        continue
                                
                                if parsed_time:
                                    # Determine timezone
                                    tz_name = "UTC"
                                    
                                    # Manual map for major countries to ensure capital/most populous city is used
                                    MANUAL_TIMEZONES = {
                                        'US': 'America/New_York',
                                        'GB': 'Europe/London',
                                        'JP': 'Asia/Tokyo',
                                        'KR': 'Asia/Seoul',
                                        'CN': 'Asia/Shanghai',
                                        'IN': 'Asia/Kolkata',
                                        'CA': 'America/Toronto',
                                        'AU': 'Australia/Sydney',
                                        'FR': 'Europe/Paris',
                                        'DE': 'Europe/Berlin',
                                        'IT': 'Europe/Rome',
                                        'ES': 'Europe/Madrid',
                                        'BR': 'America/Sao_Paulo',
                                        'RU': 'Europe/Moscow',
                                    }

                                    # Get countries
                                    countries = list(tvshow.countries.values_list('iso_3166_1', flat=True))
                                    
                                    # Check for manual overrides first
                                    found_manual = False
                                    for code in countries:
                                        if code in MANUAL_TIMEZONES:
                                            tz_name = MANUAL_TIMEZONES[code]
                                            found_manual = True
                                            break
                                    
                                    # If no manual override, try pytz
                                    if not found_manual and countries:
                                        try:
                                            # Use the first country
                                            country_code = countries[0]
                                            timezones = pytz.country_timezones.get(country_code, [])
                                            if timezones:
                                                tz_name = timezones[0]
                                        except Exception:
                                            pass
                                    
                                    try:
                                        tz = zoneinfo.ZoneInfo(tz_name)
                                        dt = datetime.combine(tvdb_date, parsed_time)
                                        dt_aware = timezone.make_aware(dt, timezone=tz)
                                        
                                        if episode.air_time != dt_aware:
                                            episode.air_time = dt_aware
                                            updated = True
                                    except Exception as e:
                                        logger.error(f"Error setting timezone {tz_name} for {tvshow.title}: {e}")
                        except (ValueError, TypeError):
                            pass
                    
                    if updated:
                        episode.save()
                        updated_count += 1
        
        return f"Updated {updated_count} episodes from TVDB for {tvshow.title}"

    except Exception as e:
        logger.error(f"Error updating episodes from TVDB for {tvshow.title}: {e}")
        return f"Error: {e}"