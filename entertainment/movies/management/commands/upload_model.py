import os
import requests
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Upload a trained SVD model (.pkl) to a remote Entertainment-List server'

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            type=str,
            required=True,
            help='Full URL of the upload endpoint, e.g. https://yoursite.com/movies/upload-model/',
        )
        parser.add_argument(
            '--key',
            type=str,
            default=None,
            help='Upload key (Bearer token). Falls back to MODEL_UPLOAD_KEY in .env',
        )
        parser.add_argument(
            '--file',
            type=str,
            default=None,
            help='Path to .pkl file. Defaults to ml_models/svd_model_latest.pkl',
        )

    def handle(self, *args, **options):
        # Resolve the model file
        model_path = options['file']
        if not model_path:
            model_path = os.path.join(
                settings.BASE_DIR, 'movies', 'ml_models', 'svd_model_latest.pkl'
            )

        if not os.path.exists(model_path):
            self.stderr.write(self.style.ERROR(f'Model file not found: {model_path}'))
            return

        file_size_mb = os.path.getsize(model_path) / (1024 * 1024)
        self.stdout.write(f'Model file: {model_path} ({file_size_mb:.1f} MB)')

        # Resolve the upload key
        upload_key = options['key'] or getattr(settings, 'MODEL_UPLOAD_KEY', '')
        if not upload_key:
            self.stderr.write(self.style.ERROR(
                'No upload key provided. Use --key or set MODEL_UPLOAD_KEY in .env'
            ))
            return

        url = options['url'].rstrip('/')
        if not url.endswith('/'):
            url += '/'

        self.stdout.write(f'Uploading to {url} ...')

        try:
            with open(model_path, 'rb') as f:
                response = requests.post(
                    url,
                    files={'model': (os.path.basename(model_path), f, 'application/octet-stream')},
                    headers={'Authorization': f'Bearer {upload_key}'},
                    timeout=300,
                )

            if response.status_code == 201:
                data = response.json()
                self.stdout.write(self.style.SUCCESS(
                    f"Upload successful!\n"
                    f"  Saved as: {data.get('saved_as')}\n"
                    f"  Version:  {data.get('model_version')}\n"
                    f"  Trained:  {data.get('trained_at')}\n"
                    f"  Items:    {data.get('n_items')}\n"
                    f"  Local users: {data.get('n_local_users')}"
                ))
            else:
                try:
                    error = response.json()
                except Exception:
                    error = response.text
                self.stderr.write(self.style.ERROR(
                    f'Upload failed (HTTP {response.status_code}): {error}'
                ))

        except requests.ConnectionError:
            self.stderr.write(self.style.ERROR(f'Could not connect to {url}'))
        except requests.Timeout:
            self.stderr.write(self.style.ERROR('Upload timed out (300s). Try a smaller model or faster connection.'))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Upload error: {e}'))
