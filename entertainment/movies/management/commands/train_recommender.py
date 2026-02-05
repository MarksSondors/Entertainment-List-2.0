import os
import psutil
import pickle
import pandas as pd
import numpy as np
import gc
import re
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.linalg import svds
from django.core.management.base import BaseCommand
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from movies.models import Movie
from custom_auth.models import Review

class Command(BaseCommand):
    help = 'Trains the SVD recommender model using MovieLens 32M data (Scipy Implementation)'

    # Mapping TMDB Genre Names -> MovieLens Genre Names
    TMDB_TO_MOVIELENS = {
        'Action': 'Action',
        'Adventure': 'Adventure',
        'Animation': 'Animation',
        'Comedy': 'Comedy',
        'Crime': 'Crime',
        'Documentary': 'Documentary',
        'Drama': 'Drama',
        'Family': "Children's", # Mapped to ML standard
        'Fantasy': 'Fantasy',
        'History': 'Drama', # Approximation
        'Horror': 'Horror',
        'Music': 'Musical', # Mapped
        'Mystery': 'Mystery',
        'Romance': 'Romance',
        'Science Fiction': 'Sci-Fi', # Mapped
        'Thriller': 'Thriller',
        'War': 'War',
        'Western': 'Western',
        'TV Movie': None # No equivalent
    }

    def add_arguments(self, parser):
        parser.add_argument(
            '--optimize',
            action='store_true',
            help='Run hyperparameter tuning (Optuna) for latent factors',
        )

    def log_memory(self, step_name):
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        mb_used = mem_info.rss / 1024 / 1024
        self.stdout.write(self.style.SUCCESS(f"[{step_name}] RAM Used: {mb_used:.2f} MB"))

    def load_metadata_and_genres(self):
        """
        Loads MovieLens movies.csv to get:
        1. Year (parsed from title)
        2. Genres (parsed from pipe-separated string)
        3. ID Mapping (MovieLens -> TMDB)
        Returns: meta_df (for merging), tmdb_to_genres (for model export)
        """
        self.log_memory("Start Metadata Load")
        data_dir = settings.BASE_DIR / 'data' / 'ml-32m'
        movies_file = data_dir / 'movies.csv'
        links_file = data_dir / 'links.csv'

        if not movies_file.exists() or not links_file.exists():
            self.stdout.write(self.style.ERROR(f'Data files not found in {data_dir}.'))
            return None, None

        # 1. Load Links (ML ID -> TMDB ID)
        links = pd.read_csv(
            links_file, 
            usecols=['movieId', 'tmdbId'],
            dtype={'movieId': 'int32', 'tmdbId': 'float'}
        ).dropna().astype({'movieId': 'int32', 'tmdbId': 'int32'})

        # 2. Load Movies (ID, Title, Genres)
        movies = pd.read_csv(
            movies_file,
            usecols=['movieId', 'title', 'genres'],
            dtype={'movieId': 'int32', 'genres': 'string'}
        )

        # Extract Year 
        movies['year'] = movies['title'].str.extract(r'\((\d{4})\)', expand=False).fillna(1900).astype('int32')
        
        # Merge
        meta_df = pd.merge(movies, links, on='movieId', how='inner')
        
        # Create TMDB ID -> Genres list map
        # Used by the recommender to look up genres for "Known" movies
        self.stdout.write("Building ID->Genre Map...")
        tmdb_to_genres = {}
        for row in meta_df.itertuples(index=False):
            # row structure: movieId, title, genres, year, tmdbId
            # We skip 'genres' if it is (no genres listed)
            if hasattr(row, 'genres') and isinstance(row.genres, str) and row.genres != '(no genres listed)':
                tmdb_to_genres[row.tmdbId] = row.genres.split('|')
            else:
                tmdb_to_genres[row.tmdbId] = []

        del movies
        del links
        gc.collect()

        return meta_df[['movieId', 'tmdbId', 'year', 'genres']], tmdb_to_genres

    def load_data(self):
        """Loads Ratings and merges with Metadata"""
        meta_df, tmdb_to_genres = self.load_metadata_and_genres()
        if meta_df is None: return None, None

        self.log_memory("Loading Ratings")
        data_dir = settings.BASE_DIR / 'data' / 'ml-32m'
        ratings_file = data_dir / 'ratings.csv'

        # Load Ratings
        ratings = pd.read_csv(
            ratings_file, 
            usecols=['userId', 'movieId', 'rating', 'timestamp'],
            dtype={'userId': 'int32', 'movieId': 'int32', 'rating': 'float32', 'timestamp': 'int64'}
        )
        
        # 3. Pruning: Filter sparse MovieLens users (<10 ratings)
        self.stdout.write("Pruning sparse MovieLens users (<10 ratings)...")
        user_counts = ratings['userId'].value_counts()
        valid_users = user_counts[user_counts >= 10].index
        ratings = ratings[ratings['userId'].isin(valid_users)]
        self.stdout.write(f"Remaining Ratings: {len(ratings)}")
        del user_counts, valid_users
        gc.collect()
        
        # Merge Metadata (Inner join filters ratings for movies without TMDB IDs)
        self.stdout.write('Merging ratings with Metadata...')
        df = pd.merge(ratings, meta_df, on='movieId', how='inner')
        
        del ratings
        del meta_df
        gc.collect()

        # Format Columns
        df['userId'] = 'ml_' + df['userId'].astype(str)
        # Create Decade Column
        df['decade'] = (df['year'] // 10) * 10
        df = df[['userId', 'tmdbId', 'rating', 'timestamp', 'year', 'decade', 'genres']]

        # Load Local Reviews
        self.stdout.write('Loading local reviews...')
        movie_ct = ContentType.objects.get_for_model(Movie)
        local_reviews = Review.objects.filter(content_type=movie_ct).values_list('user__id', 'object_id', 'rating', 'date_added')
        
        # Build local movie map efficiently
        movies_qs = Movie.objects.exclude(tmdb_id__isnull=True).values('id', 'tmdb_id', 'release_date')
        movie_map = {}
        for m in movies_qs:
            y = m['release_date'].year if m['release_date'] else 1900
            movie_map[m['id']] = (m['tmdb_id'], y)
        
        # We assume local genres are not needed for training User-Genre bias 
        # (simpler to just assume 'genres' column is empty for local rows to save complexity, 
        # or we could map them. For now, we leave them blank/NaN to avoid regex errors)
        local_data = []
        for user_id, movie_pk, rating, date_added in local_reviews:
            if movie_pk in movie_map:
                tmdb_id, year = movie_map[movie_pk]
                local_data.append({
                    'userId': f'loc_{user_id}',
                    'tmdbId': tmdb_id,
                    'rating': rating / 2.0, 
                    'timestamp': int(date_added.timestamp()),
                    'year': year,
                    'decade': (year // 10) * 10,
                    'genres': '' # Local reviews won't contribute to learning genre bias, but will use it
                })
        
        if local_data:
            local_df = pd.DataFrame(local_data)
            self.stdout.write(f"Including {len(local_df)} local reviews.")
            df = pd.concat([df, local_df], ignore_index=True)
            del local_df

        self.log_memory("Data Merged")
        return df, tmdb_to_genres

    def compute_biases(self, df, damping=5):
        """
        Compute Global, Year, Item, User, User-Genre, and User-Decade biases.
        Hierarchy: Global -> Year -> Item -> User -> User-Genre -> User-Decade
        """
        self.stdout.write('Computing biases...')
        
        # 1. Global Mean
        global_mean = df['rating'].mean()

        # 2. Year Biases
        year_stats = df.groupby('year')['rating'].agg(['sum', 'count'])
        year_stats['bias'] = (year_stats['sum'] - (year_stats['count'] * global_mean)) / (year_stats['count'] + damping)
        year_biases = year_stats['bias'].to_dict()

        # 3. Item Biases
        y_bias_series = df['year'].map(year_biases).fillna(0).astype('float32')
        item_resid_series = (df['rating'] - global_mean - y_bias_series).astype('float32')
        
        item_stats = item_resid_series.groupby(df['tmdbId']).agg(['sum', 'count'])
        item_stats['bias'] = item_stats['sum'] / (item_stats['count'] + damping)
        item_biases = item_stats['bias'].to_dict()
        
        del y_bias_series, item_resid_series, item_stats
        
        # 4. User Biases
        y_bias_series = df['year'].map(year_biases).fillna(0).astype('float32')
        i_bias_series = df['tmdbId'].map(item_biases).fillna(0).astype('float32')
        
        user_resid_series = (df['rating'] - global_mean - y_bias_series - i_bias_series).astype('float32')
        
        user_stats = user_resid_series.groupby(df['userId']).agg(['sum', 'count'])
        user_stats['bias'] = user_stats['sum'] / (user_stats['count'] + damping)
        user_biases = user_stats['bias'].to_dict()
        
        # 5. User-Genre Biases
        self.stdout.write("Computing User-Genre Biases...")
        
        # Add residual column to DF for genre iteration
        df['residual'] = (user_resid_series - df['userId'].map(user_biases).fillna(0)).astype('float32')
        
        del y_bias_series, i_bias_series, user_resid_series, user_stats
        gc.collect()

        # We stick to the standard MovieLens genre vocabulary
        genres_list = [
            "Action", "Adventure", "Animation", "Children's", "Comedy", "Crime", 
            "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "Musical", 
            "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western"
        ]
        
        user_genre_biases = {} # { Genre: { UserId: Bias } }
        df['genres'] = df['genres'].astype(str)
        
        for genre in genres_list:
            # "Contains" is memory efficient enough
            mask = df['genres'].str.contains(genre, regex=False)
            if not mask.any(): continue
            
            subset = df.loc[mask, ['userId', 'residual']]
            stats = subset.groupby('userId')['residual'].agg(['sum', 'count'])
            
            # Higher damping for genres to be conservative
            genre_damping_val = damping * 2 
            bias = stats['sum'] / (stats['count'] + genre_damping_val)
            
            user_genre_biases[genre] = bias.to_dict()
            
            # Subtract this specific bias from residual so future genres (or SVD) don't learn it again
            # Note: A movie can be Action AND Sci-Fi. We subtract iteratively.
            mapper = df.loc[mask, 'userId'].map(user_genre_biases[genre]).fillna(0).astype('float32')
            df.loc[mask, 'residual'] -= mapper
            
            del mask, subset, stats, mapper
            gc.collect()

        # 6. User-Decade Biases
        self.stdout.write("Computing User-Decade Biases...")
        user_decade_biases = {}
        # 1900 to 2030, step 10
        for decade in range(1900, 2030, 10):
            mask = (df['decade'] == decade)
            if not mask.any(): continue
            
            subset = df.loc[mask, ['userId', 'residual']]
            stats = subset.groupby('userId')['residual'].agg(['sum', 'count'])
            
            # Less aggressive damping for decades as they are exclusive (mostly)
            decade_damping = damping * 2
            bias = stats['sum'] / (stats['count'] + decade_damping)
            
            user_decade_biases[decade] = bias.to_dict()
            
            mapper = df.loc[mask, 'userId'].map(user_decade_biases[decade]).fillna(0).astype('float32')
            df.loc[mask, 'residual'] -= mapper
            
            del mask, subset, stats, mapper
            gc.collect()

        # Cleanup residual column before SVD steps (which re-calculate it to save RAM state)
        df.drop(columns=['residual'], inplace=True)

        return global_mean, year_biases, item_biases, user_biases, user_genre_biases, user_decade_biases

    def create_sparse_matrix(self, df, global_mean, year_biases, item_biases, user_biases, user_genre_biases, user_decade_biases):
        """ Creates residuals matrix R for SVD """
        self.stdout.write('Preparing residuals for SVD...')
        
        # Base Residual
        y_bias = df['year'].map(year_biases).fillna(0).astype('float32')
        i_bias = df['tmdbId'].map(item_biases).fillna(0).astype('float32')
        u_bias = df['userId'].map(user_biases).fillna(0).astype('float32')
        
        # Decade Bias
        d_bias_total = np.zeros(len(df), dtype=np.float32)
        for decade, bias_map in user_decade_biases.items():
            mask = (df['decade'] == decade)
            if mask.any():
                b = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32')
                d_bias_total[mask] = b
                del b

        # Genre Bias
        g_bias_total = np.zeros(len(df), dtype=np.float32)
        for genre, bias_map in user_genre_biases.items():
            mask = df['genres'].str.contains(genre, regex=False)
            if mask.any():
                # Map user bias for this genre to the rows
                b = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32')
                g_bias_total[mask] += b
                del b
            del mask

        residuals = (df['rating'] - global_mean - y_bias - i_bias - u_bias - d_bias_total).values - g_bias_total
        
        del y_bias, i_bias, u_bias, g_bias_total, d_bias_total
        gc.collect()
        
        # Indices
        user_to_idx = {u: i for i, u in enumerate(df['userId'].unique())}
        item_to_idx = {i: idx for idx, i in enumerate(df['tmdbId'].unique())}
        
        u_indices = df['userId'].map(user_to_idx).values
        i_indices = df['tmdbId'].map(item_to_idx).values
        
        self.stdout.write('Building sparse matrix...')
        R_sparse = coo_matrix(
            (residuals.astype(np.float32), (u_indices, i_indices)), 
            shape=(len(user_to_idx), len(item_to_idx)),
            dtype=np.float32
        ).tocsr()
        
        return R_sparse, user_to_idx, item_to_idx

    def evaluate_model(self, df_train, df_test, k, damping=5):
        """ Evaluates RMSE with full bias hierarchy """
        # We process train copy
        global_mean, year_biases, item_biases, user_biases, user_genre_biases, user_decade_biases = self.compute_biases(df_train, damping)
        
        try:
            R_sparse, u_map, i_map = self.create_sparse_matrix(df_train, global_mean, year_biases, item_biases, user_biases, user_genre_biases, user_decade_biases)
            
            min_dim = min(R_sparse.shape)
            k = min(k, max(1, min_dim - 1))
            
            u, s, vt = svds(R_sparse, k=k)
            u, s, vt = u.astype(np.float32), s.astype(np.float32), vt.astype(np.float32)
            
            sigma = np.diag(s)
            del R_sparse
            gc.collect()
        except Exception:
            return float('inf')

        # Eval Test
        known_u = set(u_map.keys())
        known_i = set(i_map.keys())
        test_df = df_test[df_test['userId'].isin(known_u) & df_test['tmdbId'].isin(known_i)].copy()
        
        if test_df.empty: return 0.0
        
        # Base Predictions
        pred = np.full(len(test_df), global_mean, dtype=np.float32)
        pred += test_df['year'].map(year_biases).fillna(0).values.astype(np.float32)
        pred += test_df['tmdbId'].map(item_biases).fillna(0).values.astype(np.float32)
        pred += test_df['userId'].map(user_biases).fillna(0).values.astype(np.float32)
        
        # Genre Predictions
        test_df['genres'] = test_df['genres'].astype(str)
        for genre, bias_map in user_genre_biases.items():
            mask = test_df['genres'].str.contains(genre, regex=False)
            if mask.any():
                pred[mask] += test_df.loc[mask, 'userId'].map(bias_map).fillna(0).values.astype(np.float32)

        # Decade Predictions
        for decade, bias_map in user_decade_biases.items():
            mask = (test_df['decade'] == decade)
            if mask.any():
                pred[mask] += test_df.loc[mask, 'userId'].map(bias_map).fillna(0).values.astype(np.float32)

        # SVD Interaction
        u_idx = test_df['userId'].map(u_map).values
        i_idx = test_df['tmdbId'].map(i_map).values
        
        u_vecs = np.dot(u[u_idx], sigma)
        vt_vecs = vt[:, i_idx]
        
        interaction = np.sum(u_vecs * vt_vecs.T, axis=1)
        pred += interaction
        
        rmse = np.sqrt(np.mean((pred - test_df['rating'].values) ** 2))
        return rmse

    def handle(self, *args, **options):
        try:
            df, tmdb_to_genres = self.load_data()
            if df is None: return

            best_k, best_damping = 120, 15
            
            if options['optimize']:
                try:
                    import optuna
                    optuna.logging.set_verbosity(optuna.logging.WARNING)
                    self.stdout.write("Starting Optuna optimization...")
                    
                    # Manual Split to avoid sklearn dependency
                    mask = np.random.rand(len(df)) < 0.8
                    train_df = df[mask].copy()
                    val_df = df[~mask].copy()
                    
                    def objective(trial):
                        k = trial.suggest_int('k', 20, 100)
                        damping = trial.suggest_int('damping', 2, 15)
                        # We use a copy of train/val for safety if evaluate modifies them, though evaluate makes internal copies/computations.
                        return self.evaluate_model(train_df, val_df, k, damping)
                        
                    study = optuna.create_study(direction='minimize')
                    study.optimize(objective, n_trials=15) # 15 trials is a good balance
                    
                    best_k = study.best_params['k']
                    best_damping = study.best_params['damping']
                    self.stdout.write(self.style.SUCCESS(f"Best Params: k={best_k}, damping={best_damping}"))
                    
                    del train_df, val_df, study, mask
                    gc.collect()
                    
                except ImportError:
                    self.stdout.write(self.style.WARNING("Optuna not installed. Skipping optimization."))

            self.stdout.write(f"Training Final Model (k={best_k})...")
            
            # Full Train
            global_mean, year_biases, item_biases, user_biases, user_genre_biases, user_decade_biases = self.compute_biases(df, best_damping)
            R_sparse, u_map, i_map = self.create_sparse_matrix(df, global_mean, year_biases, item_biases, user_biases, user_genre_biases, user_decade_biases)
            
            u, s, vt = svds(R_sparse, k=best_k)
            # ... process matrices ...
            u = u[:, ::-1].astype(np.float32)
            s = s[::-1].astype(np.float32)
            vt = vt[::-1, :].astype(np.float32)
            sigma = np.diag(s)

            # Export
            data = {
                'U': u, 'Sigma': sigma, 'Vt': vt,
                'user_to_idx': u_map, 'item_to_idx': i_map,
                'known_tmdb_ids': list(i_map.keys()),
                'global_mean': float(global_mean),
                'year_biases': year_biases,
                'item_biases': item_biases,
                'user_biases': user_biases,
                'user_genre_biases': user_genre_biases,
                'user_decade_biases': user_decade_biases,
                'tmdb_to_genres': tmdb_to_genres,
                'genre_mapping': self.TMDB_TO_MOVIELENS, # Saving mapping!
                'tmdb_id_to_year': df[['tmdbId', 'year']].drop_duplicates('tmdbId').set_index('tmdbId')['year'].to_dict()
            }
            
            out_file = settings.BASE_DIR / 'movies' / 'ml_models' / 'svd_model.pkl'
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            with open(out_file, 'wb') as f:
                pickle.dump(data, f)
                
            self.stdout.write(self.style.SUCCESS(f'Successfully saved model to {out_file} (Size: {os.path.getsize(out_file)/1024/1024:.2f} MB)'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc())
