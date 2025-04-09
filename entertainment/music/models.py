from django.db import models

from custom_auth.models import *
from tvshows.models import TVShow

# Create your models here.
class Album(Media):
    """Model for music albums that extends the base Media class."""
    cover = models.URLField(blank=True, null=True)  # Album cover
    
    release_date = models.DateField(blank=True, null=True)
    musicbrainz_id = models.CharField(max_length=36, unique=True, blank=True, null=True)
    
    # Album metadata
    primary_artist = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='primary_albums')
    featured_artists = models.ManyToManyField(Person, related_name='featured_albums', blank=True)
    total_tracks = models.PositiveIntegerField(default=0)
    runtime = models.IntegerField(blank=True, null=True)  # Total album duration in minutes
    rating = models.FloatField(blank=True, null=True)
    
    # Album type
    ALBUM_TYPES = [
        ('album', 'Album'),
        ('single', 'Single'),
        ('ep', 'EP'),
        ('soundtrack', 'Soundtrack'),
        ('compilation', 'Compilation'),
        ('score', 'Score'),
        ('other', 'Other'),
    ]
    album_type = models.CharField(max_length=20, choices=ALBUM_TYPES, default='album')
    
    # Genre relations - reusing the same Genre model as movies
    genres = models.ManyToManyField(Genre, blank=True)
    
    def __str__(self):
        return f"{self.title} - {self.primary_artist.name}"
    
    @property
    def _get_content_type_id(self):
        """Cache content type ID to avoid repeated lookups."""
        if not hasattr(self, '_content_type_id'):
            self._content_type_id = ContentType.objects.get_for_model(self.__class__).id
        return self._content_type_id
    
    def get_media_persons(self, role=None):
        """Get all MediaPerson objects related to this album."""
        query = MediaPerson.objects.filter(
            content_type_id=self._get_content_type_id,
            object_id=self.id
        ).select_related('person')
        
        if role:
            query = query.filter(role=role)
        return query
    
    def minutes_to_hours(self):
        if not self.runtime:
            return "Unknown"
        hours = self.runtime // 60
        minutes = self.runtime % 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    
    @property
    def artists(self):
        """Get all artists associated with this album (primary + featured)."""
        featured = list(self.featured_artists.all())
        return [self.primary_artist] + featured

    @property
    def related_movies(self):
        """Get all related movies for this album."""
        from django.contrib.contenttypes.models import ContentType
        # Get the Movie model by its app label and model name
        movie_type = ContentType.objects.get(app_label='movies', model='movie')
        # Use the model class from the content type
        Movie = movie_type.model_class()
        return Movie.objects.filter(
            id__in=MediaAlbumRelationship.objects.filter(
                content_type=movie_type,
                album=self
            ).values_list('object_id', flat=True)
        )
    @property
    def related_tv_shows(self):
        """Get all related TV shows for this album."""
        from django.contrib.contenttypes.models import ContentType
        # Use the current TVShow model since it's already defined in this file
        tv_type = ContentType.objects.get(app_label='tvshows', model='tvshow')
        # Use the model class from the content type
        TVShow = tv_type.model_class()
        return TVShow.objects.filter(
            id__in=MediaAlbumRelationship.objects.filter(
                content_type=tv_type,
                album=self
            ).values_list('object_id', flat=True)
        )
    
class MediaAlbumRelationship(models.Model):
    """Model to define the relationship between media (movies/TV shows) and music albums."""
    # Generic foreign key to allow both movies and TV shows
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    media = GenericForeignKey('content_type', 'object_id')
    
    album = models.ForeignKey(Album, on_delete=models.CASCADE)
    
    # Relationship type
    RELATIONSHIP_TYPES = [
        ('soundtrack', 'Soundtrack'),
        ('score', 'Original Score'),
        ('inspired_by', 'Inspired By Media'),
        ('featured_in', 'Featured In Media'),
        ('promotional', 'Promotional Album'),
        ('opening_theme', 'Opening Theme'), # TV show specific
        ('ending_theme', 'Ending Theme'),   # TV show specific
        ('other', 'Other Relationship'),
    ]
    relationship_type = models.CharField(max_length=20, choices=RELATIONSHIP_TYPES, default='soundtrack')
    
    # Optional description of the relationship
    description = models.TextField(blank=True, null=True)
    
    class Meta:
        unique_together = ['content_type', 'object_id', 'album']
        verbose_name = "Media-Album Relationship"
        verbose_name_plural = "Media-Album Relationships"
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]
        
    def __str__(self):
        return f"{self.media} - {self.album.title} ({self.get_relationship_type_display()})"