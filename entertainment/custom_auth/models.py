from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import pre_delete, post_save
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
        """Get all watchlist items for the user with related content types."""
        return Watchlist.objects.filter(user=self).select_related('content_type')
    
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
        from django.apps import apps
        Episode = apps.get_model('tvshows', 'Episode')
        
        total_episodes = Episode.objects.filter(season__show=tv_show).exclude(season__season_number=0).count()
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
    is_book = models.BooleanField(default=False)

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
    wikidata_id = models.CharField(max_length=20, blank=True, null=True)

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
    character_name = models.CharField(blank=True, null=True)  # For actors, the name of their character
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
    
    # For TV shows, we can link to a specific season or episode subgroup
    season = models.ForeignKey('tvshows.Season', on_delete=models.CASCADE, blank=True, null=True, related_name='reviews')
    episode_subgroup = models.ForeignKey('tvshows.EpisodeSubGroup', on_delete=models.CASCADE, blank=True, null=True, related_name='reviews')
    
    # Review content
    rating = models.FloatField()
    review_text = models.TextField(blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date_added']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'content_type', 'object_id', 'season', 'episode_subgroup'],
                name='unique_review_with_season_or_subgroup'
            ),
            models.UniqueConstraint(
                fields=['user', 'content_type', 'object_id'],
                condition=models.Q(season__isnull=True) & models.Q(episode_subgroup__isnull=True),
                name='unique_review_without_season_or_subgroup'
            ),
        ]
        
    def __str__(self):
        if self.season:
            return f"{self.user.username}'s review of {self.season} - {self.rating}/10"
        elif self.episode_subgroup:
            return f"{self.user.username}'s review of {self.episode_subgroup} - {self.rating}/10"
        return f"{self.user.username}'s review of {self.media} - {self.rating}/10"
    
    def save(self, *args, **kwargs):
        # Get models dynamically to avoid circular import
        from django.apps import apps
        TVShow = apps.get_model('tvshows', 'TVShow')
        
        if self.content_type.model_class() == TVShow:
            # A review for a TV show must be associated with either a season or an episode subgroup
            if not self.season and not self.episode_subgroup:
                raise ValueError("Reviews for TV shows must include a season or episode subgroup")
            
            # Can't have both season and episode subgroup
            if self.season and self.episode_subgroup:
                raise ValueError("Reviews for TV shows can't have both season and episode subgroup")
            
            # Check if all episodes have been watched before reviewing
            if self.season and not self.season.user_has_completed(self.user):
                raise ValueError(f"Can't review season {self.season} until all episodes have been watched")
            
            if self.episode_subgroup and not self.episode_subgroup.user_has_completed(self.user):
                raise ValueError(f"Can't review episode subgroup {self.episode_subgroup} until all episodes have been watched")
        
        # For non-TV shows, ensure season and episode_subgroup are None
        if self.content_type.model_class() != TVShow:
            self.season = None
            self.episode_subgroup = None
            # Remove from watchlist when reviewed
            Watchlist.objects.filter(
                user=self.user,
                content_type=self.content_type,
                object_id=self.object_id
            ).delete()
        
        super().save(*args, **kwargs)

class UserSettings(models.Model):
    """User settings and preferences"""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='settings')
    
    # Content display settings
    show_keywords = models.BooleanField(default=True, help_text="Show keywords in media details")
    show_review_text = models.BooleanField(default=True, help_text="Show review text in media details")
    show_plot = models.BooleanField(default=True, help_text="Show plot in media details")
    
    # Theme settings (if you want to add these in the future)
    # theme = models.CharField(max_length=50, default="windows98", choices=[...])
    
    # Other potential settings you might add:
    # default_language = models.CharField(...)
    # notifications_enabled = models.BooleanField(...)
    
    def __str__(self):
        return f"{self.user.username}'s settings"


# Signal to create settings when a user is created
@receiver(post_save, sender=CustomUser)
def create_user_settings(sender, instance, created, **kwargs):
    """Create UserSettings when a new user is created"""
    if created:
        UserSettings.objects.create(user=instance)

@receiver(post_save, sender=CustomUser)
def save_user_settings(sender, instance, **kwargs):
    """Save UserSettings when the user is saved"""
    # This ensures the settings are saved if the user is updated
    if hasattr(instance, 'settings'):
        instance.settings.save()
    else:
        # If for some reason the settings don't exist, create them
        UserSettings.objects.create(user=instance)
