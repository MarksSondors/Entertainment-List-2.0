from django.conf import settings
from django.db import models


class AddonEvent(models.Model):
    """Raw player/library event sent by a Stremio client (probe for watch sync, #33).

    Stored as received and not acted on yet: the point is to learn which clients send
    events and what the ids and timings look like before anything marks episodes watched.
    """

    KIND_CHOICES = [('player', 'Player'), ('library', 'Library')]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='addon_events')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    media_type = models.CharField(max_length=20)
    item_id = models.CharField(max_length=100)  # tt0903747 or tt0903747:2:5
    action = models.CharField(max_length=30, blank=True)
    current_time = models.BigIntegerField(blank=True, null=True)  # ms
    duration = models.BigIntegerField(blank=True, null=True)  # ms
    video_ids = models.JSONField(default=list, blank=True)  # library batches
    extra = models.TextField(blank=True)  # the raw extra segment, unparsed
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'created_at'])]

    def __str__(self):
        return f"{self.user_id} {self.kind}:{self.action} {self.media_type}/{self.item_id}"
