from django.db import models

# Remove this import to break the circular dependency
# from custom_auth.models import Movie

# Create your models here.

# movie collections
class Collection(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    poster = models.URLField(blank=True, null=True)
    backdrop = models.URLField(blank=True, null=True)

    tmdb_id = models.IntegerField(unique=True, blank=True, null=True)

    def __str__(self):
        return self.name
    
    def movies(self):
        # Use the app_label.model_name format instead of direct import
        return models.Q(app_label='custom_auth', model='Movie').objects.filter(collection=self)
