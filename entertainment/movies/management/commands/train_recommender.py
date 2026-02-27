import os
import psutil
import pickle
import pandas as pd
import numpy as np
import gc
from scipy.sparse import coo_matrix
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

    def load_tmdb_catalog(self):
        """
        Loads the TMDB movie catalog CSV for enrichment data.
        Provides: vote_average, vote_count, runtime, budget, revenue,
        original_language, status — used for new bias layers and cold-start priors.
        """
        self.log_memory("Start TMDB Catalog Load")
        catalog_file = settings.BASE_DIR / 'data' / 'TMDB_movie_dataset_v11.csv'

        if not catalog_file.exists():
            self.stdout.write(self.style.WARNING(
                f'TMDB catalog not found at {catalog_file}. Skipping enrichment.'))
            return None

        catalog = pd.read_csv(
            catalog_file,
            usecols=['id', 'vote_average', 'vote_count', 'runtime',
                     'budget', 'revenue', 'original_language', 'status'],
            dtype={'id': 'float64'},  # Handle NaN IDs gracefully
        )
        catalog = catalog.dropna(subset=['id'])
        catalog['id'] = catalog['id'].astype('int32')
        catalog = catalog.rename(columns={'id': 'tmdbId'})

        # Coerce numeric columns
        for col in ['vote_average', 'vote_count', 'runtime', 'budget', 'revenue']:
            catalog[col] = pd.to_numeric(catalog[col], errors='coerce').fillna(0)

        catalog['vote_count'] = catalog['vote_count'].astype('int32')
        catalog['runtime'] = catalog['runtime'].astype('float32')
        catalog['vote_average'] = catalog['vote_average'].astype('float32')
        catalog['original_language'] = catalog['original_language'].fillna('en').astype(str)
        catalog['status'] = catalog['status'].fillna('Unknown').astype(str)

        # Compute runtime buckets
        conditions = [
            catalog['runtime'] <= 0,
            catalog['runtime'] <= 90,
            catalog['runtime'] <= 120,
            catalog['runtime'] <= 150,
        ]
        choices = ['standard', 'short', 'standard', 'long']
        catalog['runtime_bucket'] = np.select(conditions, choices, default='epic')

        released_count = (catalog['status'] == 'Released').sum()
        self.stdout.write(f"TMDB catalog: {len(catalog)} total, {released_count} Released")
        self.log_memory("TMDB Catalog Loaded")
        return catalog

    def load_data(self):
        """Loads Ratings and merges with Metadata + TMDB catalog enrichment"""
        meta_df, tmdb_to_genres = self.load_metadata_and_genres()
        if meta_df is None: return None, None, None, None, None

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

        # --- TMDB Catalog Enrichment ---
        tmdb_catalog = self.load_tmdb_catalog()
        tmdb_vote_data = {}
        catalog_language = {}
        catalog_runtime_bucket = {}

        if tmdb_catalog is not None:
            # Filter training data to Released movies only
            released_ids = set(tmdb_catalog.loc[tmdb_catalog['status'] == 'Released', 'tmdbId'])
            before_count = len(df)
            df = df[df['tmdbId'].isin(released_ids)]
            self.stdout.write(f"Status filter: {before_count} -> {len(df)} ratings (removed non-Released)")

            # Merge enrichment columns
            enrich_cols = tmdb_catalog[['tmdbId', 'runtime_bucket', 'original_language']].drop_duplicates('tmdbId')
            df = pd.merge(df, enrich_cols, on='tmdbId', how='left')
            df['runtime_bucket'] = df['runtime_bucket'].fillna('standard')
            df['original_language'] = df['original_language'].fillna('en')

            # Build lookup dicts for cold-start and prediction
            for row in tmdb_catalog.itertuples(index=False):
                tmdb_vote_data[row.tmdbId] = (float(row.vote_average), int(row.vote_count))
                catalog_language[row.tmdbId] = str(row.original_language)
                catalog_runtime_bucket[row.tmdbId] = str(row.runtime_bucket)

            del tmdb_catalog
            gc.collect()
        else:
            df['runtime_bucket'] = 'standard'
            df['original_language'] = 'en'

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
        
        # Build TMDB genre lookup from local DB, converting TMDB names to MovieLens names
        # This lets local reviews contribute to and benefit from genre bias learning
        local_genre_map = {}  # tmdb_id -> pipe-delimited ML genre string
        for movie in Movie.objects.exclude(tmdb_id__isnull=True).prefetch_related('genres'):
            ml_genres = []
            for g in movie.genres.all():
                ml_name = self.TMDB_TO_MOVIELENS.get(g.name)
                if ml_name:
                    ml_genres.append(ml_name)
            if ml_genres:
                local_genre_map[movie.tmdb_id] = '|'.join(ml_genres)
                # Backfill tmdb_to_genres for movies not in MovieLens (post-2023)
                if movie.tmdb_id not in tmdb_to_genres or not tmdb_to_genres[movie.tmdb_id]:
                    tmdb_to_genres[movie.tmdb_id] = ml_genres

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
                    'genres': local_genre_map.get(tmdb_id, ''),
                    'runtime_bucket': catalog_runtime_bucket.get(tmdb_id, 'standard'),
                    'original_language': catalog_language.get(tmdb_id, 'en'),
                })
        
        if local_data:
            local_df = pd.DataFrame(local_data)
            
            # Upweight local reviews: ML has ~32M ratings drowning out local users.
            # Boost local rows so each local user has comparable influence to an avg ML user.
            ml_user_count = df['userId'].nunique()
            ml_avg_ratings_per_user = len(df) / max(ml_user_count, 1)
            local_avg_ratings = len(local_df) / max(local_df['userId'].nunique(), 1)
            boost_factor = max(1, min(int(ml_avg_ratings_per_user / max(local_avg_ratings, 1)), 10))
            
            if boost_factor > 1:
                self.stdout.write(f"Boosting local reviews {boost_factor}x to match ML user density")
                local_df = pd.concat([local_df] * boost_factor, ignore_index=True)
            
            self.stdout.write(f"Including {len(local_df)} local reviews "
                           f"({local_df['userId'].nunique()} users, {boost_factor}x boost).")
            df = pd.concat([df, local_df], ignore_index=True)
            del local_df

        self.log_memory("Data Merged")
        return df, tmdb_to_genres, tmdb_vote_data, catalog_language, catalog_runtime_bucket

    # Standard MovieLens genre vocabulary
    GENRES_LIST = [
        "Action", "Adventure", "Animation", "Children's", "Comedy", "Crime",
        "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "Musical",
        "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western"
    ]

    RUNTIME_BUCKETS = ['short', 'standard', 'long', 'epic']

    def _apply_time_decay(self, df, half_life_days=365 * 3):
        """
        Compute exponential time-decay weights so recent ratings matter more.
        Half-life of 3 years: a rating from 3 years ago has half the weight of today's.
        Floor at 0.1 so very old ratings don't vanish completely.
        """
        max_ts = df['timestamp'].max()
        seconds_per_day = 86400
        half_life_seconds = half_life_days * seconds_per_day

        decay = np.exp(-np.log(2) * (max_ts - df['timestamp'].values) / half_life_seconds)
        decay = np.clip(decay, 0.1, 1.0).astype(np.float32)
        return decay

    def _extrapolate_year_biases(self, year_biases, max_future_year=2030):
        """
        Extrapolate year biases for years beyond the training data (post-2023).
        Uses the average bias of the last 5 known years as a stable forward estimate.
        """
        known_years = sorted(y for y in year_biases.keys() if y >= 1950)
        if not known_years:
            return year_biases

        max_known = max(known_years)
        recent_years = [y for y in known_years if y >= max_known - 4]
        if recent_years:
            recent_avg = float(np.mean([year_biases[y] for y in recent_years]))
        else:
            recent_avg = 0.0

        for y in range(max_known + 1, max_future_year + 1):
            year_biases[y] = recent_avg

        self.stdout.write(f"Extrapolated year biases {max_known + 1}-{max_future_year} "
                         f"(avg bias: {recent_avg:.4f})")
        return year_biases

    def _build_genre_masks(self, df):
        """
        Pre-build boolean mask arrays for each genre. Calls str.contains() once per genre
        upfront so that compute_biases() and create_sparse_matrix() can reuse them
        without re-scanning the genres column on every iteration.
        """
        self.stdout.write("Pre-building genre masks...")
        df_genres = df['genres'].astype(str)
        genre_masks = {}
        for genre in self.GENRES_LIST:
            mask = df_genres.str.contains(genre, regex=False).values
            if mask.any():
                genre_masks[genre] = mask
        return genre_masks

    def _build_decade_masks(self, df):
        """Pre-build boolean mask arrays for each decade present in the data."""
        decade_masks = {}
        for decade in df['decade'].unique():
            mask = (df['decade'] == decade).values
            if mask.any():
                decade_masks[int(decade)] = mask
        return decade_masks

    def _build_language_masks(self, df):
        """Pre-build boolean mask arrays for languages with meaningful data (≥100 ratings)."""
        lang_counts = df['original_language'].value_counts()
        significant_langs = lang_counts[lang_counts >= 100].index
        language_masks = {}
        for lang in significant_langs:
            mask = (df['original_language'] == lang).values
            if mask.any():
                language_masks[str(lang)] = mask
        return language_masks

    def _build_runtime_masks(self, df):
        """Pre-build boolean mask arrays for each runtime bucket."""
        runtime_masks = {}
        for bucket in self.RUNTIME_BUCKETS:
            mask = (df['runtime_bucket'] == bucket).values
            if mask.any():
                runtime_masks[bucket] = mask
        return runtime_masks

    def compute_biases(self, df, damping=5):
        """
        Compute Global, Year, Item, User, User-Genre, and User-Decade biases.
        Hierarchy: Global -> Year -> Item -> User -> User-Genre -> User-Decade

        Uses time-decay weighting so recent ratings have more influence.
        Genre/decade biases use iterative convergence (3 passes) to remove
        order-dependency in the single-pass approach.
        """
        self.stdout.write('Computing biases...')

        # Time-decay weights: recent ratings matter more
        weights = self._apply_time_decay(df)
        df_rating_weighted = (df['rating'] * weights).astype('float32')

        # Pre-build masks for vectorized genre/decade/language/runtime operations
        genre_masks = self._build_genre_masks(df)
        decade_masks = self._build_decade_masks(df)
        language_masks = self._build_language_masks(df)
        runtime_masks = self._build_runtime_masks(df)

        # 1. Global Mean (weighted)
        global_mean = float(np.average(df['rating'], weights=weights))

        # 2. Year Biases (weighted)
        df_year_resid = (df_rating_weighted - weights * global_mean).astype('float32')
        year_sum = df_year_resid.groupby(df['year']).sum()
        year_weight = pd.Series(weights, index=df.index).groupby(df['year']).sum()
        year_biases = (year_sum / (year_weight + damping)).to_dict()

        # Extrapolate for years beyond training data (2024+)
        year_biases = self._extrapolate_year_biases(year_biases)

        del df_year_resid, year_sum, year_weight

        # 3. Item Biases (weighted)
        y_bias_series = df['year'].map(year_biases).fillna(0).astype('float32')
        item_resid = (df_rating_weighted - weights * (global_mean + y_bias_series)).astype('float32')

        item_sum = item_resid.groupby(df['tmdbId']).sum()
        item_weight = pd.Series(weights, index=df.index).groupby(df['tmdbId']).sum()
        item_biases = (item_sum / (item_weight + damping)).to_dict()

        del item_resid, item_sum, item_weight

        # 4. User Biases (weighted)
        i_bias_series = df['tmdbId'].map(item_biases).fillna(0).astype('float32')
        user_resid = (df_rating_weighted - weights * (global_mean + y_bias_series + i_bias_series)).astype('float32')

        user_sum = user_resid.groupby(df['userId']).sum()
        user_weight = pd.Series(weights, index=df.index).groupby(df['userId']).sum()
        user_biases = (user_sum / (user_weight + damping)).to_dict()

        del y_bias_series, i_bias_series, user_resid, user_sum, user_weight
        del df_rating_weighted
        gc.collect()

        # 5 & 6. User-Genre and User-Decade Biases with Iterative Convergence
        # Instead of a single pass where Action is always computed before Western,
        # we run 3 iterations. Each iteration recomputes residuals from scratch
        # using ALL previous-iteration biases, then re-estimates genre and decade biases.
        self.stdout.write("Computing User-Genre, Decade, Language & Runtime Biases (iterative convergence)...")

        user_genre_biases = {}   # { Genre: { UserId: Bias } }
        user_decade_biases = {}  # { Decade: { UserId: Bias } }
        user_language_biases = {}  # { Language: { UserId: Bias } }
        user_runtime_biases = {}   # { RuntimeBucket: { UserId: Bias } }

        genre_damping = damping * 2
        decade_damping = damping * 2
        language_damping = damping * 2
        runtime_damping = damping * 2
        n_iterations = 3

        # Pre-compute the base residual (rating - global - year - item - user) once
        base_residual = (
            df['rating']
            - global_mean
            - df['year'].map(year_biases).fillna(0).astype('float32')
            - df['tmdbId'].map(item_biases).fillna(0).astype('float32')
            - df['userId'].map(user_biases).fillna(0).astype('float32')
        ).astype('float32').values

        for iteration in range(n_iterations):
            self.stdout.write(f"  Bias convergence iteration {iteration + 1}/{n_iterations}")

            # Start from base residual each iteration
            residual = base_residual.copy()

            # Subtract ALL previous-iteration genre biases
            for genre, bias_map in user_genre_biases.items():
                if genre in genre_masks:
                    mask = genre_masks[genre]
                    mapped = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                    residual[mask] -= mapped

            # Subtract ALL previous-iteration decade biases
            for decade, bias_map in user_decade_biases.items():
                if decade in decade_masks:
                    mask = decade_masks[decade]
                    mapped = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                    residual[mask] -= mapped

            # Subtract ALL previous-iteration language biases
            for lang, bias_map in user_language_biases.items():
                if lang in language_masks:
                    mask = language_masks[lang]
                    mapped = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                    residual[mask] -= mapped

            # Subtract ALL previous-iteration runtime biases
            for bucket, bias_map in user_runtime_biases.items():
                if bucket in runtime_masks:
                    mask = runtime_masks[bucket]
                    mapped = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                    residual[mask] -= mapped

            # Re-estimate genre biases from clean residuals
            new_genre_biases = {}
            weighted_residual = residual * weights
            for genre, mask in genre_masks.items():
                subset_user = df.loc[mask, 'userId']
                wr = pd.Series(weighted_residual[mask], index=subset_user.index)
                w = pd.Series(weights[mask], index=subset_user.index)

                genre_sum = wr.groupby(subset_user).sum()
                genre_wt = w.groupby(subset_user).sum()
                bias = (genre_sum / (genre_wt + genre_damping))
                new_genre_biases[genre] = bias.to_dict()

            # Re-estimate decade biases from residuals (minus new genre biases)
            residual_after_genre = residual.copy()
            for genre, bias_map in new_genre_biases.items():
                if genre in genre_masks:
                    mask = genre_masks[genre]
                    mapped = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                    residual_after_genre[mask] -= mapped

            new_decade_biases = {}
            weighted_residual_ag = residual_after_genre * weights
            for decade, mask in decade_masks.items():
                subset_user = df.loc[mask, 'userId']
                wr = pd.Series(weighted_residual_ag[mask], index=subset_user.index)
                w = pd.Series(weights[mask], index=subset_user.index)

                decade_sum = wr.groupby(subset_user).sum()
                decade_wt = w.groupby(subset_user).sum()
                bias = (decade_sum / (decade_wt + decade_damping))
                new_decade_biases[decade] = bias.to_dict()

            # Re-estimate language biases (residual - genre - decade)
            residual_after_decade = residual_after_genre.copy()
            for decade, bias_map in new_decade_biases.items():
                if decade in decade_masks:
                    mask = decade_masks[decade]
                    mapped = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                    residual_after_decade[mask] -= mapped

            new_language_biases = {}
            weighted_residual_ad = residual_after_decade * weights
            for lang, mask in language_masks.items():
                subset_user = df.loc[mask, 'userId']
                wr = pd.Series(weighted_residual_ad[mask], index=subset_user.index)
                w = pd.Series(weights[mask], index=subset_user.index)

                lang_sum = wr.groupby(subset_user).sum()
                lang_wt = w.groupby(subset_user).sum()
                bias = (lang_sum / (lang_wt + language_damping))
                new_language_biases[lang] = bias.to_dict()

            # Re-estimate runtime biases (residual - genre - decade - language)
            residual_after_lang = residual_after_decade.copy()
            for lang, bias_map in new_language_biases.items():
                if lang in language_masks:
                    mask = language_masks[lang]
                    mapped = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                    residual_after_lang[mask] -= mapped

            new_runtime_biases = {}
            weighted_residual_al = residual_after_lang * weights
            for bucket, mask in runtime_masks.items():
                subset_user = df.loc[mask, 'userId']
                wr = pd.Series(weighted_residual_al[mask], index=subset_user.index)
                w = pd.Series(weights[mask], index=subset_user.index)

                rt_sum = wr.groupby(subset_user).sum()
                rt_wt = w.groupby(subset_user).sum()
                bias = (rt_sum / (rt_wt + runtime_damping))
                new_runtime_biases[bucket] = bias.to_dict()

            user_genre_biases = new_genre_biases
            user_decade_biases = new_decade_biases
            user_language_biases = new_language_biases
            user_runtime_biases = new_runtime_biases

            del residual, weighted_residual, residual_after_genre, weighted_residual_ag
            del residual_after_decade, weighted_residual_ad
            del residual_after_lang, weighted_residual_al
            gc.collect()

        del base_residual
        gc.collect()

        self.log_memory("Biases Computed")
        return (global_mean, year_biases, item_biases, user_biases,
                user_genre_biases, user_decade_biases,
                user_language_biases, user_runtime_biases)

    def create_sparse_matrix(self, df, global_mean, year_biases, item_biases, user_biases,
                             user_genre_biases, user_decade_biases,
                             user_language_biases, user_runtime_biases):
        """Creates residuals matrix R for SVD, using pre-built masks for genre/decade/language/runtime."""
        self.stdout.write('Preparing residuals for SVD...')

        # Pre-build masks once for this method
        genre_masks = self._build_genre_masks(df)
        decade_masks = self._build_decade_masks(df)
        language_masks = self._build_language_masks(df)
        runtime_masks = self._build_runtime_masks(df)

        # Base Residual
        y_bias = df['year'].map(year_biases).fillna(0).astype('float32')
        i_bias = df['tmdbId'].map(item_biases).fillna(0).astype('float32')
        u_bias = df['userId'].map(user_biases).fillna(0).astype('float32')

        # Decade Bias (vectorized via pre-built masks)
        d_bias_total = np.zeros(len(df), dtype=np.float32)
        for decade, bias_map in user_decade_biases.items():
            if decade in decade_masks:
                mask = decade_masks[decade]
                b = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                d_bias_total[mask] = b

        # Genre Bias (vectorized via pre-built masks)
        g_bias_total = np.zeros(len(df), dtype=np.float32)
        for genre, bias_map in user_genre_biases.items():
            if genre in genre_masks:
                mask = genre_masks[genre]
                b = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                g_bias_total[mask] += b

        # Language Bias (vectorized via pre-built masks)
        l_bias_total = np.zeros(len(df), dtype=np.float32)
        for lang, bias_map in user_language_biases.items():
            if lang in language_masks:
                mask = language_masks[lang]
                b = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                l_bias_total[mask] = b

        # Runtime Bias (vectorized via pre-built masks)
        r_bias_total = np.zeros(len(df), dtype=np.float32)
        for bucket, bias_map in user_runtime_biases.items():
            if bucket in runtime_masks:
                mask = runtime_masks[bucket]
                b = df.loc[mask, 'userId'].map(bias_map).fillna(0).astype('float32').values
                r_bias_total[mask] = b

        residuals = (df['rating'].values - global_mean - y_bias.values - i_bias.values
                     - u_bias.values - d_bias_total - g_bias_total - l_bias_total - r_bias_total)

        # Clamp residuals to prevent SVD distortion from extreme outliers
        residuals = np.clip(residuals, -3.0, 3.0).astype(np.float32)
        
        del y_bias, i_bias, u_bias, g_bias_total, d_bias_total, l_bias_total, r_bias_total
        del genre_masks, decade_masks, language_masks, runtime_masks
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
        """Evaluates RMSE and MAE with full bias hierarchy, with per-group breakdown."""
        # We process train copy
        (global_mean, year_biases, item_biases, user_biases,
         user_genre_biases, user_decade_biases,
         user_language_biases, user_runtime_biases) = self.compute_biases(df_train, damping)
        
        try:
            R_sparse, u_map, i_map = self.create_sparse_matrix(
                df_train, global_mean, year_biases, item_biases, user_biases,
                user_genre_biases, user_decade_biases,
                user_language_biases, user_runtime_biases)
            
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

        # Language Predictions
        if 'original_language' in test_df.columns:
            for lang, bias_map in user_language_biases.items():
                mask = (test_df['original_language'] == lang)
                if mask.any():
                    pred[mask] += test_df.loc[mask, 'userId'].map(bias_map).fillna(0).values.astype(np.float32)

        # Runtime Predictions
        if 'runtime_bucket' in test_df.columns:
            for bucket, bias_map in user_runtime_biases.items():
                mask = (test_df['runtime_bucket'] == bucket)
                if mask.any():
                    pred[mask] += test_df.loc[mask, 'userId'].map(bias_map).fillna(0).values.astype(np.float32)

        # SVD Interaction
        u_idx = test_df['userId'].map(u_map).values
        i_idx = test_df['tmdbId'].map(i_map).values
        
        u_vecs = np.dot(u[u_idx], sigma)
        vt_vecs = vt[:, i_idx]
        
        interaction = np.sum(u_vecs * vt_vecs.T, axis=1)
        pred += interaction
        
        # Overall metrics
        errors = pred - test_df['rating'].values
        rmse = float(np.sqrt(np.mean(errors ** 2)))
        mae = float(np.mean(np.abs(errors)))
        self.stdout.write(f"  Overall: RMSE={rmse:.4f}, MAE={mae:.4f} ({len(test_df)} samples)")

        # Per-group evaluation: loc_ (your users) vs ml_ (MovieLens)
        user_ids = test_df['userId'].values
        for prefix, label in [('loc_', 'Local'), ('ml_', 'MovieLens')]:
            group_mask = np.array([uid.startswith(prefix) for uid in user_ids])
            if group_mask.any():
                group_errors = errors[group_mask]
                g_rmse = float(np.sqrt(np.mean(group_errors ** 2)))
                g_mae = float(np.mean(np.abs(group_errors)))
                self.stdout.write(f"  {label}: RMSE={g_rmse:.4f}, MAE={g_mae:.4f} ({group_mask.sum()} samples)")

        return rmse

    def handle(self, *args, **options):
        try:
            df, tmdb_to_genres, tmdb_vote_data, catalog_language, catalog_runtime_bucket = self.load_data()
            if df is None: return

            best_k, best_damping = 120, 15
            
            if options['optimize']:
                try:
                    import optuna
                    optuna.logging.set_verbosity(optuna.logging.WARNING)
                    self.stdout.write("Starting Optuna optimization...")
                    
                    # Temporal split: train on older ratings, validate on newer ones.
                    # This prevents data leakage (training on future to predict past)
                    # and better simulates real-world usage.
                    cutoff = df['timestamp'].quantile(0.8)
                    train_df = df[df['timestamp'] <= cutoff].copy()
                    val_df = df[df['timestamp'] > cutoff].copy()
                    self.stdout.write(f"Temporal split: {len(train_df)} train, {len(val_df)} validation")
                    
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
                    
                    del train_df, val_df, study
                    gc.collect()
                    
                except ImportError:
                    self.stdout.write(self.style.WARNING("Optuna not installed. Skipping optimization."))

            self.stdout.write(f"Training Final Model (k={best_k})...")
            
            # Full Train
            (global_mean, year_biases, item_biases, user_biases,
             user_genre_biases, user_decade_biases,
             user_language_biases, user_runtime_biases) = self.compute_biases(df, best_damping)
            R_sparse, u_map, i_map = self.create_sparse_matrix(
                df, global_mean, year_biases, item_biases, user_biases,
                user_genre_biases, user_decade_biases,
                user_language_biases, user_runtime_biases)
            
            u, s, vt = svds(R_sparse, k=best_k)
            # ... process matrices ...
            u = u[:, ::-1].astype(np.float32)
            s = s[::-1].astype(np.float32)
            vt = vt[::-1, :].astype(np.float32)
            sigma = np.diag(s)

            # Enrich tmdb_to_genres with local DB movies not in MovieLens (post-2023)
            self.stdout.write("Enriching genre map with local DB movies...")
            enriched_count = 0
            for movie in Movie.objects.exclude(tmdb_id__isnull=True).prefetch_related('genres'):
                if movie.tmdb_id not in tmdb_to_genres or not tmdb_to_genres[movie.tmdb_id]:
                    ml_genres = []
                    for g in movie.genres.all():
                        ml_name = self.TMDB_TO_MOVIELENS.get(g.name)
                        if ml_name:
                            ml_genres.append(ml_name)
                    if ml_genres:
                        tmdb_to_genres[movie.tmdb_id] = ml_genres
                        enriched_count += 1
            self.stdout.write(f"Added {enriched_count} local movies to genre map.")

            # Export
            from datetime import datetime
            import shutil

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
                'user_language_biases': user_language_biases,
                'user_runtime_biases': user_runtime_biases,
                'tmdb_to_genres': tmdb_to_genres,
                'tmdb_to_language': catalog_language,
                'tmdb_to_runtime_bucket': catalog_runtime_bucket,
                'tmdb_vote_data': tmdb_vote_data,
                'genre_mapping': self.TMDB_TO_MOVIELENS,
                'tmdb_id_to_year': df[['tmdbId', 'year']].drop_duplicates('tmdbId').set_index('tmdbId')['year'].to_dict(),
                'metadata': {
                    'trained_at': datetime.now().isoformat(),
                    'k': best_k,
                    'damping': best_damping,
                    'n_users': len(u_map),
                    'n_items': len(i_map),
                    'n_ratings': len(df),
                    'n_local_users': sum(1 for uid in u_map if uid.startswith('loc_')),
                    'n_ml_users': sum(1 for uid in u_map if uid.startswith('ml_')),
                    'model_version': '3.0',
                },
            }
            
            model_dir = settings.BASE_DIR / 'movies' / 'ml_models'
            os.makedirs(model_dir, exist_ok=True)

            # Save versioned copy with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            versioned_file = model_dir / f'svd_model_{timestamp}.pkl'
            with open(versioned_file, 'wb') as f:
                pickle.dump(data, f)

            # Copy as the latest model (what recommendation.py loads)
            latest_file = model_dir / 'svd_model_latest.pkl'
            shutil.copy2(versioned_file, latest_file)

            # Also keep backward-compatible name
            compat_file = model_dir / 'svd_model.pkl'
            shutil.copy2(versioned_file, compat_file)
                
            size_mb = os.path.getsize(versioned_file) / 1024 / 1024
            self.stdout.write(self.style.SUCCESS(
                f'Model saved: {versioned_file.name} ({size_mb:.2f} MB)\n'
                f'  Users: {data["metadata"]["n_local_users"]} local, '
                f'{data["metadata"]["n_ml_users"]} ML | '
                f'Items: {data["metadata"]["n_items"]} | '
                f'k={best_k}, damping={best_damping}'
            ))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            import traceback
            self.stdout.write(traceback.format_exc())
