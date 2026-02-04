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
        movies_file = data_dir / 'movies.csv'

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

        self.stdout.write('Loading MovieLens movies...')
        movies = pd.read_csv(
            movies_file,
            usecols=['movieId', 'title']
        )
        # Extract year regex
        movies['year'] = movies['title'].str.extract(r'\((\d{4})\)').fillna(1900).astype('int32')
        movies = movies[['movieId', 'year']]

        # 2. Merge Data
        self.stdout.write('Merging ratings with TMDB IDs and Years...')
        df = pd.merge(ratings, links, on='movieId', how='inner')
        df = pd.merge(df, movies, on='movieId', how='left')
        df['year'] = df['year'].fillna(1900).astype('int32')

        del ratings
        del links
        del movies
        gc.collect()

        # 3. Prepare Unified DataFrame
        df = df[['userId', 'tmdbId', 'rating', 'timestamp', 'year']]
        df['userId'] = 'ml_' + df['userId'].astype(str)
        
        # 4. Load Local Reviews
        self.stdout.write('Loading local reviews...')
        movie_ct = ContentType.objects.get_for_model(Movie)
        local_reviews = Review.objects.filter(content_type=movie_ct).values_list('user__id', 'object_id', 'rating', 'date_added')
        
        movies_qs = Movie.objects.exclude(tmdb_id__isnull=True).values('id', 'tmdb_id', 'release_date')
        movie_map = {}
        for m in movies_qs:
            y = m['release_date'].year if m['release_date'] else 1900
            movie_map[m['id']] = (m['tmdb_id'], y)

        local_data = []
        for user_id, movie_pk, rating, date_added in local_reviews:
            if movie_pk in movie_map:
                tmdb_id, year = movie_map[movie_pk]
                local_data.append({
                    'userId': f'loc_{user_id}',
                    'tmdbId': tmdb_id,
                    'rating': rating / 2.0, # Normalize 1-10 -> 0.5-5.0
                    'timestamp': int(date_added.timestamp()),
                    'year': year
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
        Computes Global Mean, Year Biases, Item Biases, and User Biases using Damped Least Squares.
        Score = Global + b_y + b_i + b_u + Interaction
        Does NOT modify df in-place to save memory.
        """
        self.stdout.write('Computing biases...')
        
        # 1. Global Mean
        global_mean = df['rating'].mean()

        # 2. Year Biases
        year_stats = df.groupby('year')['rating'].agg(['sum', 'count'])
        year_stats['bias'] = (year_stats['sum'] - (year_stats['count'] * global_mean)) / (year_stats['count'] + damping)
        year_biases = year_stats['bias'].to_dict()

        # 3. Item Biases
        # b_i = sum(r - global - year_bias) / (count + damping)
        # Use series arithmetic instead of column assignment to save memory
        y_bias_series = df['year'].map(year_biases).fillna(0).astype('float32')
        item_resid_series = (df['rating'] - global_mean - y_bias_series).astype('float32')
        
        item_stats = item_resid_series.groupby(df['tmdbId']).agg(['sum', 'count'])
        item_stats['bias'] = item_stats['sum'] / (item_stats['count'] + damping)
        item_biases = item_stats['bias'].to_dict()
        
        # Clean up
        del y_bias_series
        del item_resid_series
        del item_stats
        
        # 4. User Biases
        # Residual = Rating - Global - YearBias - ItemBias
        # We need to recompute biases series (CPU trade-off for Memory)
        y_bias_series = df['year'].map(year_biases).fillna(0).astype('float32')
        i_bias_series = df['tmdbId'].map(item_biases).fillna(0).astype('float32')
        
        user_resid_series = (df['rating'] - global_mean - y_bias_series - i_bias_series).astype('float32')
        
        user_stats = user_resid_series.groupby(df['userId']).agg(['sum', 'count'])
        user_stats['bias'] = user_stats['sum'] / (user_stats['count'] + damping)
        user_biases = user_stats['bias'].to_dict()
        
        del y_bias_series
        del i_bias_series
        del user_resid_series
        del user_stats
        gc.collect()

        return global_mean, year_biases, item_biases, user_biases

    def create_sparse_matrix(self, df, global_mean, year_biases, item_biases, user_biases):
        """
        Creates residuals matrix R = (Rating - Global - b_y - b_i - b_u)
        Returns: R_sparse, user_to_idx, item_to_idx
        """
        self.stdout.write('Preparing residuals for SVD...')
        
        y_bias_series = df['year'].map(year_biases).fillna(0).astype('float32')
        i_bias_series = df['tmdbId'].map(item_biases).fillna(0).astype('float32')
        u_bias_series = df['userId'].map(user_biases).fillna(0).astype('float32')
        
        residuals = (df['rating'] - global_mean - y_bias_series - i_bias_series - u_bias_series).astype('float32').values
        
        del y_bias_series
        del i_bias_series
        del u_bias_series
        gc.collect()
        
        # Create Indices
        unique_users = df['userId'].unique()
        unique_items = df['tmdbId'].unique()
        
        user_to_idx = {user: i for i, user in enumerate(unique_users)}
        item_to_idx = {item: i for i, item in enumerate(unique_items)}
        
        user_indices = df['userId'].map(user_to_idx).values
        item_indices = df['tmdbId'].map(item_to_idx).values
        
        self.stdout.write('Building sparse matrix...')
        R_sparse = coo_matrix(
            (residuals, (user_indices, item_indices)), 
            shape=(len(user_to_idx), len(item_to_idx)),
            dtype=np.float32
        ).tocsr()
        
        return R_sparse, user_to_idx, item_to_idx

    def evaluate_model(self, df_train, df_test, k, damping=5):
        """Trains on Train set, computes RMSE on Test set"""
        # NO COPIES: Pass df_train directly, compute_biases and create_sparse_matrix will not modify it
        global_mean, year_biases, item_biases, user_biases = self.compute_biases(df_train, damping=damping)
        
        # Build Matrix
        R_sparse, user_to_idx, item_to_idx = self.create_sparse_matrix(df_train, global_mean, year_biases, item_biases, user_biases)
        
        # SVD
        try:
            # Optimize k if it's too large for the matrix dims
            min_dim = min(R_sparse.shape)
            if k >= min_dim:
                k = max(1, min_dim - 1)

            u, s, vt = svds(R_sparse, k=k)
            
            # Reduce precision immediately
            u = u.astype(np.float32)
            s = s.astype(np.float32)
            vt = vt.astype(np.float32)
            
            # Reorder singular values
            u = u[:, ::-1]
            s = s[::-1]
            vt = vt[::-1, :]
            sigma = np.diag(s)
            
            del R_sparse
            gc.collect()
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"SVD Failed: {e}"))
            return float('inf')

        # Evaluate
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
        actual_ratings = df_test_filtered['rating'].values.astype(np.float32)
        
        # Get biases
        test_g_mean = float(global_mean)
        
        if 'year' in df_test_filtered.columns:
             test_y_biases = df_test_filtered['year'].map(year_biases).fillna(0).values.astype(np.float32)
        else:
             test_y_biases = np.zeros(len(df_test_filtered), dtype=np.float32)

        test_i_biases = df_test_filtered['tmdbId'].map(item_biases).fillna(0).values.astype(np.float32)
        test_u_biases = df_test_filtered['userId'].map(user_biases).fillna(0).values.astype(np.float32)
        
        # Get Dot Product Predictions
        batch_u = u[test_u_indices] # Shape (N, k)
        batch_v = vt[:, test_i_indices] # Shape (k, N)
        
        # Optimized: interaction = sum( (U . Sigma) * V.T, axis=1 )
        u_weighted = np.dot(batch_u, sigma)
        interaction_scores = np.sum(u_weighted * batch_v.T, axis=1)
        
        predictions = test_g_mean + test_y_biases + test_i_biases + test_u_biases + interaction_scores
        
        # Clip
        predictions = np.clip(predictions, 0.5, 5.0)
        
        mse = np.mean((predictions - actual_ratings) ** 2)
        rmse = np.sqrt(mse)
        
        del u, s, vt, sigma, batch_u, batch_v, u_weighted, interaction_scores
        gc.collect()
        
        return rmse

    def handle(self, *args, **options):
        try:
            df = self.load_data()
            if df is None: return

            # Optimize Step
            best_k = 50 
            best_damping = 5
            
            if options['optimize']:
                try:
                    import optuna
                    optuna.logging.set_verbosity(optuna.logging.WARNING)
                    self.stdout.write(self.style.WARNING("Starting Hyperparameter Optimization (Optuna)..."))
                except ImportError:
                    self.stdout.write(self.style.ERROR("Optuna not found. Please install `optuna` to use optimization."))
                    return
                
                # Time-based Split (80% / 20%)
                df.sort_values('timestamp', inplace=True)
                split_idx = int(len(df) * 0.8)
                
                df_train = df.iloc[:split_idx]
                df_test = df.iloc[split_idx:]
                
                self.stdout.write(f"Train Size: {len(df_train)}, Test Size: {len(df_test)}")
                
                def objective(trial):
                    k = trial.suggest_int('k', 20, 150)
                    damping = trial.suggest_int('damping', 1, 25)
                    
                    rmse = self.evaluate_model(df_train, df_test, k, damping)
                    self.stdout.write(f"Trial {trial.number}: k={k}, damping={damping} -> RMSE: {rmse:.4f}")
                    gc.collect()
                    return rmse

                study = optuna.create_study(direction='minimize')
                study.optimize(objective, n_trials=20)
                
                best_k = study.best_params['k']
                best_damping = study.best_params['damping']
                
                self.stdout.write(self.style.SUCCESS(f"Optimization Complete. Best params: k={best_k}, damping={best_damping} (RMSE: {study.best_value:.4f})"))
                
                del df_train
                del df_test
                gc.collect()

            # Final Training
            self.stdout.write(self.style.SUCCESS(f"Training Final Model with k={best_k}, damping={best_damping}..."))
            
            # Compute Biases on Full Data
            global_mean, year_biases, item_biases, user_biases = self.compute_biases(df, damping=best_damping)
            
            # Create Sparse Matrix
            R_sparse, user_to_idx, item_to_idx = self.create_sparse_matrix(df, global_mean, year_biases, item_biases, user_biases)
            
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

            # Prepare tmdb_id_to_year
            tmdb_id_to_year = df[['tmdbId', 'year']].drop_duplicates(subset=['tmdbId']).set_index('tmdbId')['year'].to_dict()

            model_data = {
                'U': u,
                'Sigma': sigma,
                'Vt': vt,
                'user_to_idx': user_to_idx,
                'item_to_idx': item_to_idx,
                'known_tmdb_ids': known_tmdb_ids,
                'global_mean': float(global_mean),
                'year_biases': year_biases,
                'item_biases': item_biases,
                'user_biases': user_biases,
                'tmdb_id_to_year': tmdb_id_to_year,
                'k': best_k,
                'damping': best_damping
            }
            
            with open(output_file, 'wb') as f:
                pickle.dump(model_data, f)
            
            self.stdout.write(self.style.SUCCESS(f'Successfully saved model to {output_file} (Size: {os.path.getsize(output_file)/1024/1024:.2f} MB)'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc())
