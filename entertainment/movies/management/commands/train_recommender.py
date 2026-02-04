import os
import psutil
import pickle
import pandas as pd
import numpy as np
import gc
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.linalg import svds
from django.core.management.base import BaseCommand
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from movies.models import Movie
from custom_auth.models import Review

class Command(BaseCommand):
    help = 'Trains the SVD recommender model using MovieLens 32M data and local reviews (Scipy Implementation)'

    def log_memory(self, step_name):
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mb_used = mem_info.rss / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(f"[{step_name}] RAM Used: {mb_used:.2f} MB"))

    def handle(self, *args, **options):
        self.log_memory("Start")

        # Paths
        data_dir = settings.BASE_DIR / 'data' / 'ml-32m'
        ratings_file = data_dir / 'ratings.csv'
        links_file = data_dir / 'links.csv'
        
        output_dir = settings.BASE_DIR / 'movies' / 'ml_models'
        output_file = output_dir / 'svd_model.pkl'

        if not ratings_file.exists() or not links_file.exists():
            self.stdout.write(self.style.ERROR(f'Data files not found in {data_dir}.'))
            return

        # 1. Load MovieLens Data
        self.stdout.write('Loading MovieLens ratings...')
        try:
            ratings = pd.read_csv(
                ratings_file, 
                usecols=['userId', 'movieId', 'rating'],
                dtype={'userId': 'int32', 'movieId': 'int32', 'rating': 'float32'}
            )
            self.log_memory("Ratings Loaded")

            self.stdout.write('Loading MovieLens links...')
            links = pd.read_csv(
                links_file, 
                usecols=['movieId', 'tmdbId'],
                dtype={'movieId': 'int32', 'tmdbId': 'float32'}
            )
            links = links.dropna(subset=['tmdbId'])
            links['tmdbId'] = links['tmdbId'].astype('int32')
            self.log_memory("Links Loaded")

            # 2. Merge Data
            self.stdout.write('Merging ratings with TMDB IDs...')
            df = pd.merge(ratings, links, on='movieId', how='inner')
            del ratings
            del links
            gc.collect()
            self.log_memory("Merged Data")

            # 3. Extract Known IDs
            self.stdout.write('Extracting known IDs...')
            known_tmdb_ids = set(df['tmdbId'].unique().tolist())
            
            # 4. Prepare Unified DataFrame
            df = df[['userId', 'tmdbId', 'rating']]
            
            # Map MovieLens User IDs to string keys
            df['userId'] = 'ml_' + df['userId'].astype(str)
            
            # 5. Load Local Reviews
            self.stdout.write('Loading local reviews...')
            movie_ct = ContentType.objects.get_for_model(Movie)
            local_reviews = Review.objects.filter(content_type=movie_ct).values_list('user__id', 'object_id', 'rating')
            movie_tmdb_map = dict(Movie.objects.exclude(tmdb_id__isnull=True).values_list('id', 'tmdb_id'))

            local_data = []
            for user_id, movie_pk, rating in local_reviews:
                tmdb_id = movie_tmdb_map.get(movie_pk)
                if tmdb_id:
                    local_data.append({
                        'userId': f'loc_{user_id}',
                        'tmdbId': tmdb_id,
                        'rating': rating / 2.0  # Normalize 1-10 -> 0.5-5.0
                    })
            
            if local_data:
                local_df = pd.DataFrame(local_data)
                self.stdout.write(f"Including {len(local_df)} local reviews.")
                df = pd.concat([df, local_df], ignore_index=True)
                local_tmdb_ids = set([r['tmdbId'] for r in local_data])
                known_tmdb_ids.update(local_tmdb_ids)
                del local_df

            self.log_memory("Combined Data")

            # Calculate User Means for Normalize Matrix
            self.stdout.write('Calculating user means...')
            user_means = df.groupby('userId')['rating'].mean()
            # Convert to dictionary {userId: mean_rating}
            user_means_dict = user_means.to_dict()
            global_mean = df['rating'].mean()

            # Subtract mean from ratings
            self.stdout.write('Normalizing ratings...')
            # Map mean to each row
            means = df['userId'].map(user_means)
            df['rating'] = df['rating'] - means

            # 6. Create Sparse Matrix
            self.stdout.write('Creating mappings...')
            
            # Create integer mapping for Users and Items
            # unique() returns numpy array, distinct values
            unique_users = df['userId'].unique()
            unique_items = df['tmdbId'].unique()
            
            user_to_idx = {user: i for i, user in enumerate(unique_users)}
            item_to_idx = {item: i for i, item in enumerate(unique_items)}
            
            # Map DataFrame to integers
            self.stdout.write('Mapping data to indices...')
            user_indices = df['userId'].map(user_to_idx).values
            item_indices = df['tmdbId'].map(item_to_idx).values
            ratings_array = df['rating'].values
            
            # Free DF
            del df
            del unique_users 
            del unique_items
            gc.collect()
            
            self.stdout.write('Building sparse matrix...')
            # Create CSR Matrix: (Users, Items)
            # Use float32 for space efficiency
            R_sparse = coo_matrix((ratings_array, (user_indices, item_indices)), shape=(len(user_to_idx), len(item_to_idx))).tocsr()
            
            self.log_memory("Sparse Matrix Built")

            # 7. Train SVD (SVDs from Scipy)
            # Use k=50 latent factors (adjustable based on memory/performance)
            self.stdout.write('Running SVD (k=50)...')
            k = 50
            
            # u, s, vt = svds(R_sparse, k=k)
            # Note: svds returns eigenvalues sorted smallest to largest.
            u, s, vt = svds(R_sparse, k=k)
            
            # Reverse them to act like standard SVD (largest singular values first)
            u = u[:, ::-1]
            s = s[::-1]
            vt = vt[::-1, :]
            
            # Create diagonal sigma matrix
            sigma = np.diag(s)
            
            self.log_memory("SVD Computed")

            # 8. Save
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            self.stdout.write(f'Saving model to {output_file}...')
            
            # We need to save the Matrices + Mappings
            # We don't save the full R_sparse, too big. Just the decomposed vectors.
            
            model_data = {
                'U': u,
                'Sigma': sigma,
                'Vt': vt,
                'user_to_idx': user_to_idx,
                'item_to_idx': item_to_idx,
                'known_tmdb_ids': list(known_tmdb_ids),
                'user_means': user_means_dict,
                'global_mean': float(global_mean)
            }
            
            with open(output_file, 'wb') as f:
                pickle.dump(model_data, f)
            
            self.stdout.write(self.style.SUCCESS(f'Successfully trained SVD (Size: {os.path.getsize(output_file)/1024/1024:.2f} MB)'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc())
