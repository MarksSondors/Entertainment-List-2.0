from django.db import models
from custom_auth.models import Genre, Country, Person, Keyword
# Create your models here.

class Movie(models.Model):
    title = models.CharField(max_length=100)
    original_title = models.CharField(max_length=100)

    poster = models.URLField()
    backdrops = models.JSONField()

    release_date = models.DateField()
    tmdb_id = models.IntegerField()

    runtime = models.IntegerField()
    plot = models.TextField()
    rating = models.FloatField()
    trailer = models.URLField(blank=True, null=True)

    is_anime = models.BooleanField(default=False)

    # foreign keys
    genres = models.ManyToManyField(Genre)
    countries = models.ManyToManyField(Country)
    directors = models.ManyToManyField(Person, related_name='directors')
    writers = models.ManyToManyField(Person, related_name='writers')
    producers = models.ManyToManyField(Person, related_name='producers')
    cast = models.ManyToManyField(Person, related_name='cast')
    keywords = models.ManyToManyField(Keyword)

    def __str__(self):
        return self.title