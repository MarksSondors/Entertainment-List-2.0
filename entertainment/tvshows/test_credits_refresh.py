"""Regression tests for the cast/crew sync in ``update_single_tvshow``.

Covers two bugs: the update read TMDB's ``credits`` (latest season only) instead
of ``aggregate_credits`` (every season, which create_tvshow uses), and it never
removed MediaPerson rows TMDB stopped listing, so stale or duplicate people
piled up.
"""
from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from custom_auth.models import MediaPerson, Person
from tvshows.models import TVShow
from tvshows.tasks import update_single_tvshow


def _tmdb_payload(cast, crew=None, created_by=None, number_of_episodes=10):
    return {
        'status': 'Ended',
        'overview': 'Overview',
        'vote_average': 8.0,
        'name': 'Test Show',
        'original_name': 'Test Show',
        'number_of_episodes': number_of_episodes,
        'created_by': created_by or [],
        'aggregate_credits': {'cast': cast, 'crew': crew or []},
    }


def _cast(person, *characters, order=0):
    return {
        'id': person.tmdb_id, 'name': person.name, 'order': order,
        'roles': [{'character': c, 'episode_count': 1} for c in characters],
    }


def _crew(person, department, *jobs):
    """jobs: (job, episode_count) pairs"""
    return {
        'id': person.tmdb_id, 'name': person.name, 'department': department,
        'jobs': [{'job': job, 'episode_count': count} for job, count in jobs],
    }


class UpdateSingleTVShowCreditsTests(TestCase):
    def setUp(self):
        self.show = TVShow.objects.create(
            title='Test Show', original_title='Test Show', description='Overview',
            tmdb_id=12345, rating=8.0, status='Ended',
        )
        self.content_type = ContentType.objects.get_for_model(TVShow)
        self.s1_actor = Person.objects.create(name='Season One Actor', tmdb_id=1, is_actor=True)
        self.s2_actor = Person.objects.create(name='Season Two Actor', tmdb_id=2, is_actor=True)
        self.old_record = Person.objects.create(name='Same Name', tmdb_id=3, is_actor=True)
        self.new_record = Person.objects.create(name='Same Name', tmdb_id=4, is_actor=True)
        self.showrunner = Person.objects.create(name='Showrunner', tmdb_id=5)
        self.episode_director = Person.objects.create(name='Episode Director', tmdb_id=6)
        self.composer = Person.objects.create(name='Composer', tmdb_id=7)

    def _add_row(self, person, role='Actor', character=None, order=0):
        return MediaPerson.objects.create(
            content_type=self.content_type, object_id=self.show.id, person=person,
            role=role, character_name=character, order=order,
        )

    def _run_update(self, payload):
        with patch('tvshows.tasks.TVShowsService') as service_cls, \
                patch('tvshows.tasks.MoviesService'), patch('tvshows.tasks.async_task'):
            service_cls.return_value.get_show_details.return_value = payload
            update_single_tvshow(self.show.id, True)
        return service_cls.return_value.get_show_details

    def _actors(self):
        return list(
            MediaPerson.objects.filter(content_type=self.content_type, object_id=self.show.id, role='Actor')
            .order_by('order', 'id').values_list('person__tmdb_id', 'character_name', 'order')
        )

    def _crew_roles(self):
        return set(
            MediaPerson.objects.filter(content_type=self.content_type, object_id=self.show.id)
            .exclude(role='Actor').values_list('person__tmdb_id', 'role')
        )

    def test_requests_all_season_credits(self):
        get_show_details = self._run_update(_tmdb_payload([_cast(self.s1_actor, 'Lead')]))

        append = get_show_details.call_args.kwargs['append_to_response'].split(',')
        self.assertIn('aggregate_credits', append)
        self.assertNotIn('credits', append)

    def test_cast_from_every_season_is_kept_and_added(self):
        self._add_row(self.s1_actor, character='Lead', order=0)

        self._run_update(_tmdb_payload([
            _cast(self.s1_actor, 'Lead', order=0),
            _cast(self.s2_actor, 'Rival', 'Rival (young)', order=1),
        ]))

        self.assertEqual(self._actors(), [(1, 'Lead', 0), (2, 'Rival, Rival (young)', 1)])

    def test_credit_moved_to_another_person_removes_stale_row(self):
        self._add_row(self.s1_actor, character='Lead', order=0)
        self._add_row(self.old_record, character='Nurse', order=5)
        self._add_row(self.new_record, character='Nurse', order=5)

        self._run_update(_tmdb_payload([
            _cast(self.s1_actor, 'Lead', order=0),
            _cast(self.new_record, 'Nurse', order=4),
        ]))

        self.assertEqual(self._actors(), [(1, 'Lead', 0), (4, 'Nurse', 4)])

    def test_empty_tmdb_cast_does_not_wipe_existing_cast(self):
        self._add_row(self.s1_actor, character='Lead', order=0)

        self._run_update(_tmdb_payload([]))

        self.assertEqual(self._actors(), [(1, 'Lead', 0)])

    def test_only_series_level_directors_and_writers_are_synced(self):
        self._run_update(_tmdb_payload(
            [_cast(self.s1_actor, 'Lead')],
            crew=[
                _crew(self.showrunner, 'Writing', ('Writer', 10)),
                _crew(self.episode_director, 'Directing', ('Director', 2)),
                _crew(self.composer, 'Sound', ('Original Music Composer', 1)),
            ],
            number_of_episodes=10,
        ))

        self.assertEqual(self._crew_roles(), {(5, 'Writer'), (7, 'Original Music Composer')})
        self.showrunner.refresh_from_db()
        self.composer.refresh_from_db()
        self.assertTrue(self.showrunner.is_writer)
        self.assertTrue(self.composer.is_original_music_composer)

    def test_stale_crew_and_creators_removed_but_unsynced_roles_kept(self):
        self._add_row(self.showrunner, role='Creator')
        self._add_row(self.episode_director, role='Creator')
        self._add_row(self.composer, role='Original Music Composer')
        self._add_row(self.episode_director, role='Director')
        self._add_row(self.showrunner, role='Executive Producer')

        self._run_update(_tmdb_payload(
            [_cast(self.s1_actor, 'Lead')],
            crew=[_crew(self.composer, 'Sound', ('Original Music Composer', 10))],
            created_by=[{'id': self.showrunner.tmdb_id, 'name': self.showrunner.name}],
        ))

        self.assertEqual(self._crew_roles(), {
            (5, 'Creator'), (7, 'Original Music Composer'), (5, 'Executive Producer'),
        })
