#!/bin/sh
# Wait for Redis to be ready
echo "Waiting for Redis..."
until nc -z redis 6379; do
  sleep 1
done
echo "Redis is up - continuing..."

# Apply database migrations
echo "Applying migrations..."
python manage.py migrate

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Start Django Q cluster in the background
echo "Starting Django Q cluster..."
python manage.py qcluster &

# Start Django server
echo "Starting Django server..."
python manage.py runserver 0.0.0.0:8000