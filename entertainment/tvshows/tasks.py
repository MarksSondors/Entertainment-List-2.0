from django_q.tasks import async_task
from .parsers import create_tvshow
import logging
from datetime import date
from django.conf import settings
from django_q.models import Schedule

from api.services.tvshows import TVShowsService
from .models import TVShow, Season, Episode, EpisodeGroup, EpisodeSubGroup

logger = logging.getLogger(__name__)

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
    tvshows = TVShow.objects.order_by('date_updated')[:10]
    
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
        data = tvshows_service.get_show_details(tvshow.tmdb_id, append_to_response="videos,keywords")
        
        if not data:
            logger.error(f"TMDB API error for TV show {tvshow.title} (ID: {tvshow_id}): Failed to retrieve data")
            return f"Failed to update TV show {tvshow_id}"
            
        # Update TV show fields if they've changed
        updates = {}
        
        # Check and update basic fields
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
            else:
                pass
            
            return f"Updated TV show {tvshow.title} with {len(updates)} changes: {', '.join([f'{k}={v}' for k, v in updates.items()])}"
        else:
            # Even if no basic show details changed, we should still check for new episodes and episode groups
            async_task(update_tvshow_seasons, tvshow.id)
            if tvshow.is_anime:
                async_task(update_episode_groups, tvshow.id)
            else:
                pass
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
    # Schedule task to update ongoing shows daily
    Schedule.objects.get_or_create(
        name='Update ongoing TV shows',
        defaults={
            'func': 'tvshows.tasks.update_ongoing_tvshows',
            'schedule_type': Schedule.DAILY,
            'repeats': -1,  # Repeat forever
        }
    )
    
    # Schedule random TV show updates to run daily
    Schedule.objects.get_or_create(
        name='Update random TV shows',
        defaults={
            'func': 'tvshows.tasks.update_random_tvshows',
            'schedule_type': Schedule.DAILY,
            'repeats': -1,  # Repeat forever
        }
    )
    
    return "TV show scheduled task setup complete"