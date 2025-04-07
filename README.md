# Entertainment-List-2.0

A comprehensive web application for tracking and reviewing various entertainment media including movies, TV shows, books, music, and games.

## Overview

Entertainment-List-2.0 is a Django-based platform that allows users to:
- Discover new entertainment content
- Track personal collections and watchlists
- Rate and review different media types
- Share opinions with the community

## Features

- **Multiple Media Types**: Support for movies, TV shows, books, music albums, and games
- **User Authentication**: Custom authentication system for personalized experiences
- **Review System**: Rate and review content with a 10-point scale
- **Responsive Design**: Works on desktop and mobile devices
- **API Support**: RESTful API for integrating with other services

## Technologies Used

- **Backend**: Django (Python)
- **Database**: PostgreSQL
- **Frontend**: HTML, CSS, JavaScript
- **Containerization**: Docker and Docker Compose
- **Reverse Proxy**: Traefik with automatic HTTPS
- **External APIs**: Integration with entertainment databases (MusicBrainz, etc.)

## Installation & Setup

### Prerequisites
- Docker and Docker Compose
- Python 3.x (for local development)

### Using Docker (Recommended)
1. Clone the repository:
```bash
git clone https://github.com/yourusername/Entertainment-List-2.0.git
cd Entertainment-List-2.0
```

2. Set up environment variables:
```bash
cp .env.example .env
```
Edit the `.env` file with your specific configuration.

3. Start the application:
```bash
docker-compose up -d
```

4. Access the application at http://localhost:8000

### Local Development
1. Set up a virtual environment:
```bash
cd entertainment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run migrations:
```bash
python manage.py migrate
```

4. Start the development server:
```bash
python manage.py runserver
```

## Usage

After installation, create an account to start tracking your favorite movies, TV shows, books, music, and games. You can:

- Add items to your collection
- Rate content on a scale from 1-10
- Write detailed reviews
- Browse community reviews

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the `LICENSE` file for details.