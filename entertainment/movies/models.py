from django.db import models
from custom_auth.models import Movie
# Create your models here.


# movie collections
class Collection(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    poster = models.ImageField(upload_to='collections/posters/', blank=True, null=True)
    backdrop = models.ImageField(upload_to='collections/backdrops/', blank=True, null=True)

    def __str__(self):
        return self.name
    
    def movies(self):
        return Movie.objects.filter(collection=self)
