from django.db import models
from django.contrib.contenttypes.models import ContentType
from custom_auth.models import CustomUser, Media, Keyword

# Create your models here.

class GameGenre(models.Model):
    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=100, blank=True, null=True)
    rawg_id = models.IntegerField(unique=True, blank=True, null=True)

    def __str__(self):
        return self.name

class Platform(models.Model):
    name = models.CharField(max_length=100)
    slug = models.CharField(max_length=100, blank=True, null=True)
    rawg_id = models.IntegerField(unique=True, blank=True, null=True)
    
    def __str__(self):
        return self.name

class GameDeveloper(models.Model):
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255, blank=True, null=True)
    rawg_id = models.IntegerField(unique=True, blank=True, null=True)
    image_background = models.URLField(blank=True, null=True)

    def __str__(self):
        return self.name

class GamePublisher(models.Model):
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255, blank=True, null=True)
    rawg_id = models.IntegerField(unique=True, blank=True, null=True)
    image_background = models.URLField(blank=True, null=True)

    def __str__(self):
        return self.name

class GameCollection(models.Model):
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255, blank=True, null=True)
    rawg_id = models.IntegerField(unique=True, blank=True, null=True)
    image_background = models.URLField(blank=True, null=True)
    
    def __str__(self):
        return self.name

class Game(Media):
    """Main Game model - basic structure to start fresh"""
    
    # Basic information
    release_date = models.DateField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    # Visual assets
    poster = models.URLField(blank=True, null=True)
    backdrop = models.URLField(blank=True, null=True)
    
    # Ratings
    rating = models.FloatField(blank=True, null=True)
    metacritic = models.IntegerField(blank=True, null=True)
    
    # External IDs - RAWG API integration
    rawg_id = models.IntegerField(unique=True, blank=True, null=True)
    rawg_slug = models.CharField(max_length=255, blank=True, null=True)
    
    # Relationships
    genres = models.ManyToManyField(GameGenre, blank=True)
    keywords = models.ManyToManyField(Keyword, blank=True)
    platforms = models.ManyToManyField(Platform, blank=True)
    developers = models.ManyToManyField(GameDeveloper, blank=True, related_name='developed_games')
    publishers = models.ManyToManyField(GamePublisher, blank=True, related_name='published_games')
    game_collection = models.ForeignKey(GameCollection, on_delete=models.SET_NULL, blank=True, null=True, related_name='games')

    # Metadata
    added_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, related_name='added_games', blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.title
    
    def get_absolute_url(self):
        """Get the absolute URL for the game detail page."""
        from django.urls import reverse
        return reverse('game_detail', kwargs={'pk': self.id})
    
    @classmethod
    def get_content_type(cls):
        """Get ContentType for this model - used for generic relations."""
        return ContentType.objects.get_for_model(cls)
    
    @property
    def content_type_id(self):
        """Quick access to content type ID."""
        return self.get_content_type().id
    
    @property
    def year(self):
        """Get release year for compatibility with other media types."""
        return self.release_date.year if self.release_date else None
