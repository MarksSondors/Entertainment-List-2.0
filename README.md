# Entertainment-List-2.0

A comprehensive entertainment tracking platform built with Django that allows users to discover, track, and review movies, TV shows, books, music albums, and games. Features advanced search capabilities, community features, personalized recommendations, network graph analysis, and seamless integration with multiple external APIs.

## Overview

Entertainment-List-2.0 is a modern Django 5.2+ web application that provides a complete entertainment management experience:

- **Multi-Media Tracking**: Comprehensive support for movies, TV shows, books, music albums, and games
- **Smart Discovery**: Advanced search and filtering with real-time external API integration, plus a unified cross-media explorer
- **Personal Management**: Customizable watchlists, reading lists, and personal collections
- **Social Features**: Community reviews, ratings, and movie-of-the-week discussions
- **Intelligent Recommendations**: Personalized content suggestions via a hybrid SVD/content-based recommender
- **Network Graph Analysis**: Community detection (Leiden algorithm) to visualize relationships between movies, actors, and directors
- **Notifications**: Web push notifications for background task and activity updates
- **Stremio Integration**: Companion addon for streaming discovery
- **Production Ready**: Fully containerized with Docker, production deployment with Traefik

## Key Features

### 🎬 **Movies & TV Shows**
- Real-time search via **The Movie Database (TMDB) API** and **TheTVDB API**
- Detailed information including cast, crew, genres, production companies
- Background task processing for data synchronization
- Episode tracking for TV series with season/episode progress
- Collection management and movie recommendations
- Community movie-of-the-week features
- Interactive network graph of movies, actors, and directors with automatic community naming

### 📚 **Books**
- Integration with **Hardcover API** for book discovery
- Author information with cross-media relationships
- Series tracking and reading progress
- Publisher and collection management
- Book recommendation system

### 🎵 **Music**
- **MusicBrainz API** integration for comprehensive music data
- Album and artist discovery with detailed metadata
- Soundtrack linking to movies and TV shows via IMDb ID matching
- Featured artist and collaboration tracking
- Music collection management

### 🎮 **Games**
- **RAWG API** integration for game discovery and metadata
- **SteamGridDB** integration for cover art/hero images
- Platform, genre, developer, and publisher tracking
- Game collection management and progress tracking

### 🔎 **Explorer**
- Unified search and browsing across all media types (movies, TV shows, books, music, games)
- Tree-based category browsing with shared filtering/ordering utilities

### 👥 **Community Features**
- User reviews and ratings (10-point scale)
- Movie-of-the-week community picks
- Recent activity feeds
- User profiles with statistics
- Discussion threads for community picks

### 🔧 **Advanced Features**
- **Background Processing**: Django Q2 for asynchronous tasks
- **Caching**: Redis-based caching for improved performance
- **Search**: PostgreSQL full-text search with trigram indexes
- **API Documentation**: Interactive Swagger/ReDoc documentation
- **Performance Monitoring**: Django Debug Toolbar and Silk profiling
- **PWA Support**: Progressive Web App capabilities with web push notifications
- **Real-time Updates**: Live task status monitoring

## Technology Stack

### **Backend Framework**
- **Django 5.2+** - Modern Python web framework
- **Django REST Framework** - Comprehensive API development
- **PostgreSQL** - Primary database with advanced search features
- **Redis** - Caching and session storage
- **Django Q2** - Background task processing

### **External Integrations**
- **TMDB API** - Movies and TV shows data
- **TheTVDB API** - Supplemental TV show data
- **MusicBrainz API** - Music and artist information
- **Hardcover API** - Books and literature data
- **RAWG API** - Games data
- **SteamGridDB API** - Game cover art and hero images

### **Development & Deployment**
- **Docker & Docker Compose** - Containerization
- **Traefik** - Reverse proxy with automatic HTTPS
- **Gunicorn** - WSGI HTTP Server for production
- **WhiteNoise** - Static file serving
- **PostgreSQL** - Production database

### **Frontend & UI**
- **Bootstrap 5** - Responsive UI framework
- **JavaScript (ES6+)** - Interactive frontend features
- **Progressive Web App** - Mobile-optimized experience
- **Responsive Design** - Works across all devices

### **Development Tools**
- **Django Debug Toolbar** - Development debugging
- **Silk** - Performance profiling
- **DRF Spectacular** - Automatic API documentation

## Installation & Setup

### Prerequisites
- **Docker** and **Docker Compose** (recommended)
- **Python 3.10+** (for local development)
- **PostgreSQL** (for production)
- **Redis** (for caching and background tasks)

