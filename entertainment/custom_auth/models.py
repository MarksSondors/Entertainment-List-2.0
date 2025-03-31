from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
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
    profile_picture = models.ImageField(upload_to='person_profiles/', blank=True, null=True)

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

class Movie(Media):
    poster = models.ImageField(upload_to='movie_posters/', blank=True, null=True)
    backdrop = models.ImageField(upload_to='movie_backdrops/', blank=True, null=True)

    release_date = models.DateField()
    tmdb_id = models.IntegerField(unique=True)

    runtime = models.IntegerField()
    rating = models.FloatField()
    trailer = models.URLField(blank=True, null=True)

    is_anime = models.BooleanField(default=False)

    # foreign keys
    genres = models.ManyToManyField(Genre)
    countries = models.ManyToManyField(Country)
    keywords = models.ManyToManyField(Keyword)

    def __str__(self):
        return self.title
    
    def minutes_to_hours(self):
        hours = self.runtime // 60
        minutes = self.runtime % 60
        return f"{hours}h {minutes}m"

    def get_media_persons(self, role=None):
        """Get all MediaPerson objects related to this movie."""
        query = MediaPerson.objects.filter(
            content_type_id=ContentType.objects.get_for_model(self.__class__).id,
            object_id=self.id
        ).select_related('person')
        
        if role:
            query = query.filter(role=role)
        return query
    
    @property
    def _get_content_type_id(self):
        """Cache content type ID to avoid repeated lookups."""
        if not hasattr(self, '_content_type_id'):
            self._content_type_id = ContentType.objects.get_for_model(self.__class__).id
        return self._content_type_id
    
    def _get_persons_by_role(self, role_filter):
        """Base method for retrieving persons by role."""
        return Person.objects.filter(
            mediaperson__content_type_id=self._get_content_type_id,
            mediaperson__object_id=self.id,
            **role_filter
        ).distinct()
    
    @property
    def directors(self):
        """Get all directors of this movie."""
        return self._get_persons_by_role({'mediaperson__role': "Director"})
    
    @property
    def writers(self):
        """Get all writers of this movie."""
        return self._get_persons_by_role({
            'mediaperson__role__in': ["Writer", "Screenplay", "Original Story", "Story"]
        })
    
    @property
    def producers(self):
        """Get all producers of this movie."""
        return self._get_persons_by_role({'mediaperson__role': "Executive Producer"})
    
    @property
    def cast(self):
        """Get all cast members of this movie with their character names."""
        return MediaPerson.objects.select_related('person').filter(
            content_type_id=self._get_content_type_id,
            object_id=self.id,
            role="Actor"
        ).order_by('order')
    
    @property
    def composers(self):
        """Get all composers of this movie."""
        return self._get_persons_by_role({'mediaperson__role': "Original Music Composer"})

class TVShow(Media):
    """Model for TV shows."""
    poster = models.ImageField(upload_to='tvshow_posters/', blank=True, null=True)
    backdrop = models.ImageField(upload_to='tvshow_backdrops/', blank=True, null=True)
    
    first_air_date = models.DateField(blank=True, null=True)
    last_air_date = models.DateField(blank=True, null=True)
    tmdb_id = models.IntegerField(unique=True)
    
    status = models.CharField(max_length=50, blank=True, null=True)  # e.g., "Ended", "Returning Series"
    rating = models.FloatField(blank=True, null=True)
    trailer = models.URLField(blank=True, null=True)
    
    is_anime = models.BooleanField(default=False)
    
    # foreign keys
    genres = models.ManyToManyField(Genre)
    countries = models.ManyToManyField(Country)
    keywords = models.ManyToManyField(Keyword)
    
    def __str__(self):
        return self.title
    
    def get_media_persons(self, role=None):
        """Get all MediaPerson objects related to this TV show."""
        query = MediaPerson.objects.filter(
            content_type_id=ContentType.objects.get_for_model(self.__class__).id,
            object_id=self.id
        ).select_related('person')
        
        if role:
            query = query.filter(role=role)
        return query
    
    @property
    def _get_content_type_id(self):
        """Cache content type ID to avoid repeated lookups."""
        if not hasattr(self, '_content_type_id'):
            self._content_type_id = ContentType.objects.get_for_model(self.__class__).id
        return self._content_type_id
    
    def _get_persons_by_role(self, role_filter):
        """Base method for retrieving persons by role."""
        return Person.objects.filter(
            mediaperson__content_type_id=self._get_content_type_id,
            mediaperson__object_id=self.id,
            **role_filter
        ).distinct()
    
    @property
    def creators(self):
        """Get all creators of this TV show."""
        return self._get_persons_by_role({'mediaperson__role': "Creator"})
    
    @property
    def cast(self):
        """Get all cast members of this TV show with their character names."""
        return MediaPerson.objects.select_related('person').filter(
            content_type_id=self._get_content_type_id,
            object_id=self.id,
            role="Actor"
        ).order_by('order')
    
    @property
    def seasons_count(self):
        """Get the number of seasons for this TV show."""
        return self.seasons.count()
    
    @property
    def episodes_count(self):
        """Get the total number of episodes across all seasons."""
        return Episode.objects.filter(season__show=self).count()

