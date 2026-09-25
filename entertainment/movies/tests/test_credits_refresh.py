"""Regression tests for the cast/crew sync in ``update_single_movie``.

Covers the bug where the update only ever added or edited MediaPerson rows, so
when TMDB moved a credit to a different person record (Drive My Car: two
"Saki Suzuki" entries) the stale row stayed and the actor showed up twice. Also
covers crew members picking up an extra role, which used to overwrite their
existing role instead of adding one.
"""
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from custom_auth.models import MediaPerson, Person
from movies.models import Movie
from movies.tasks import update_single_movie


def _tmdb_payload(cast, crew=None):
    return {
        'status': 'Released',
        'overview': 'Overview',
        'vote_average': 7.5,
        'runtime': 179,
        'title': 'Drive My Car',
        'original_title': 'Drive My Car',
        'release_date': '2021-08-20',
        'credits': {'cast': cast, 'crew': crew or []},
    }


def _cast(person, character='', order=0):
    return {'id': person.tmdb_id, 'name': person.name, 'character': character, 'order': order}


def _crew(person, job, department):
    return {'id': person.tmdb_id, 'name': person.name, 'job': job, 'department': department}


class UpdateSingleMovieCreditsTests(TestCase):
    def setUp(self):
        self.movie = Movie.objects.create(
            title='Drive My Car',
            original_title='Drive My Car',
            description='Overview',
            tmdb_id=758866,
            runtime=179,
            rating=7.5,
            status='Released',
        )
        self.content_type = ContentType.objects.get_for_model(Movie)
        self.lead = Person.objects.create(name='Hidetoshi Nishijima', tmdb_id=1, is_actor=True)
        self.old_saki = Person.objects.create(name='Saki Suzuki', tmdb_id=3101454, is_actor=True)
        self.new_saki = Person.objects.create(name='Saki Suzuki', tmdb_id=3953194, is_actor=True)
        self.director = Person.objects.create(name='Ryusuke Hamaguchi', tmdb_id=2, is_director=True)

    def _add_row(self, person, role='Actor', character=None, order=0):
        return MediaPerson.objects.create(
            content_type=self.content_type, object_id=self.movie.id, person=person,
            role=role, character_name=character, order=order,
        )

    def _run_update(self, payload):
        with patch('movies.tasks.MoviesService') as service_cls, patch('movies.tasks.async_task'):
            service_cls.return_value.get_movie_details.return_value = payload
            update_single_movie(self.movie.id, True)

    def _rows(self, role='Actor'):
        return list(
            MediaPerson.objects.filter(content_type=self.content_type, object_id=self.movie.id, role=role)
            .order_by('order', 'id').values_list('person__tmdb_id', 'character_name', 'order')
        )

    def test_credit_moved_to_another_person_removes_stale_row(self):
        self._add_row(self.lead, character='Yusuke Kafuku', order=0)
        self._add_row(self.old_saki, character='', order=20)
        self._add_row(self.new_saki, character='', order=20)

        self._run_update(_tmdb_payload([
            _cast(self.lead, 'Yusuke Kafuku', 0),
            _cast(self.new_saki, '', 19),
        ]))

        self.assertEqual(self._rows(), [(1, 'Yusuke Kafuku', 0), (3953194, '', 19)])

    def test_person_credited_for_two_characters_keeps_both_rows(self):
        self._add_row(self.lead, character='Tyra (voice)', order=0)
        self._add_row(self.lead, character='Taira (voice)', order=1)

        self._run_update(_tmdb_payload([
            _cast(self.lead, 'Tyra (voice)', 0),
            _cast(self.lead, 'Taira (voice)', 1),
        ]))

        self.assertEqual(self._rows(), [(1, 'Tyra (voice)', 0), (1, 'Taira (voice)', 1)])

    def test_leftover_duplicate_row_is_removed(self):
        self._add_row(self.lead, character='Yusuke Kafuku', order=0)
        self._add_row(self.lead, character='Yusuke Kafuku', order=0)

        self._run_update(_tmdb_payload([_cast(self.lead, 'Yusuke Kafuku', 0)]))

        self.assertEqual(self._rows(), [(1, 'Yusuke Kafuku', 0)])

    def test_empty_tmdb_cast_does_not_wipe_existing_cast(self):
        self._add_row(self.lead, character='Yusuke Kafuku', order=0)

        self._run_update(_tmdb_payload([]))

        self.assertEqual(self._rows(), [(1, 'Yusuke Kafuku', 0)])

    def test_extra_crew_role_is_added_not_overwritten(self):
        self._add_row(self.director, role='Director')

        self._run_update(_tmdb_payload(
            [_cast(self.lead, 'Yusuke Kafuku', 0)],
            [_crew(self.director, 'Director', 'Directing'), _crew(self.director, 'Screenplay', 'Writing')],
        ))

        roles = set(
            MediaPerson.objects.filter(content_type=self.content_type, object_id=self.movie.id, person=self.director)
            .values_list('role', flat=True)
        )
        self.assertEqual(roles, {'Director', 'Screenplay'})

    def test_dropped_crew_role_is_removed_but_unsynced_roles_are_kept(self):
        self._add_row(self.director, role='Director')
        self._add_row(self.director, role='Writer')
        self._add_row(self.director, role='Executive Producer')

        self._run_update(_tmdb_payload(
            [_cast(self.lead, 'Yusuke Kafuku', 0)],
            [_crew(self.director, 'Director', 'Directing')],
        ))

        roles = set(
            MediaPerson.objects.filter(content_type=self.content_type, object_id=self.movie.id, person=self.director)
            .values_list('role', flat=True)
        )
        self.assertEqual(roles, {'Director', 'Executive Producer'})
