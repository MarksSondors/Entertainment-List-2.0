# Entertainment-List-2.0

A comprehensive entertainment tracking platform built with Django that allows users to discover, track, and review movies, TV shows, books, music albums, and games. Features advanced search capabilities, community features, personalized recommendations, and seamless integration with multiple external APIs.

## Overview

Entertainment-List-2.0 is a modern Django 5.2+ web application that provides a complete entertainment management experience:

- **Multi-Media Tracking**: Comprehensive support for movies, TV shows, books, music albums, and games
- **Smart Discovery**: Advanced search and filtering with real-time external API integration
- **Personal Management**: Customizable watchlists, reading lists, and personal collections
- **Social Features**: Community reviews, ratings, and movie-of-the-week discussions
- **Intelligent Recommendations**: Personalized content suggestions based on user preferences
- **Production Ready**: Fully containerized with Docker, production deployment with Traefik

## Key Features

### 🎬 **Movies & TV Shows**
- Real-time search via **The Movie Database (TMDB) API**
- Detailed information including cast, crew, genres, production companies
- Background task processing for data synchronization
- Episode tracking for TV series with season/episode progress
- Collection management and movie recommendations
- Community movie-of-the-week features

### 📚 **Books**
- Integration with **Hardcover API** for book discovery
- Author information with cross-media relationships
- Series tracking and reading progress
- Publisher and collection management
- Book recommendation system

### 🎵 **Music**
- **MusicBrainz API** integration for comprehensive music data
- Album and artist discovery with detailed metadata
- Soundtrack linking to movies and TV shows
- Featured artist and collaboration tracking
- Music collection management

### 🎮 **Games**
- Game tracking and collection management
- Platform and genre categorization
- Gaming statistics and progress tracking

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
- **PWA Support**: Progressive Web App capabilities
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
- **MusicBrainz API** - Music and artist information
- **Hardcover API** - Books and literature data

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
HARDCOVER_API_TOKEN=your-hardcover-api-token
APP_NAME=Entertainment-List-2.0
APP_VERSION=1.0.0
CONTACT_INFO=your-email@example.com

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
# Production deployment
docker-compose -f docker-compose.prod.yml up -d

# Development environment
docker-compose up -d
```

4. **Access the application:**
   - Development: http://localhost:8000
   - Production: https://yourdomain.com (with Traefik)

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

- **Movie/TV data synchronization** from TMDB
- **Music data processing** from MusicBrainz
- **Book data fetching** from Hardcover API
- **Recommendation calculations**
- **Search index updates**

## API Documentation

### Interactive Documentation
- **Swagger UI**: `/api/schema/swagger/`
- **ReDoc**: `/api/schema/redoc/`
- **OpenAPI Schema**: `/api/schema/`

### Key API Endpoints

#### Movies
- `GET /movies/search/` - Search TMDB for movies
- `POST /movies/` - Create movie from TMDB ID
- `GET /movies/popular/` - Get popular movies
- `POST /movies/watchlist/` - Add to watchlist
- `GET /movies/recommendations/` - Get personalized recommendations

#### TV Shows
- `GET /tvshows/search/` - Search TMDB for TV shows
- `POST /tvshows/` - Create TV show from TMDB ID
- `GET /tvshows/popular/` - Get popular TV shows
- `POST /tvshows/watchlist/` - Add to watchlist

#### Music
- `GET /music/search/` - Search MusicBrainz for music
- `POST /music/albums/` - Create album from MusicBrainz ID
- `GET /music/albums/{id}/` - Get album details

#### Books
- `GET /books/search/` - Search Hardcover for books
- `POST /books/` - Create book from Hardcover ID

#### User Management
- `GET /profile/` - User profile and statistics
- `GET /watchlist/` - User's watchlist items
- `POST /reviews/` - Create/update reviews
- `GET /activity/recent/` - Recent user activity

## Usage

### Adding Content

1. **Movies/TV Shows**: Search via TMDB integration, add to database and watchlist
2. **Books**: Search via Hardcover API, track reading progress
3. **Music**: Search via MusicBrainz, automatic soundtrack linking to movies
4. **Reviews**: Rate and review any content with detailed comments

### Community Features

- **Movie of the Week**: Community voting and discussion
- **User Profiles**: View other users' activities and reviews
- **Recent Activity**: Track what the community is watching/reading

### Advanced Features

- **Smart Recommendations**: AI-powered content suggestions
- **Progress Tracking**: Episode progress for TV shows, reading progress for books
- **Collection Management**: Organize content into custom collections
- **External Linking**: Automatic linking between related content (soundtracks to movies)

## Project Structure

```
Entertainment-List-2.0/
├── entertainment/              # Main Django project
│   ├── entertainment/         # Project settings
│   ├── custom_auth/          # User authentication and shared models
│   ├── movies/               # Movie management
│   ├── tvshows/             # TV show management
│   ├── books/               # Book management
│   ├── music/               # Music management
│   ├── games/               # Game management (placeholder)
│   ├── api/                 # External API services
│   │   └── services/        # API service classes
│   ├── static/              # Static files
│   ├── templates/           # HTML templates
│   └── manage.py
├── docker-compose.yml        # Development docker setup
├── docker-compose.prod.yml   # Production docker setup
├── traefik.yml              # Traefik configuration
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
- **MusicBrainz** for comprehensive music information
- **Hardcover** for book and literature data
- **Django Community** for the excellent framework and packages
- **Contributors** who have helped improve this project