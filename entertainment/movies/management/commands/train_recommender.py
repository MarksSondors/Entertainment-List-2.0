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
    help = 'Trains the SVD recommender model using MovieLens 32M data (Scipy Implementation)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--optimize',
            action='store_true',
            help='Run hyperparameter tuning (Grid Search) for latent factors',
        )

    def log_memory(self, step_name):
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mb_used = mem_info.rss / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(f"[{step_name}] RAM Used: {mb_used:.2f} MB"))

    def load_data(self):
        """Loads and merges MovieLens and Local data"""
        self.log_memory("Start Load")
        
        # Paths
        data_dir = settings.BASE_DIR / 'data' / 'ml-32m'
        ratings_file = data_dir / 'ratings.csv'
        links_file = data_dir / 'links.csv'

        if not ratings_file.exists() or not links_file.exists():
            self.stdout.write(self.style.ERROR(f'Data files not found in {data_dir}. Run download_dataset first.'))
            return None

        # 1. Load MovieLens Data (with timestamp for splitting)
        self.stdout.write('Loading MovieLens ratings...')
        ratings = pd.read_csv(
            ratings_file, 
            usecols=['userId', 'movieId', 'rating', 'timestamp'],
            dtype={'userId': 'int32', 'movieId': 'int32', 'rating': 'float32', 'timestamp': 'int64'}
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

        # 2. Merge Data
        self.stdout.write('Merging ratings with TMDB IDs...')
        df = pd.merge(ratings, links, on='movieId', how='inner')
        del ratings
        del links
        gc.collect()

        # 3. Prepare Unified DataFrame
        df = df[['userId', 'tmdbId', 'rating', 'timestamp']]
        df['userId'] = 'ml_' + df['userId'].astype(str)
        
        # 4. Load Local Reviews
        self.stdout.write('Loading local reviews...')
        movie_ct = ContentType.objects.get_for_model(Movie)
        local_reviews = Review.objects.filter(content_type=movie_ct).values_list('user__id', 'object_id', 'rating', 'date_added')
        movie_tmdb_map = dict(Movie.objects.exclude(tmdb_id__isnull=True).values_list('id', 'tmdb_id'))

        local_data = []
        for user_id, movie_pk, rating, date_added in local_reviews:
            tmdb_id = movie_tmdb_map.get(movie_pk)
            if tmdb_id:
                local_data.append({
                    'userId': f'loc_{user_id}',
                    'tmdbId': tmdb_id,
                    'rating': rating / 2.0, # Normalize 1-10 -> 0.5-5.0
                    'timestamp': int(date_added.timestamp())
                })
        
        if local_data:
            local_df = pd.DataFrame(local_data)
            self.stdout.write(f"Including {len(local_df)} local reviews.")
            df = pd.concat([df, local_df], ignore_index=True)
            del local_df

        self.log_memory("Data Merged")
        return df

    def compute_biases(self, df, damping=5):
        """
        Computes Global Mean, Item Biases, and User Biases using Damped Least Squares.
        Score = Global + b_i + b_u + Interaction
        """
        self.stdout.write('Computing biases...')
        
        # 1. Global Mean
        global_mean = df['rating'].mean()
        
        # 2. Item Biases
        # b_i = sum(r - global) / (count + damping)
        item_stats = df.groupby('tmdbId')['rating'].agg(['sum', 'count'])
        item_stats['bias'] = (item_stats['sum'] - (item_stats['count'] * global_mean)) / (item_stats['count'] + damping)
        item_biases = item_stats['bias'].to_dict()
        
        # 3. User Biases
        # Subtract global and item bias to get residuals for user bias calculation
        # To do this efficiently in pandas without iteration:
        # Map item bias to original df
        df['item_bias'] = df['tmdbId'].map(item_stats['bias'])
        
        # Residual = Rating - Global - ItemBias
        df['user_resid'] = df['rating'] - global_mean - df['item_bias']
        
        user_stats = df.groupby('userId')['user_resid'].agg(['sum', 'count'])
        user_stats['bias'] = user_stats['sum'] / (user_stats['count'] + damping)
        user_biases = user_stats['bias'].to_dict()
        
        # Cleanup temp columns
        df.drop(columns=['item_bias', 'user_resid'], inplace=True, errors='ignore')

        return global_mean, item_biases, user_biases

    def create_sparse_matrix(self, df, global_mean, item_biases, user_biases):
        """
        Creates residuals matrix R = (Rating - Global - b_i - b_u)
        Returns: R_sparse, user_to_idx, item_to_idx
        """
        self.stdout.write('Preparing residuals for SVD...')
        
        # Compute normalized rating (Residual)
        # We need to map dicts efficiently
        # Since df can be huge, map() is slow. 
        # But we already have groupbys.
        
        # Map biases
        i_bias_series = df['tmdbId'].map(item_biases).fillna(0)
        u_bias_series = df['userId'].map(user_biases).fillna(0)
        
        df['residual'] = df['rating'] - global_mean - i_bias_series - u_bias_series
        
        # Create Indices
        unique_users = df['userId'].unique()
        unique_items = df['tmdbId'].unique()
        
        user_to_idx = {user: i for i, user in enumerate(unique_users)}
        item_to_idx = {item: i for i, item in enumerate(unique_items)}
        
        user_indices = df['userId'].map(user_to_idx).values
        item_indices = df['tmdbId'].map(item_to_idx).values
        ratings_array = df['residual'].values
        
        self.stdout.write('Building sparse matrix...')
        R_sparse = coo_matrix(
            (ratings_array, (user_indices, item_indices)), 
            shape=(len(user_to_idx), len(item_to_idx))
        ).tocsr()
        
        return R_sparse, user_to_idx, item_to_idx

    def evaluate_model(self, df_train, df_test, k):
        """Trains on Train set, computes RMSE on Test set"""
        # Compute biases on TRAIN data only
        global_mean, item_biases, user_biases = self.compute_biases(df_train.copy())
        
        # Build Matrix
        R_sparse, user_to_idx, item_to_idx = self.create_sparse_matrix(df_train.copy(), global_mean, item_biases, user_biases)
        
        # SVD
        self.stdout.write(f'Running SVD for k={k}...')
        try:
            u, s, vt = svds(R_sparse, k=k)
            # Reorder singular values
            u = u[:, ::-1]
            s = s[::-1]
            vt = vt[::-1, :]
            sigma = np.diag(s)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"SVD Failed for k={k}: {e}"))
            return float('inf')

        # Evaluate
        self.stdout.write(f'Evaluating k={k} on test set ({len(df_test)} rows)...')
        
        # Filter test set for known users/items only (Cold start is separate problem)
        known_users = set(user_to_idx.keys())
        known_items = set(item_to_idx.keys())
        
        df_test_filtered = df_test[
            df_test['userId'].isin(known_users) & 
            df_test['tmdbId'].isin(known_items)
        ]
        
        if df_test_filtered.empty:
            return 0.0

        test_u_indices = df_test_filtered['userId'].map(user_to_idx).values
        test_i_indices = df_test_filtered['tmdbId'].map(item_to_idx).values
        actual_ratings = df_test_filtered['rating'].values
        
        # Get biases
        test_g_mean = global_mean
        test_i_biases = df_test_filtered['tmdbId'].map(item_biases).fillna(0).values
        test_u_biases = df_test_filtered['userId'].map(user_biases).fillna(0).values
        
        # Get Dot Product Predictions
        batch_u = u[test_u_indices] # Shape (N, k)
        batch_v = vt[:, test_i_indices] # Shape (k, N)
        
        # interaction = sum( (u*s) * v.T )
        interaction_scores = np.sum(np.dot(batch_u, sigma) * batch_v.T, axis=1)
        
        predictions = test_g_mean + test_i_biases + test_u_biases + interaction_scores
        
        # Clip
        predictions = np.clip(predictions, 0.5, 5.0)
        
        mse = np.mean((predictions - actual_ratings) ** 2)
        rmse = np.sqrt(mse)
        
        self.stdout.write(f'RMSE for k={k}: {rmse:.4f}')
        return rmse

    def handle(self, *args, **options):
        try:
            df = self.load_data()
            if df is None: return

            # Optimize Step
            best_k = 50 # Default
            
            if options['optimize']:
                self.stdout.write(self.style.WARNING("Starting Hyperparameter Optimization (Grid Search)..."))
                
                # Time-based Split (80% / 20%)
                df.sort_values('timestamp', inplace=True)
                split_idx = int(len(df) * 0.8)
                
                df_train = df.iloc[:split_idx]
                df_test = df.iloc[split_idx:]
                
                self.stdout.write(f"Train Size: {len(df_train)}, Test Size: {len(df_test)}")
                
                param_grid = [20, 50, 100]
                best_rmse = float('inf')
                
                for k in param_grid:
                    rmse = self.evaluate_model(df_train, df_test, k)
                    if rmse < best_rmse:
                        best_rmse = rmse
                        best_k = k
                    
                    gc.collect()
                
                self.stdout.write(self.style.SUCCESS(f"Optimization Complete. Best k={best_k} (RMSE: {best_rmse:.4f})"))
                
                del df_train
                del df_test
                gc.collect()
            
            # Final Training
            self.stdout.write(self.style.SUCCESS(f"Training Final Model with k={best_k}..."))
            
            # Compute Biases on Full Data
            global_mean, item_biases, user_biases = self.compute_biases(df)
            
            # Create Sparse Matrix
            R_sparse, user_to_idx, item_to_idx = self.create_sparse_matrix(df, global_mean, item_biases, user_biases)
            
            # Run SVD
            u, s, vt = svds(R_sparse, k=best_k)
            
            # Reorder
            u = u[:, ::-1]
            s = s[::-1]
            vt = vt[::-1, :]
            sigma = np.diag(s)
            
            # Save
            output_dir = settings.BASE_DIR / 'movies' / 'ml_models'
            output_file = output_dir / 'svd_model.pkl'
            
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            known_tmdb_ids = list(item_biases.keys())

            model_data = {
                'U': u,
                'Sigma': sigma,
                'Vt': vt,
                'user_to_idx': user_to_idx,
                'item_to_idx': item_to_idx,
                'known_tmdb_ids': known_tmdb_ids,
                'global_mean': float(global_mean),
                'item_biases': item_biases,
                'user_biases': user_biases,
                'k': best_k
            }
            
            with open(output_file, 'wb') as f:
                pickle.dump(model_data, f)
            
            self.stdout.write(self.style.SUCCESS(f'Successfully saved model to {output_file} (Size: {os.path.getsize(output_file)/1024/1024:.2f} MB)'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc())
