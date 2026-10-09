#!/bin/sh
# Production entrypoint for the web container: migrate, collect static, register
# schedules, run the Django Q cluster under a restart loop, then exec gunicorn.
set -e

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

echo "Applying migrations..."
python manage.py migrate

echo "Collecting static files..."
python manage.py collectstatic --noinput

# Idempotent: only creates Django Q schedules that don't exist yet.
echo "Registering scheduled tasks..."
python manage.py update_movies --setup
python manage.py update_tvshows --setup

# Background tasks (imports, enrichment, schedules) need a running cluster. Run it
# in this container so it ships with every deploy, and restart it if it ever exits.
echo "Starting Django Q cluster..."
(
  set +e
  while true; do
    python manage.py qcluster
    echo "Django Q cluster exited with status $?; restarting in 5s..." >&2
    sleep 5
  done
) &

echo "Starting Gunicorn..."
exec gunicorn entertainment.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 3 \
    --threads 2 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --log-level info
