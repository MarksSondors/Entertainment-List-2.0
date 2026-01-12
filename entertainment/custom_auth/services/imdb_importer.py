import pandas as pd
from django.db.models import Q
from movies.models import Movie
from tvshows.models import TVShow
from custom_auth.models import Review, Watchlist
from django.contrib.contenttypes.models import ContentType
import logging

logger = logging.getLogger(__name__)

class ImdbImporter:
    def __init__(self, user):
        self.user = user
        self.movie_ct = ContentType.objects.get_for_model(Movie)
        self.tv_ct = ContentType.objects.get_for_model(TVShow)

    def parse_csv(self, file):
        try:
            # IMDb CSVs are usually comma separated
            df = pd.read_csv(file)
            # Normalize columns to lowercase
            df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            logger.error(f"Error parsing CSV: {e}")
            return None

    def process_ratings(self, file):
        df = self.parse_csv(file)
        if df is None:
            return {'error': 'Failed to parse file'}

        results = {
            'count': 0,
            'imported': [],
            'conflicts': [], # user has different rating locally
            'skipped': [],   # duplicates (same rating) or ignored (TV shows)
            'to_fetch': [],  # not in DB, will be fetched
        }
        
        # Check required columns
        required = ['const', 'your rating', 'title type', 'title']
        if not all(col in df.columns for col in required):
             return {'error': f'Missing required columns. Found: {list(df.columns)}'}

        # Check if date rated column exists (it's usually 'date rated')
        date_column = 'date rated' if 'date rated' in df.columns else None

        for _, row in df.iterrows():
            imdb_id = row['const']
            title_type = str(row['title type']).lower() # Ensure string and lowercase for comparison
            title = row.get('title', 'Unknown')
            rating = row['your rating']
            review_date = row[date_column] if date_column else None

            # Ignore TV show RATINGS as per requirement
            # Updated to include space variants
            if title_type not in ['movie', 'tvmovie', 'video', 'short', 'tv movie']:
                results['skipped'].append({
                    'title': title,
                    'reason': f'Ignored type: {title_type}'
                })
                continue
            
            # Find movie in DB
            movie = Movie.objects.filter(imdb_id=imdb_id).first()
            if not movie:
                # Add to fetch list
                results['to_fetch'].append({
                    'imdb_id': imdb_id,
                    'title': title,
                    'rating': rating,
                    'type': 'movie',
                    'date': review_date
                })
                continue
            
            # Check existing review
            existing_review = Review.objects.filter(
                user=self.user,
                content_type=self.movie_ct,
                object_id=movie.id
            ).first()

            if existing_review:
                if abs(existing_review.rating - rating) > 0.1: # float comparison
                    # Conflict
                    results['conflicts'].append({
                        'title': movie.title,
                        'local_rating': existing_review.rating,
                        'imdb_rating': rating,
                        'imdb_id': imdb_id
                    })
                else:
                    # Match
                    results['skipped'].append({
                        'title': movie.title,
                        'reason': 'Already rated (matches)'
                    })
            else:
                # To Import
                results['imported'].append({
                    'object_id': movie.id,
                    'title': movie.title,
                    'rating': rating,
                    'imdb_id': imdb_id,
                    'type': 'movie',
                    'date': review_date
                })
        
        return results

    def process_watchlist(self, file):
        df = self.parse_csv(file)
        if df is None:
            return {'error': 'Failed to parse file'}

        results = {
            'count': 0,
            'imported': [],
            'skipped': [],
            'to_fetch': []
        }
        
        if 'const' not in df.columns or 'title type' not in df.columns:
             return {'error': 'Missing required columns (const, Title Type)'}

        for _, row in df.iterrows():
            imdb_id = row['const']
            title_type = str(row['title type']).lower() # Ensure string and lowercase
            title = row.get('title', 'Unknown')
            
            content_object = None
            ct = None
            media_type = None
            
            if title_type in ['movie', 'tvmovie', 'video', 'short', 'tv movie']:
                content_object = Movie.objects.filter(imdb_id=imdb_id).first()
                ct = self.movie_ct
                media_type = 'movie'
            elif title_type in ['tvseries', 'tvminiseries', 'tvspecial', 'tv series', 'tv mini series', 'tv special']:
                content_object = TVShow.objects.filter(imdb_id=imdb_id).first()
                ct = self.tv_ct
                media_type = 'tv'
            else:
                 # Skip other types (episodes, games etc if any)
                 results['skipped'].append({
                     'title': title,
                     'reason': f'Ignored type: {title_type}'
                 })
                 continue
            
            if not content_object:
                results['to_fetch'].append({
                    'imdb_id': imdb_id,
                    'title': title,
                    'type': media_type
                })
                continue

            # Check if already in watchlist
            exists = Watchlist.objects.filter(
                user=self.user,
                content_type=ct,
                object_id=content_object.id
            ).exists()
            
            if exists:
                results['skipped'].append({
                    'title': content_object.title,
                    'reason': 'Already in watchlist'
                })
            else:
                results['imported'].append({
                    'object_id': content_object.id,
                    'content_type_id': ct.id,
                    'title': content_object.title,
                    'imdb_id': imdb_id,
                    'type': media_type
                })
                
        return results
