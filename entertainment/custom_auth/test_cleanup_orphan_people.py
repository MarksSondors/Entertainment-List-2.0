"""Tests for the ``cleanup_orphan_people`` command (weekly orphaned-Person cleanup)."""
from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from books.models import Book
from custom_auth.models import MediaPerson, Person
from movies.models import Movie
from music.models import Album


class CleanupOrphanPeopleTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            title='Live Movie', original_title='Live Movie', tmdb_id=1, runtime=100, rating=7.0,
        )
        self.deleted_movie = Movie.objects.create(
            title='Deleted Movie', original_title='Deleted Movie', tmdb_id=2, runtime=100, rating=7.0,
        )
        self.movie_ct = ContentType.objects.get_for_model(Movie)

        self.actor = Person.objects.create(name='Credited Actor')
        self.author = Person.objects.create(name='Book Author')
        self.artist = Person.objects.create(name='Album Artist')
        self.featured = Person.objects.create(name='Featured Artist')
        self.unlinked = Person.objects.create(name='Unlinked')
        self.only_deleted = Person.objects.create(name='Only In Deleted Movie')
        self.both = Person.objects.create(name='In Live And Deleted Movie')

        self._credit(self.actor, self.movie)
        self._credit(self.both, self.movie)
        self.dangling = [
            self._credit(self.only_deleted, self.deleted_movie),
            self._credit(self.both, self.deleted_movie),
        ]
        book = Book.objects.create(title='Book', original_title='Book', hardcover_id=1)
        book.authors.add(self.author)
        album = Album.objects.create(title='Album', original_title='Album', primary_artist=self.artist)
        album.featured_artists.add(self.featured)

        # Deleting a movie leaves its MediaPerson rows behind (generic relation)
        self.deleted_movie.delete()

    def _credit(self, person, movie):
        return MediaPerson.objects.create(
            content_type=self.movie_ct, object_id=movie.id, person=person, role='Actor',
        )

    def _run(self, *args):
        out = StringIO()
        call_command('cleanup_orphan_people', *args, stdout=out)
        return out.getvalue()

    def test_removes_orphans_and_dangling_credits_only(self):
        output = self._run()

        self.assertEqual(
            set(Person.objects.values_list('name', flat=True)),
            {'Credited Actor', 'Book Author', 'Album Artist', 'Featured Artist', 'In Live And Deleted Movie'},
        )
        self.assertFalse(MediaPerson.objects.filter(id__in=[mp.id for mp in self.dangling]).exists())
        self.assertEqual(MediaPerson.objects.filter(person=self.both).count(), 1)
        self.assertIn('Removed 2 credits for deleted media and 2 orphaned people', output)

    def test_dry_run_deletes_nothing(self):
        output = self._run('--dry-run')

        self.assertEqual(Person.objects.count(), 7)
        self.assertEqual(MediaPerson.objects.count(), 4)
        self.assertIn('Would remove 2 credits for deleted media and 2 orphaned people', output)
