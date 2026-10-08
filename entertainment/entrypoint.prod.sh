#!/bin/sh
# Usage: entrypoint.prod.sh [web|worker]   (default: web)
#   web    - migrate, collect static, register schedules, then run gunicorn
#   worker - wait until migrations are applied, then run the Django Q cluster
set -e

ROLE="${1:-web}"

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

case "$ROLE" in
  worker)
    # The web container owns migrations; don't start processing tasks against a stale schema.
    echo "Waiting for migrations to be applied..."
    until python manage.py migrate --check >/dev/null 2>&1; do
      sleep 5
    done
    echo "Starting Django Q cluster..."
    exec python manage.py qcluster
    ;;
  web)
    echo "Applying migrations..."
    python manage.py migrate

    echo "Collecting static files..."
    python manage.py collectstatic --noinput

    # Idempotent: only creates Django Q schedules that don't exist yet.
    echo "Registering scheduled tasks..."
    python manage.py update_movies --setup
    python manage.py update_tvshows --setup

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
    ;;
  *)
    echo "Unknown role: $ROLE (expected 'web' or 'worker')" >&2
    exit 1
    ;;
esac