### Environment Configuration

Create a `.env` file in the root directory with the following variables:

```env
# Django Settings
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database Configuration
DB_ENGINE=django.db.backends.postgresql
POSTGRES_DB=entertainment_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your-password
DB_HOST=postgres
DB_PORT=5432

# External API Keys
TMDB_BEARER_TOKEN=your-tmdb-bearer-token
TVDB_API_KEY=your-tvdb-api-key
HARDCOVER_API_TOKEN=your-hardcover-api-token
RAWG_API_KEY=your-rawg-api-key
STEAMGRIDDB_API_KEY=your-steamgriddb-api-key
APP_NAME=Entertainment-List-2.0
APP_VERSION=1.0.0
CONTACT_INFO=your-email@example.com

# Web Push Notifications
WEBPUSH_VAPID_PUBLIC_KEY=your-vapid-public-key
WEBPUSH_VAPID_PRIVATE_KEY=your-vapid-private-key
WEBPUSH_VAPID_ADMIN_EMAIL=your-email@example.com

# Security (Production)
CSRF_TRUSTED_ORIGINS=https://yourdomain.com
```

### Production Deployment with Docker (Recommended)

1. **Clone the repository:**
```bash
git clone https://github.com/yourusername/Entertainment-List-2.0.git
cd Entertainment-List-2.0
```

2. **Configure environment:**
```bash
cp .env.example .env
# Edit .env with your production settings
```

3. **Deploy with Docker Compose:**
```bash
# Production deployment (Traefik reverse proxy)
docker-compose up -d --build

# Development environment
docker-compose -f docker-compose.dev.yaml up -d --build
```

4. **Access the application:**
   - Development: http://localhost:8000
   - Production: https://yourdomain.com (with Traefik)

## Recommender System Setup

To enable the hybrid recommender system (SVD collaborative filtering + content-based):

