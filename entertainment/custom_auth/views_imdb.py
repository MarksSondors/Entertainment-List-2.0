from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms_imdb import ImdbImportForm
from .services.imdb_importer import ImdbImporter
from .models import Review, Watchlist
from movies.models import Movie
from tvshows.models import TVShow
from django.contrib.contenttypes.models import ContentType

@login_required
def imdb_import_page(request):
    if request.method == 'POST':
        form = ImdbImportForm(request.POST, request.FILES)
        if form.is_valid():
            importer = ImdbImporter(request.user)
            import_data = {'ratings': None, 'watchlist': None}
            
            if request.FILES.get('ratings_file'):
                import_data['ratings'] = importer.process_ratings(request.FILES['ratings_file'])
            
            if request.FILES.get('watchlist_file'):
                import_data['watchlist'] = importer.process_watchlist(request.FILES['watchlist_file'])
            
            # Check for errors
            if import_data['ratings'] and 'error' in import_data['ratings']:
                messages.error(request, f"Ratings Error: {import_data['ratings']['error']}")
                return redirect('imdb_import_page')
            
            if import_data['watchlist'] and 'error' in import_data['watchlist']:
                messages.error(request, f"Watchlist Error: {import_data['watchlist']['error']}")
                return redirect('imdb_import_page')

            request.session['imdb_import_data'] = import_data
            return redirect('imdb_import_preview')
    else:
        form = ImdbImportForm()
    
    return render(request, 'imdb_import_page.html', {'form': form})

@login_required
def imdb_import_preview(request):
    data = request.session.get('imdb_import_data')
    if not data:
        messages.warning(request, "No import data found.")
        return redirect('imdb_import_page')
    
    context = {
        'ratings': data.get('ratings'),
        'watchlist': data.get('watchlist')
    }
    return render(request, 'imdb_import_preview.html', context)

@login_required
def imdb_import_confirm(request):
    if request.method != 'POST':
        return redirect('imdb_import_page')
        
    data = request.session.get('imdb_import_data')
    if not data:
        messages.warning(request, "No import data found.")
        return redirect('imdb_import_page')

    from django_q.tasks import async_task
    
    # Collect all existing items
    existing_items = []
    
    # Track items being rated to avoid adding them to watchlist
    # Key: (content_type_id, object_id)
    being_rated_ids = set()
    
    movie_ct = ContentType.objects.get_for_model(Movie)
    tv_ct = ContentType.objects.get_for_model(TVShow)
    
    # Process Ratings first
    if data.get('ratings'):
        # Existing
        for item in data['ratings']['imported']:
             existing_items.append({
                 'type': 'rating',
                 'object_id': item['object_id'],
                 'rating': item['rating'],
                 'media_type': 'movie', # ratings only movie
                 'date': item.get('date')
             })
             # Track this as rated
             being_rated_ids.add((movie_ct.id, item['object_id']))

    # Process Watchlist
    if data.get('watchlist'):
        # Existing
        for item in data['watchlist']['imported']:
             ct = movie_ct if item['type'] == 'movie' else tv_ct
             
             # Skip if being rated in this batch
             if (ct.id, item['object_id']) in being_rated_ids:
                 continue
                 
             # Skip if already rated in DB
             if Review.objects.filter(content_type=ct, object_id=item['object_id'], user=request.user).exists():
                 continue
                 
             existing_items.append({
                 'type': 'watchlist',
                 'object_id': item['object_id'],
                 'media_type': item['type'],
                 'date': item.get('date')
             })

    # Prepare fetch items with deduplication
    fetch_map = {} # imdb_id -> dict
    
    # Ratings to fetch
    if data.get('ratings'):
        for item in data['ratings']['to_fetch']:
            fetch_map[item['imdb_id']] = {
                'imdb_id': item['imdb_id'],
                'type': item['type'],
                'rating': item['rating'],
                'date': item.get('date'),
                'watchlist': False # Enforce no watchlist if rated
            }

    # Watchlist to fetch
    if data.get('watchlist'):
        for item in data['watchlist']['to_fetch']:
            imdb_id = item['imdb_id']
            if imdb_id in fetch_map:
                # Already fetching as rated item -> Ignore watchlist request
                continue
            
            fetch_map[imdb_id] = {
                'imdb_id': imdb_id,
                'type': item['type'],
                'watchlist': True,
                'date': item.get('date')
            }
            
    to_fetch_items = list(fetch_map.values())

    # 1. Process existing items immediately
    count_existing = 0
    for item in existing_items:
        ct = movie_ct if item['media_type'] == 'movie' else tv_ct
        
        if item['type'] == 'rating':
            review = Review.objects.create(
                user=request.user,
                content_type=ct,
                object_id=item['object_id'],
                rating=item['rating'],
                review_text="Imported from IMDb"
            )
            if item.get('date'):
                 # Need to manually update date_added since it's auto_now_add
                 review.date_added = item['date']
                 review.save(update_fields=['date_added'])
                 
        elif item['type'] == 'watchlist':
            watchlist_item, created = Watchlist.objects.get_or_create(
                user=request.user,
                content_type=ct,
                object_id=item['object_id']
            )
            if item.get('date'):
                watchlist_item.date_added = item['date']
                watchlist_item.save(update_fields=['date_added'])
        count_existing += 1
            
    # 2. Queue task for new items
    if to_fetch_items:
        async_task('custom_auth.tasks.import_imdb_data', request.user.id, to_fetch_items)
        messages.success(request, f"Imported {count_existing} existing items. {len(to_fetch_items)} new items are being fetched in the background. You will receive a notification when complete.")
    else:
        messages.success(request, f"Successfully imported {count_existing} items.")

    # Clear session
    del request.session['imdb_import_data']
    
    return redirect('settings_page')
