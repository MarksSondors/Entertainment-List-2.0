from django.db import models
from custom_auth.models import Genre, Country, Person, Keyword
# Create your models here.

class Movie(models.Model):
    title = models.CharField(max_length=100)
    original_title = models.CharField(max_length=100)

    poster = models.URLField()
    backdrop = models.URLField()

    release_date = models.DateField()
    tmdb_id = models.IntegerField(unique=True)

    runtime = models.IntegerField()
    plot = models.TextField()
    rating = models.FloatField()
    trailer = models.URLField(blank=True, null=True)

    is_anime = models.BooleanField(default=False)

    # foreign keys
    genres = models.ManyToManyField(Genre)
    countries = models.ManyToManyField(Country)
    directors = models.ManyToManyField(Person, related_name='directors', limit_choices_to={'is_director': True}, blank=True)
    writers = models.ManyToManyField(Person, related_name='writers', limit_choices_to={'is_writer': True}, blank=True)
    producers = models.ManyToManyField(Person, related_name='producers', limit_choices_to={'is_producer': True}, blank=True)
    cast = models.ManyToManyField(Person, related_name='cast', limit_choices_to={'is_actor': True}, blank=True)
    composer = models.ManyToManyField(Person, related_name='sound', limit_choices_to={'is_composer': True}, blank=True)
    keywords = models.ManyToManyField(Keyword)

    def __str__(self):
        return self.title
    
    def minutes_to_hours(self):
        hours = self.runtime // 60
        minutes = self.runtime % 60
        return f"{hours}h {minutes}m"