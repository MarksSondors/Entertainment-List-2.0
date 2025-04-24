from django.db import models
from django.utils import timezone

from custom_auth.models import *

# Create your models here.
class TVShow(Media):
    """Model for TV shows."""

    poster = models.URLField(blank=True, null=True)
    backdrop = models.URLField(blank=True, null=True)
    
    first_air_date = models.DateField(blank=True, null=True)
    last_air_date = models.DateField(blank=True, null=True)

    tmdb_id = models.IntegerField(unique=True)
    imdb_id = models.CharField(max_length=20, blank=True, null=True)
    
    status = models.CharField(max_length=50, blank=True, null=True)  # e.g., "Ended", "Returning Series"
    rating = models.FloatField(blank=True, null=True)
    trailer = models.URLField(blank=True, null=True)
    
    is_anime = models.BooleanField(default=False)
    
    # foreign keys
    genres = models.ManyToManyField(Genre)
    countries = models.ManyToManyField(Country)
    keywords = models.ManyToManyField(Keyword)

    # user related fields
    added_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, related_name='added_tv_shows', blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.title
    
    def get_absolute_url(self):
        """Get the absolute URL for the TV show page."""
        return f"/tvshows/{self.id}/"
    
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
    
    def get_upcoming_episodes(self, limit=None):
        """Get upcoming episodes for this TV show."""
        today = timezone.now().date()
        upcoming = Episode.objects.filter(
            season__show=self,
            air_date__gt=today
        ).order_by('air_date')
        
        if limit:
            upcoming = upcoming[:limit]
        
        return upcoming

    def get_next_episode(self):
        """Get the next episode to air."""
        upcoming = self.get_upcoming_episodes(limit=1)
        return upcoming.first()

    def get_crew(self):
        """
        Returns crew members with their combined roles.
        Example: {'Person1': ['Director', 'Writer'], 'Person2': ['Producer']}
        """
        # Get all crew members (not actors)
        crew_members = MediaPerson.objects.filter(
            content_type_id=self._get_content_type_id,
            object_id=self.id
        ).exclude(role="Actor")
        
        # Group by person and combine roles
        crew_by_person = {}
        for member in crew_members:
            if member.person not in crew_by_person:
                crew_by_person[member.person] = []
            crew_by_person[member.person].append(member.role)
        
        return crew_by_person

class Season(models.Model):
    """Model for TV show seasons."""
    show = models.ForeignKey(TVShow, related_name='seasons', on_delete=models.CASCADE)
    
    title = models.CharField(max_length=255, blank=True, null=True)
    season_number = models.PositiveIntegerField()
    air_date = models.DateField(blank=True, null=True)
    overview = models.TextField(blank=True, null=True)
    poster = models.URLField(blank=True, null=True)
    
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
    
    def user_completion_percentage(self, user):
        """Calculate what percentage of episodes the user has watched."""
        total_episodes = self.episodes.count()
        if total_episodes == 0:
            return 0
        
        watched_episodes = user.watched_episodes.filter(
            episode__season=self
        ).count()
        
        return (watched_episodes / total_episodes) * 100
    
    def user_has_completed(self, user):
        """Check if a user has watched all episodes in this season."""
        return self.user_completion_percentage(user) == 100

class Episode(models.Model):
    """Model for TV show episodes."""
    season = models.ForeignKey(Season, related_name='episodes', on_delete=models.CASCADE)
    
    title = models.CharField(max_length=255)
    episode_number = models.PositiveIntegerField()
    air_date = models.DateField(blank=True, null=True)
    overview = models.TextField(blank=True, null=True)
    still = models.URLField(blank=True, null=True)
    
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
    
    @property
    def is_aired(self):
        """Check if the episode has already aired."""
        if not self.air_date:
            return False
        return self.air_date <= timezone.now().date()

class EpisodeGroup(models.Model):
    """Top-level episode grouping category (like 'Story Arcs', 'Viewing Orders', etc.)"""
    tmdb_id = models.CharField(max_length=30, blank=True, null=True, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    show = models.ForeignKey(TVShow, related_name='episode_groups', on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ['order', 'name']
        
    def __str__(self):
        return f"{self.show.title} - {self.name}"

class EpisodeSubGroup(models.Model):
    """Specific episode groupings that belong to a parent group"""
    tmdb_id = models.CharField(max_length=30, blank=True, null=True, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    parent_group = models.ForeignKey(EpisodeGroup, related_name='sub_groups', on_delete=models.CASCADE)
    episodes = models.ManyToManyField(Episode, related_name='sub_groups')
    
    # Optional fields for ordering and display
    order = models.PositiveIntegerField(default=0)
    poster = models.URLField(blank=True, null=True)
    
    class Meta:
        ordering = ['order', 'name']
        
    def __str__(self):
        return f"{self.parent_group} - {self.name}"
    
    def user_completion_percentage(self, user):
        """Calculate what percentage of episodes the user has watched."""
        total_episodes = self.episodes.count()
        if total_episodes == 0:
            return 0
        
        watched_episodes = user.watched_episodes.filter(
            episode__in=self.episodes.all()
        ).count()
        
        return (watched_episodes / total_episodes) * 100
    
    def user_has_completed(self, user):
        """Check if a user has watched all episodes in this subgroup."""
        total_episodes = self.episodes.count()
        if total_episodes == 0:
            return False
            
        watched_count = WatchedEpisode.objects.filter(
            user=user,
            episode__in=self.episodes.all()
        ).count()
        
        return watched_count == total_episodes

class WatchedEpisode(models.Model):
    """Model for tracking which episodes a user has watched."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='watched_episodes')
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name='watched_by')
    
    watched_date = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ['user', 'episode']
        ordering = ['-watched_date']
        indexes = [
            models.Index(fields=['user', 'episode']),
            models.Index(fields=['watched_date']),
        ]
        
    def __str__(self):
        return f"{self.user.username} watched {self.episode}"