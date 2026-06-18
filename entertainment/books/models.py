from django.db import models
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from custom_auth.models import CustomUser, Media, Person, Keyword

# Book-specific models

class BookGenre(models.Model):
    """Genre model specific to books (separate from TMDB-based movie/TV genres)."""
    name = models.CharField(max_length=100, unique=True)
    hardcover_tag_id = models.BigIntegerField(blank=True, null=True, unique=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

class BookSeries(models.Model):
    """Series that books belong to"""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    hardcover_id = models.IntegerField(unique=True, blank=True, null=True)
    
    def __str__(self):
        return self.name

class Publisher(models.Model):
    """Publisher of books"""
    name = models.CharField(max_length=255)
    hardcover_id = models.IntegerField(unique=True, blank=True, null=True)
    
    def __str__(self):
        return self.name

class BookCollection(models.Model):
    """Collection of books - similar to movie collections"""
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    image_url = models.URLField(blank=True, null=True)
    hardcover_id = models.IntegerField(unique=True, blank=True, null=True)

    def __str__(self):
        return self.name

class Book(Media):
    """Main Book model"""
    image_url = models.URLField(blank=True, null=True)
    
    # Book specifics
    subtitle = models.CharField(max_length=255, blank=True, null=True)
    isbn_10 = models.CharField(max_length=10, blank=True, null=True)
    isbn_13 = models.CharField(max_length=13, blank=True, null=True)
    pages = models.IntegerField(blank=True, null=True)
    published_date = models.DateField(blank=True, null=True)
    language = models.CharField(max_length=50, blank=True, null=True)
    
    # IDs
    hardcover_id = models.IntegerField(unique=True)
    rating = models.FloatField(blank=True, null=True)
    
    # Relationships
    authors = models.ManyToManyField(Person, related_name='books')
    genres = models.ManyToManyField(BookGenre, related_name='books', blank=True)
    publishers = models.ManyToManyField(Publisher, blank=True, related_name='books')
    keywords = models.ManyToManyField(Keyword, blank=True, related_name='books')
    
    series = models.ForeignKey(BookSeries, on_delete=models.SET_NULL, blank=True, null=True, related_name='books')
    series_position = models.FloatField(blank=True, null=True)  # Float to handle positions like 1.5 for novellas
    collection = models.ForeignKey(BookCollection, on_delete=models.SET_NULL, blank=True, null=True, related_name='books')
    
    # User related fields
    added_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, related_name='added_books', blank=True, null=True)
    date_added = models.DateTimeField(auto_now_add=True, db_index=True)
    date_updated = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.title
        
    @property
    def author_names(self):
        """Return a string of author names"""
        return ', '.join([author.name for author in self.authors.all()])


