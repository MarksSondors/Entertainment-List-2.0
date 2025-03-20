from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
# Create your models here.


class CustomUser(AbstractUser):
    email = models.EmailField(unique=True)
    bio = models.TextField(blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)

    def get_profile_picture(self):
        if self.profile_picture:
            return self.profile_picture.url
        return 'https://www.gravatar.com/avatar/'

    def __str__(self):
        return self.username

class Genre(models.Model):
    name = models.CharField(max_length=100)
    tmdb_id = models.IntegerField(blank=True, null=True)


    def __str__(self):
        return self.name

class Country(models.Model):
    name = models.CharField(max_length=100)
    iso_3166_1 = models.CharField(max_length=2)

    def __str__(self):
        return self.name

class Keyword(models.Model):
    name = models.CharField(max_length=100)
    tmdb_id = models.IntegerField(blank=True, null=True)

    def __str__(self):
        return self.name

class Person(models.Model):
    name = models.CharField(max_length=100)
    bio = models.TextField(blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    date_of_death = models.DateField(blank=True, null=True)
    profile_picture = models.URLField(blank=True, null=True)
    is_actor = models.BooleanField(default=False)
    is_director = models.BooleanField(default=False)
    is_producer = models.BooleanField(default=False)
    is_writer = models.BooleanField(default=False)
    is_composer = models.BooleanField(default=False)

    imdb_id = models.CharField(max_length=20, blank=True, null=True)
    tmdb_id = models.IntegerField(unique=True, blank=True, null=True)

    def __str__(self):
        return self.name

class Review(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    content = models.TextField()
    rating = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_movie = models.BooleanField(default=False)
    movie = models.ForeignKey('movies.Movie', on_delete=models.CASCADE, blank=True, null=True)

    def __str__(self):
        return self.content

