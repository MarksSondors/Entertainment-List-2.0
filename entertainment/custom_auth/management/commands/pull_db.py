import os
import subprocess
import tempfile

import requests
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Pull database dump from production server and restore locally'

    def add_arguments(self, parser):
        parser.add_argument('--url', type=str, help='Production server URL (overrides PROD_URL env var)')
        parser.add_argument('--api-key', type=str, help='Superuser API key (overrides PROD_API_KEY env var)')
        parser.add_argument('--skip-confirm', action='store_true', help='Skip confirmation prompt')

    def handle(self, *args, **options):
        url = options['url'] or config('PROD_URL', default='')
        api_key = options['api_key'] or config('PROD_API_KEY', default='')

        if not url or not api_key:
            raise CommandError(
                'Provide --url and --api-key, or set PROD_URL and PROD_API_KEY in your .env file.'
            )

        if not options['skip_confirm']:
            self.stdout.write(self.style.WARNING(
                '\n⚠️  This will DROP your local database and replace it with production data.\n'
            ))
            confirm = input('Are you sure? [y/N] ')
            if confirm.lower() != 'y':
                self.stdout.write('Aborted.')
                return

        # Download dump
        export_url = f'{url.rstrip("/")}/api/db-export/'
        self.stdout.write(f'Downloading database dump from {export_url}...')

        try:
            response = requests.get(
                export_url,
                headers={'Authorization': f'Bearer {api_key}'},
                stream=True,
                timeout=300,
            )
        except requests.ConnectionError:
            raise CommandError(f'Could not connect to {export_url}')

        if response.status_code != 200:
            raise CommandError(f'Failed to download: HTTP {response.status_code} - {response.text[:200]}')

        # Write to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.dump') as f:
            dump_path = f.name
            total = 0
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                total += len(chunk)
            self.stdout.write(f'Downloaded {total / 1024 / 1024:.1f} MB')

        try:
            db = settings.DATABASES['default']
            env = {**os.environ, 'PGPASSWORD': db['PASSWORD']}
            pg_args = ['-h', db['HOST'], '-p', str(db['PORT']), '-U', db['USER']]

            # Close Django's own database connections first
            self.stdout.write('Closing local Django DB connections...')
            from django.db import connections
            for conn_name in connections:
                connections[conn_name].close()

            # Terminate all other sessions connected to this database
            self.stdout.write('Terminating other database sessions...')
            subprocess.run(
                [
                    'psql', *pg_args, '-d', 'postgres', '-c',
                    f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    f"WHERE datname = '{db['NAME']}' AND pid <> pg_backend_pid();"
                ],
                env=env, check=True, capture_output=True,
            )

            # Drop and recreate
            self.stdout.write('Dropping and recreating local database...')
            subprocess.run(
                ['dropdb', *pg_args, '--if-exists', db['NAME']],
                env=env, check=True,
            )
            subprocess.run(
                ['createdb', *pg_args, db['NAME']],
                env=env, check=True,
            )

            # Restore
            self.stdout.write('Restoring database...')
            result = subprocess.run(
                ['pg_restore', *pg_args, '-d', db['NAME'], '--no-owner', '--no-acl', '-F', 'c', dump_path],
                env=env, capture_output=True, text=True,
            )

            if result.returncode != 0 and result.stderr:
                self.stdout.write(self.style.WARNING(f'pg_restore warnings:\n{result.stderr[:500]}'))

            self.stdout.write(self.style.SUCCESS('\n✅ Database restored successfully!'))

        finally:
            os.unlink(dump_path)
