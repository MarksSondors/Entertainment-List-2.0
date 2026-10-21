"""Pre-render card thumbnails for every genre background image.

Thumbnails are also built on Genre save and lazily by the discover API; run
this once after deploying so no visitor pays the first-render cost.

    python manage.py build_genre_thumbnails
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from custom_auth.models import Genre
from custom_auth.services.genre_images import ensure_thumbnail


class Command(BaseCommand):
    help = "Build WebP card thumbnails for Genre.background_image."

    def handle(self, *args, **options):
        built, missing = 0, []
        for genre in Genre.objects.exclude(background_image='').exclude(background_image__isnull=True):
            if ensure_thumbnail(genre):
                built += 1
            else:
                missing.append(f'{genre.name} ({genre.background_image.name})')

        self.stdout.write(self.style.SUCCESS(f'{built} genre thumbnails ready.'))
        for item in missing:
            self.stdout.write(self.style.WARNING(f'Skipped (missing or unreadable): {item}'))
