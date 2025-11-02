from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType

# Import these from custom_auth app
from custom_auth.models import *
from music.models import MediaAlbumRelationship
from music.models import Album
# Create your models here.

# movie collections
class Collection(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    poster = models.URLField(blank=True, null=True)
    backdrop = models.URLField(blank=True, null=True)

    tmdb_id = models.IntegerField(unique=True, blank=True, null=True)

    def __str__(self):
        return self.name
    

class Movie(Media):
    poster = models.URLField(blank=True, null=True)
    backdrop = models.URLField(blank=True, null=True)

    release_date = models.DateField(blank=True, null=True)
    
    # ids
    tmdb_id = models.IntegerField(unique=True)
    imdb_id = models.CharField(max_length=20, blank=True, null=True)

    runtime = models.IntegerField()
    rating = models.FloatField()
    trailer = models.URLField(blank=True, null=True)

    is_anime = models.BooleanField(default=False)

    status = models.CharField(max_length=50, blank=True, null=True)  # e.g., "Released", "In Production", "Post Production"

    # foreign keys
    genres = models.ManyToManyField(Genre)
    countries = models.ManyToManyField(Country)
    keywords = models.ManyToManyField(Keyword)
    production_companies = models.ManyToManyField(ProductionCompany)

    collection = models.ForeignKey(Collection, on_delete=models.SET_NULL, blank=True, null=True, related_name='movies')

    # user related fields
    added_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, related_name='added_movies', blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True)
    date_updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title
    
    def get_absolute_url(self):
        """Get the absolute URL for the movie page."""
        return f"/movies/{self.tmdb_id}/"
    
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
    def crew(self):
        """Get all crew members of this movie."""
        return MediaPerson.objects.select_related('person').filter(
            content_type_id=self._get_content_type_id,
            object_id=self.id
        ).exclude(role="Actor").order_by('order')

    @property
    def composers(self):
        """Get all composers of this movie."""
        return self._get_persons_by_role({'mediaperson__role': "Original Music Composer"})
    
    @property
    def soundtracks(self):
        """Get soundtrack albums for this movie."""
        return self.related_albums.filter(mediaalbumrelationship__relationship_type='soundtrack')

    @property
    def scores(self):
        """Get score albums for this movie."""
        return self.related_albums.filter(mediaalbumrelationship__relationship_type='score')

    def get_related_albums(self, relationship_type=None):
        """Get albums related to this movie with optional relationship type filter."""
        from django.contrib.contenttypes.models import ContentType
        movie_type = ContentType.objects.get_for_model(self.__class__)
        query = MediaAlbumRelationship.objects.filter(
            content_type=movie_type,
            object_id=self.id
        )
        if relationship_type:
            query = query.filter(relationship_type=relationship_type)
        return Album.objects.filter(
            id__in=query.values_list('album_id', flat=True)
        ).select_related('primary_artist')

    @property
    def related_albums(self):
        """Get all related albums for this movie."""
        from django.contrib.contenttypes.models import ContentType
        movie_type = ContentType.objects.get_for_model(self.__class__)
        return Album.objects.filter(
            id__in=MediaAlbumRelationship.objects.filter(
                content_type=movie_type,
                object_id=self.id
            ).values_list('album_id', flat=True)
        )

    def get_crew(self):
        """
        Returns crew members with their combined roles.
        Example: {'Person1': ['Director', 'Writer'], 'Person2': ['Novel']}
        """
        from django.contrib.contenttypes.models import ContentType
        movie_content_type = ContentType.objects.get_for_model(self)
        
        # Get all crew members (not actors)
        crew_members = MediaPerson.objects.filter(
            content_type=movie_content_type,
            object_id=self.id
        ).exclude(role="Actor").select_related('person')
        
        # Group by person and combine roles
        crew_by_person = {}
        for member in crew_members:
            if member.person not in crew_by_person:
                crew_by_person[member.person] = []
            crew_by_person[member.person].append(member.role)
        
        return crew_by_person


class MovieOfWeekPick(models.Model):
    movie = models.ForeignKey(Movie, on_delete=models.CASCADE)
    suggested_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='movie_week_suggestions')
    suggestion_reason = models.TextField()
    
    # When this movie was/will be featured
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    
    # Status: 'queued', 'active', 'completed'
    status = models.CharField(max_length=20, default='queued')
    
    # Track who has watched it
    watched_by = models.ManyToManyField(CustomUser, related_name='movies_of_week_watched', blank=True)
    
    date_created = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-start_date', 'date_created']
    
    def __str__(self):
        return f"{self.movie.title} (suggested by {self.suggested_by.username})"
    
    @property
    def watched_count(self):
        return self.watched_by.count()
