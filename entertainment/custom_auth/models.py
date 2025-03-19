from django.db import models
from django.contrib.auth.models import AbstractUser
# Create your models here.


class CustomUser(AbstractUser):
    email = models.EmailField(unique=True)
    bio = models.TextField(blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)

    def __str__(self):
        return self.username

class Genre(models.Model):
    name = models.CharField(max_length=100)
    tmdb_id = models.IntegerField(unique=True)

    def __str__(self):
        return self.name

class Person(models.Model):
    name = models.CharField(max_length=100)
    bio = models.TextField(blank=True, null=True)
    date_of_birth = models.DateField()
    date_of_death = models.DateField(blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', blank=True, null=True)
    is_actor = models.BooleanField(default=False)
    is_director = models.BooleanField(default=False)
    is_producer = models.BooleanField(default=False)
    is_writer = models.BooleanField(default=False)

    def __str__(self):
        return self.name

