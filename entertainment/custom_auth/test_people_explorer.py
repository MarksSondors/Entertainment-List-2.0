"""Tests for the People Explorer service and its JSON endpoint."""
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from books.models import Book
from custom_auth.models import CustomUser, MediaPerson, Person, Review, Watchlist
from custom_auth.services import people_explorer as pe
from movies.models import Movie
from music.models import Album


class PeopleExplorerTests(TestCase):
    def setUp(self):
        cache.clear()
        pe.clear_local_index()
        self.addCleanup(pe.clear_local_index)

        self.me = CustomUser.objects.create_user(username='me', email='me@example.com', password='pw')
        self.other = CustomUser.objects.create_user(username='other', email='other@example.com', password='pw')
        self.movie_ct = ContentType.objects.get_for_model(Movie)

        self.great = self._movie('Great Film', 1)
        self.good = self._movie('Good Film', 2)
        self.bad = self._movie('Bad Film', 3)
        self.unseen = self._movie('Watchlisted Film', 4)

        # The director only directed "Bad Film" but acted in "Great Film":
        # their directing score must ignore the acting credit.
        self.hybrid = Person.objects.create(name='Hybrid Person')
        self.solo = Person.objects.create(name='Pedro Almodóvar')
        self.prolific = Person.objects.create(name='Prolific Director')
        self._credit(self.hybrid, self.bad, 'Director')
        self._credit(self.hybrid, self.great, 'Actor')
        self._credit(self.solo, self.great, 'Director')
        self._credit(self.prolific, self.good, 'Director')
        self._credit(self.prolific, self.great, 'Writer')
        self._credit(self.prolific, self.unseen, 'Director')

        # A one-off 10/10 should not beat a director with several strong films.
        self.one_hit = self._movie('One Hit', 5)
        self.wonder = Person.objects.create(name='One Hit Wonder')
        self._credit(self.wonder, self.one_hit, 'Director')
        self.steady = Person.objects.create(name='Steady Hand')
        self.steady_films = [self._movie(f'Steady {i}', 10 + i) for i in range(5)]
        for film in self.steady_films:
            self._credit(self.steady, film, 'Director')
            self._review(self.other, film, 9.5)
        self._review(self.other, self.one_hit, 10)

        self._review(self.me, self.great, 9)
        self._review(self.other, self.great, 7)
        self._review(self.me, self.good, 7)
        self._review(self.other, self.bad, 3)
        Watchlist.objects.create(user=self.me, content_type=self.movie_ct, object_id=self.unseen.id)

        self.artist = Person.objects.create(name='Album Artist')
        self.featured = Person.objects.create(name='Guest Singer')
        self.album = Album.objects.create(title='Record', original_title='Record', primary_artist=self.artist)
        self.album.featured_artists.add(self.featured)
        self._review(self.me, self.album, 8)

        self.writer = Person.objects.create(name='Novel Writer')
        self.book = Book.objects.create(title='A Book', original_title='A Book', hardcover_id=99)
        MediaPerson.objects.create(
            content_type=ContentType.objects.get_for_model(Book), object_id=self.book.id,
            person=self.writer, role='Author',
        )
        Watchlist.objects.create(
            user=self.me, content_type=ContentType.objects.get_for_model(Book), object_id=self.book.id,
        )

    def _movie(self, title, tmdb_id):
        return Movie.objects.create(title=title, original_title=title, tmdb_id=tmdb_id, runtime=100, rating=7.0)

    def _credit(self, person, movie, role):
        return MediaPerson.objects.create(content_type=self.movie_ct, object_id=movie.id, person=person, role=role)

    def _review(self, user, obj, rating):
        return Review.objects.create(
            user=user, content_type=ContentType.objects.get_for_model(obj), object_id=obj.id, rating=rating,
        )

    def _listing(self, **kwargs):
        kwargs.setdefault('per_page', 100)
        return pe.build_listing(self.me, **kwargs)

    @staticmethod
    def _by_name(data):
        return {p['name']: p for p in data['people']}

    def test_ratings_are_scoped_to_the_role(self):
        directors = self._by_name(self._listing(category='directors'))
        self.assertEqual(directors['Hybrid Person']['avg_rating'], 3.0)
        self.assertEqual(directors['Hybrid Person']['works_rated'], 1)
        self.assertEqual(directors['Pedro Almodóvar']['avg_rating'], 8.0)
        self.assertEqual(directors['Prolific Director']['works_rated'], 1)
        self.assertEqual(directors['Prolific Director']['avg_rating'], 7.0)

        actors = self._by_name(self._listing(category='actors'))
        self.assertEqual(set(actors), {'Hybrid Person'})
        self.assertEqual(actors['Hybrid Person']['avg_rating'], 8.0)

        everyone = self._by_name(self._listing(category='all'))
        self.assertEqual(everyone['Hybrid Person']['works_rated'], 2)
        self.assertEqual(everyone['Hybrid Person']['roles'], ['Director', 'Actor'])

    def test_weighted_score_beats_single_perfect_rating(self):
        names = [p['name'] for p in self._listing(category='directors')['people']]
        self.assertLess(names.index('Steady Hand'), names.index('One Hit Wonder'))

        plain = [p['name'] for p in self._listing(category='directors', sort='rating')['people']]
        self.assertEqual(plain[0], 'One Hit Wonder')

    def test_personal_stats_and_affinity(self):
        person = self._by_name(self._listing(category='directors'))['Pedro Almodóvar']
        self.assertEqual(person['my_avg'], 9.0)
        self.assertEqual(person['my_count'], 1)
        # I gave 9, the only other reviewer gave 7.
        self.assertEqual(person['affinity'], 2.0)
        # Nobody else rated "Good Film", so there is nothing to compare against.
        self.assertIsNone(self._by_name(self._listing(category='directors'))['Prolific Director']['affinity'])

    def test_known_for_details(self):
        person = self._by_name(self._listing(category='directors'))['Prolific Director']
        titles = {item['title']: item for item in person['known_for']}
        self.assertEqual(set(titles), {'Good Film', 'Watchlisted Film'})
        self.assertEqual(titles['Good Film']['my_rating'], 7.0)
        self.assertEqual(titles['Good Film']['url'], self.good.get_absolute_url())
        self.assertTrue(titles['Watchlisted Film']['on_watchlist'])

    def test_scopes(self):
        mine = {p['name'] for p in self._listing(category='directors', scope='mine')['people']}
        self.assertEqual(mine, {'Pedro Almodóvar', 'Prolific Director'})

        watchlist = self._by_name(self._listing(category='directors', scope='watchlist'))
        self.assertEqual(set(watchlist), {'Prolific Director'})
        self.assertEqual(watchlist['Prolific Director']['watchlist_count'], 1)

        authors = self._by_name(self._listing(category='authors', scope='watchlist'))
        self.assertEqual(set(authors), {'Novel Writer'})
        self.assertEqual(
            authors['Novel Writer']['known_for'][0]['url'],
            reverse('books:book_page', kwargs={'book_id': self.book.id}),
        )

    def test_unrated_people_need_scope(self):
        # Watchlist-only works don't qualify anyone for the "everyone" scope.
        self.assertNotIn('Novel Writer', self._by_name(self._listing(category='all')))

    def test_min_works(self):
        names = {p['name'] for p in self._listing(category='directors', min_works=3)['people']}
        self.assertEqual(names, {'Steady Hand'})

    def test_album_artists_are_musicians(self):
        musicians = self._by_name(self._listing(category='musicians'))
        self.assertEqual(set(musicians), {'Album Artist', 'Guest Singer'})
        self.assertEqual(musicians['Album Artist']['avg_rating'], 8.0)

    def test_accent_insensitive_search_and_counts(self):
        data = self._listing(category='directors', search='almodovar')
        self.assertEqual([p['name'] for p in data['people']], ['Pedro Almodóvar'])
        self.assertEqual(data['counts']['directors'], 1)
        self.assertEqual(data['counts']['actors'], 0)
        self.assertEqual(data['counts']['all'], 1)

    def test_counts_match_totals(self):
        data = self._listing(category='directors')
        for category in ('all', 'directors', 'actors', 'writers', 'musicians'):
            total = self._listing(category=category)['pagination']['total']
            self.assertEqual(data['counts'][category], total, category)

    def test_pagination(self):
        first = self._listing(category='all', sort='name', per_page=2)
        second = self._listing(category='all', sort='name', per_page=2, page=2)
        self.assertTrue(first['pagination']['has_more'])
        names = [p['name'] for p in first['people'] + second['people']]
        self.assertEqual(names, sorted(names, key=str.casefold))
        self.assertEqual(len(set(names)), 4)

    def test_index_refreshes_when_new_media_is_reviewed(self):
        self._listing(category='directors')
        newcomer = Person.objects.create(name='Newcomer')
        film = self._movie('Fresh', 50)
        self._credit(newcomer, film, 'Director')
        self._review(self.other, film, 6)
        self.assertIn('Newcomer', self._by_name(self._listing(category='directors')))

    def test_api_endpoint(self):
        self.client.force_login(self.me)
        response = self.client.get(reverse('api_people_by_category'), {
            'category': 'other_creators', 'scope': 'watchlist', 'sort': 'rating-desc', 'per_page': 'abc',
        }, secure=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['category'], 'authors')
        self.assertEqual(data['sort'], 'score')
        self.assertEqual([p['name'] for p in data['people']], ['Novel Writer'])

    def test_page_renders(self):
        self.client.force_login(self.me)
        response = self.client.get(reverse('browse_by_people'), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-category="musicians"')
        self.assertContains(response, 'data-scope="watchlist"')
