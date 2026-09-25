"""Delete Person rows that no media in the database refers to.

A person is kept while they have a MediaPerson credit (movies, TV shows,
books), authored a book, or are a primary/featured album artist. MediaPerson
rows pointing at media that no longer exists are removed first, since deleting
a movie or show leaves its credits behind (the relation is generic, no cascade).

    python manage.py cleanup_orphan_people [--dry-run]
"""
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import transaction

from custom_auth.models import MediaPerson, Person


class Command(BaseCommand):
    help = "Remove credits for deleted media, then people not linked to any media."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting anything.",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]

        with transaction.atomic():
            dangling_ids: list[int] = []
            content_type_ids = set(MediaPerson.objects.order_by().values_list("content_type_id", flat=True).distinct())
            for content_type in ContentType.objects.filter(id__in=content_type_ids):
                model = content_type.model_class()
                if model is None:
                    # Model was removed from the codebase; all its credits are dangling.
                    rows = MediaPerson.objects.filter(content_type=content_type)
                else:
                    rows = MediaPerson.objects.filter(content_type=content_type).exclude(
                        object_id__in=model.objects.values("id")
                    )
                dangling_ids += rows.values_list("id", flat=True)

            orphans = Person.objects.exclude(
                id__in=MediaPerson.objects.exclude(id__in=dangling_ids).values("person_id")
            ).filter(
                books__isnull=True,
                primary_albums__isnull=True,
                featured_albums__isnull=True,
            )
            orphan_ids = list(orphans.values_list("id", flat=True))

            if not dry_run:
                MediaPerson.objects.filter(id__in=dangling_ids).delete()
                Person.objects.filter(id__in=orphan_ids).delete()

        verb = "Would remove" if dry_run else "Removed"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {len(dangling_ids)} credits for deleted media and {len(orphan_ids)} orphaned people."
        ))
