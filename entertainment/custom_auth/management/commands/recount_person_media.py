"""Backfill (or reconcile) `Person.media_count`.

Run once after applying migration 0024, and periodically as a safety net
against missed signals (e.g. bulk_create bypasses post_save).

    python manage.py recount_person_media
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from custom_auth.models import MediaPerson, Person


class Command(BaseCommand):
    help = "Recompute Person.media_count from MediaPerson rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of Person rows to update per bulk_update call.",
        )

    def handle(self, *args, **options):
        batch_size: int = options["batch_size"]

        counts = dict(
            MediaPerson.objects.values("person_id")
            .annotate(n=Count("id"))
            .values_list("person_id", "n")
        )

        updated = 0
        buffer: list[Person] = []
        for person in Person.objects.only("id", "media_count").iterator():
            desired = counts.get(person.id, 0)
            if person.media_count != desired:
                person.media_count = desired
                buffer.append(person)
                if len(buffer) >= batch_size:
                    Person.objects.bulk_update(buffer, ["media_count"])
                    updated += len(buffer)
                    buffer.clear()

        if buffer:
            Person.objects.bulk_update(buffer, ["media_count"])
            updated += len(buffer)

        self.stdout.write(
            self.style.SUCCESS(f"Reconciled media_count on {updated} person rows.")
        )
