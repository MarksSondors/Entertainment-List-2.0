"""Pre-render small WebP thumbnails for every uploaded user avatar.

Thumbnails are also built when a user saves a new picture and lazily on first
display; run this once after deploying so no visitor pays the first-render
cost. Originals are left untouched.

    python manage.py build_avatar_thumbnails
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from custom_auth.models import CustomUser
from custom_auth.services.avatar_images import ensure_thumbnail


class Command(BaseCommand):
    help = "Build WebP thumbnails for CustomUser.profile_picture."

    def handle(self, *args, **options):
        built, missing = 0, []
        for user in CustomUser.objects.exclude(profile_picture='').exclude(profile_picture__isnull=True):
            if ensure_thumbnail(user):
                built += 1
            else:
                missing.append(f'{user.username} ({user.profile_picture.name})')

        self.stdout.write(self.style.SUCCESS(f'{built} avatar thumbnails ready.'))
        for item in missing:
            self.stdout.write(self.style.WARNING(f'Skipped (missing or unreadable): {item}'))
