from django_q.tasks import async_task
# Fix the import statement to match your actual function name
from .parsers import create_tvshow

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