1. **Download datasets:**
   - Download the [MovieLens 32M Dataset](https://grouplens.org/datasets/movielens/32m/) (or the Small dataset for development).
   - Extract `ratings.csv` and `links.csv` to `entertainment/data/ml-32m/`.
   - Download the [TMDB Movies Dataset](https://www.kaggle.com/datasets/asaniczka/tmdb-movies-dataset-2023-930k-movies) and place `TMDB_movie_dataset_v11.csv` in `entertainment/data/`. This provides content-based enrichment (ratings, runtime, language, status, genres) used to build movie feature vectors.

2. **Train the model:**
```bash
python manage.py train_recommender
```
   - This merges the external MovieLens data and TMDB catalog data with your local user reviews and trains an SVD model.
   - The model and mapping files are saved to `entertainment/movies/ml_models/svd_model.pkl`.
   - Run periodically (e.g., weekly) to update recommendations based on new user interactions.

3. **Usage:**
   - The dashboard automatically uses the model for personalized recommendations.
   - Use the `/movies/recommendations/external/` endpoint to get recommendations for movies *not* yet in your database (discovery mode).

### Local Development Setup

1. **Create virtual environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. **Install dependencies:**
```bash
cd entertainment
pip install -r requirements.txt
```

3. **Configure local database:**
```bash
# Set up PostgreSQL locally or use SQLite for development
export DB_ENGINE=django.db.backends.sqlite3
```

4. **Run migrations and start server:**
```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput

# Start background task processor
python manage.py qcluster &

# Start development server
python manage.py runserver
```

### Background Services

The application uses Django Q2 for background task processing:

- **Movie/TV data synchronization** from TMDB and TheTVDB
- **Music data processing** from MusicBrainz
- **Book data fetching** from Hardcover API
- **Game data fetching** from RAWG and SteamGridDB
- **Recommendation calculations**
- **Network graph rebuilding and community detection**
- **Web push notification delivery**
- **Search index updates**

## API Documentation

### Interactive Documentation
- **Swagger UI**: `/api/schema/swagger-ui/`
- **ReDoc**: `/api/schema/redoc/`
- **OpenAPI Schema**: `/api/schema/`

### Key Endpoints

#### Movies (`/movies/`)
- `GET /movies/search/` - Search TMDB for movies
- `POST /movies/` - Create movie from TMDB ID
- `GET /movies/popular/` - Get popular movies
- `POST /movies/watchlist/` - Add to watchlist
- `GET /movies/recommendations/` - Get personalized recommendations
- `GET /movies/recommendations/external/` - Discover recommendations outside your database
- `GET /movies/network-graph/` - Movie/actor/director network graph data

#### TV Shows (`/tvshows/`)
- `GET /tvshows/search/` - Search TMDB for TV shows
- `POST /tvshows/` - Create TV show from TMDB ID
- `GET /tvshows/popular/` - Get popular TV shows
- `POST /tvshows/watchlist/` - Add to watchlist

#### Music (`/music/`)
- `GET /music/search/` - Search MusicBrainz for music
- `POST /music/` - Create album from MusicBrainz ID

#### Books (`/books/`)
- `GET /books/search/` - Search Hardcover for books
- `POST /books/` - Create book from Hardcover ID

#### Games (`/games/`)
- `GET /games/search/` - Search RAWG for games
- `POST /games/` - Create game from RAWG ID

#### Explorer (`/explorer/`)
- Unified cross-media search and category tree browsing

#### Notifications (`/api/notifications/`)
- Web push subscription management and notification history

#### User Management (`/`)
- `GET /profile/` - User profile and statistics
- `GET /watchlist/` - User's watchlist items
- `POST /reviews/` - Create/update reviews
- `GET /activity/recent/` - Recent user activity

## Usage

### Adding Content

1. **Movies/TV Shows**: Search via TMDB/TheTVDB integration, add to database and watchlist
2. **Books**: Search via Hardcover API, track reading progress
3. **Music**: Search via MusicBrainz, automatic soundtrack linking to movies
4. **Games**: Search via RAWG, cover art fetched from SteamGridDB
5. **Reviews**: Rate and review any content with detailed comments

### Community Features

- **Movie of the Week**: Community voting and discussion
- **User Profiles**: View other users' activities and reviews
- **Recent Activity**: Track what the community is watching/reading

### Advanced Features

- **Smart Recommendations**: Hybrid SVD/content-based content suggestions
- **Network Graph**: Explore movie/actor/director relationships with automatic community detection and naming
- **Progress Tracking**: Episode progress for TV shows, reading progress for books
- **Collection Management**: Organize content into custom collections
- **External Linking**: Automatic linking between related content (soundtracks to movies)
- **Push Notifications**: Get notified of background task completions and activity

## Project Structure

```
Entertainment-List-2.0/
├── entertainment/              # Main Django project
│   ├── entertainment/         # Project settings
│   ├── custom_auth/          # User authentication and shared models
│   ├── movies/               # Movie management, recommender, network graph
│   │   └── services/
│   │       ├── network_graph/    # Graph builders, algorithms (Leiden), types
│   │       └── recommender/      # Hybrid SVD/content-based recommender
│   ├── tvshows/              # TV show management
│   ├── books/                # Book management
│   ├── music/                # Music management
│   ├── games/                # Game management (RAWG, SteamGridDB)
│   ├── notifications/        # Web push notifications
│   ├── explorer/             # Unified cross-media search/browsing
│   ├── stremio/              # Stremio addon integration
│   ├── api/                  # External API services
│   │   └── services/         # API service classes (TMDB, TVDB, MusicBrainz, Hardcover, RAWG, SteamGridDB)
│   ├── static/                # Static files
│   ├── templates/            # HTML templates
│   └── manage.py
├── nginx/                     # Nginx config (dev reverse proxy)
├── traefik/                   # Traefik config (production reverse proxy/HTTPS)
├── docker-compose.yaml         # Production docker setup (Traefik)
├── docker-compose.dev.yaml     # Development docker setup
└── README.md
```

## Contributing

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/amazing-feature`
3. **Make your changes** and add tests
4. **Run tests**: `python manage.py test`
5. **Commit your changes**: `git commit -m 'Add amazing feature'`
6. **Push to branch**: `git push origin feature/amazing-feature`
7. **Open a Pull Request**

### Development Guidelines

- Follow Django best practices
- Write tests for new features
- Update documentation as needed
- Use meaningful commit messages
- Ensure code is properly formatted

## Monitoring & Debugging

### Development Tools
- **Django Debug Toolbar**: Available at `/__debug__/`
- **Silk Profiling**: Available at `/silk/`
- **Admin Interface**: Available at `/admin/`

### Performance Monitoring
- Redis caching for frequently accessed data
- Database query optimization with select_related/prefetch_related
- Background task monitoring via Django Q2 admin
- Full-text search with PostgreSQL

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- **The Movie Database (TMDB)** for movie and TV show data
- **TheTVDB** for supplemental TV show data
- **MusicBrainz** for comprehensive music information
- **Hardcover** for book and literature data
- **RAWG** for game data
- **SteamGridDB** for game cover art
- **Django Community** for the excellent framework and packages