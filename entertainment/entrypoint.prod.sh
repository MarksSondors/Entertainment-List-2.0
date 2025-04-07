#!/bin/sh

# Wait for Redis to be ready
echo "Waiting for Redis..."
until nc -z redis 6379; do
  sleep 1
done
echo "Redis is up - continuing..."

# Wait for Postgres
echo "Waiting for PostgreSQL..."
until nc -z postgres 5432; do
  sleep 1
done
echo "PostgreSQL is up - continuing..."

# Apply database migrations
echo "Applying migrations..."
python manage.py migrate

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Start Django Q cluster in the background
echo "Starting Django Q cluster..."
python manage.py qcluster &

echo "Creating tasks..."
python manage.py update_movies --setup

# Start Gunicorn
echo "Starting Gunicorn..."
gunicorn entertainment.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --threads 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --log-level info