class Season(models.Model):
    """Model for TV show seasons."""
    show = models.ForeignKey(TVShow, related_name='seasons', on_delete=models.CASCADE)
    
    title = models.CharField(max_length=255, blank=True, null=True)
    season_number = models.PositiveIntegerField()
    air_date = models.DateField(blank=True, null=True)
    overview = models.TextField(blank=True, null=True)
    poster = models.ImageField(upload_to='season_posters/', blank=True, null=True)
    
    tmdb_id = models.IntegerField(blank=True, null=True)
    
    class Meta:
        unique_together = ['show', 'season_number']
        ordering = ['season_number']
        
    def __str__(self):
        return f"{self.show.title} - Season {self.season_number}"
    
    @property
    def episodes_count(self):
        """Get the number of episodes in this season."""
        return self.episodes.count()

class Episode(models.Model):
    """Model for TV show episodes."""
    season = models.ForeignKey(Season, related_name='episodes', on_delete=models.CASCADE)
    
    title = models.CharField(max_length=255)
    episode_number = models.PositiveIntegerField()
    air_date = models.DateField(blank=True, null=True)
    overview = models.TextField(blank=True, null=True)
    still = models.ImageField(upload_to='episode_stills/', blank=True, null=True)
    
    runtime = models.IntegerField(blank=True, null=True)
    rating = models.FloatField(blank=True, null=True)
    tmdb_id = models.IntegerField(blank=True, null=True)
    
    class Meta:
        unique_together = ['season', 'episode_number']
        ordering = ['episode_number']
        
    def __str__(self):
        return f"{self.season.show.title} - S{self.season.season_number:02d}E{self.episode_number:02d} - {self.title}"
    
    def minutes_to_hours(self):
        if not self.runtime:
            return "Unknown"
        hours = self.runtime // 60
        minutes = self.runtime % 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

class WatchedEpisode(models.Model):
    """Model for tracking which episodes a user has watched."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='watched_episodes')
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name='watched_by')
    
    watched_date = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'episode']
        ordering = ['-watched_date']
        
    def __str__(self):
        return f"{self.user.username} watched {self.episode}"

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
    object_id = models.PositiveIntegerField()
    media = GenericForeignKey('content_type', 'object_id')
    
    # Tracking information
    date_added = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date_added']
        # Ensure a user can't add the same item twice
        unique_together = ['user', 'content_type', 'object_id']
        
    def __str__(self):
        return f"{self.user.username}'s watchlist - {self.media}"

class Review(models.Model):
    """Model for user reviews of media items."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    
    # Generic foreign key to allow different media types
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    media = GenericForeignKey('content_type', 'object_id')
    
    # Review content
    rating = models.FloatField()
    review_text = models.TextField(blank=True, null=True)
    date_reviewed = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-date_reviewed']
        unique_together = ['user', 'content_type', 'object_id']
        
    def __str__(self):
        return f"{self.user.username}'s review of {self.media} - {self.rating}/10"
    
    def save(self, *args, **kwargs):
        # Remove from watchlist when reviewed
        Watchlist.objects.filter(
            user=self.user,
            content_type=self.content_type,
            object_id=self.object_id
        ).delete()
        
        super().save(*args, **kwargs)

