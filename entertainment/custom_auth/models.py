from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import pre_delete
from django.dispatch import receiver


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
        
    def get_watchlist(self):
        """Get all items in the user's watchlist."""
        return self.watchlist_items.all()
    
    def get_reviews(self):
        """Get all reviews by the user."""
        return self.reviews.all()

    def get_watched_episodes(self):
        """Get all episodes watched by the user."""
        return self.watched_episodes.all()

    def get_watched_episodes_for_show(self, tv_show):
        """Get all episodes of a specific TV show watched by the user."""
        return self.watched_episodes.filter(episode__season__show=tv_show)

    def get_watched_episodes_for_season(self, season):
        """Get all episodes of a specific season watched by the user."""
        return self.watched_episodes.filter(episode__season=season)

    def get_watch_progress(self, tv_show):
        """Get the watch progress for a TV show (percentage of episodes watched)."""
        total_episodes = Episode.objects.filter(season__show=tv_show).count()
        if total_episodes == 0:
            return 0
        watched_episodes = self.watched_episodes.filter(episode__season__show=tv_show).count()
        return (watched_episodes / total_episodes) * 100

class Genre(models.Model):
    name = models.CharField(max_length=100)
    tmdb_id = models.IntegerField(blank=True, null=True)

    icon = models.ImageField(upload_to='genre_icons/', blank=True, null=True)

    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        """Get the absolute URL for the genre page."""
        return f"/genres/{self.id}/"

class Country(models.Model):
    name = models.CharField(max_length=100)
    iso_3166_1 = models.CharField(max_length=2)

    def __str__(self):
        return self.name
    
    def get_absolute_url(self):
        """Get the absolute URL for the country page."""
        return f"/countries/{self.id}/"

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

    # directing
    is_director = models.BooleanField(default=False)

    # writing
    is_screenwriter = models.BooleanField(default=False)
    is_writer = models.BooleanField(default=False)
    is_story = models.BooleanField(default=False)

    # sound
    is_original_music_composer = models.BooleanField(default=False)

    # external to the movie industry, but can be related to it mangaka or author
    is_original_story = models.BooleanField(default=False)
    is_novelist = models.BooleanField(default=False)
    is_comic_artist = models.BooleanField(default=False)
    is_graphic_novelist = models.BooleanField(default=False)

    # tv show specific
    is_tv_creator = models.BooleanField(default=False)

    # actor
    is_actor = models.BooleanField(default=False)

    # music roles
    is_musician = models.BooleanField(default=False)
    
    # external IDs for music services
    musicbrainz_id = models.CharField(max_length=36, blank=True, null=True)

    imdb_id = models.CharField(max_length=20, blank=True, null=True)
    tmdb_id = models.IntegerField(unique=True, blank=True, null=True)

    def __str__(self):
        return self.name

class Media(models.Model):
    title = models.CharField(max_length=255)
    original_title = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    class Meta:
        abstract = True

class MediaPerson(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    media = GenericForeignKey('content_type', 'object_id')

    person = models.ForeignKey('Person', on_delete=models.CASCADE)
    role = models.CharField(max_length=50)  # e.g., "Director", "Writer", "Actor", "Composer", "Author", "Artist"
    character_name = models.CharField(max_length=100, blank=True, null=True)  # For actors, the name of their character
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']
        
class Watchlist(models.Model):
    """Model for tracking media items a user wants to watch."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='watchlist_items')
    
    # Generic foreign key to allow different media types
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField(db_index=True)
    media = GenericForeignKey('content_type', 'object_id')
    
    # Tracking information
    date_added = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date_added']
        unique_together = ['user', 'content_type', 'object_id']
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]
        
    def __str__(self):
        return f"{self.user.username}'s watchlist - {self.media}"

# Add signals to automatically remove watchlist entries when media is deleted

@receiver(pre_delete)
def remove_from_watchlist(sender, instance, **kwargs):
    """Remove any watchlist entries when media is deleted."""
    # Check if the model is one that can be in a watchlist
    media_models = ['Movie', 'TVShow', 'Album']
    
    if sender.__name__ in media_models:
        # Direct media deletion
        content_type = ContentType.objects.get_for_model(sender)
        Watchlist.objects.filter(content_type=content_type, object_id=instance.id).delete()
    
class Review(models.Model):
    """Model for user reviews of media items."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    
    # Generic foreign key to allow different media types
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    media = GenericForeignKey('content_type', 'object_id')
    
    # For TV shows, we can link to a specific season
    season = models.ForeignKey('tvshows.Season', on_delete=models.CASCADE, blank=True, null=True, related_name='reviews')
    
    # Review content
    rating = models.FloatField()
    review_text = models.TextField(blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date_added']
        # Update unique constraint to include season for TV shows
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'content_type', 'object_id', 'season'],
                name='unique_review_with_season'
            ),
            models.UniqueConstraint(
                fields=['user', 'content_type', 'object_id'],
                condition=models.Q(season__isnull=True),
                name='unique_review_without_season'
            ),
        ]
        
    def __str__(self):
        if self.season:
            return f"{self.user.username}'s review of {self.season} - {self.rating}/10"
        return f"{self.user.username}'s review of {self.media} - {self.rating}/10"
    
    def save(self, *args, **kwargs):
        # Get TVShow model dynamically to avoid circular import
        from django.apps import apps
        TVShow = apps.get_model('tvshows', 'TVShow')
        
        # For TV shows, check if the content type is TVShow
        if self.content_type.model_class() == TVShow and not self.season:
            raise ValueError("Reviews for TV shows must include a season")
        
        # For non-TV shows, ensure season is None
        if self.content_type.model_class() != TVShow:
            self.season = None
        
        # Remove from watchlist when reviewed
        Watchlist.objects.filter(
            user=self.user,
            content_type=self.content_type,
            object_id=self.object_id
        ).delete()
        
        super().save(*args, **kwargs)
