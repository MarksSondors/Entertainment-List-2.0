"""Responses carry the signed-in username and route name for Traefik's access log."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class AccessLogTagsMiddlewareTests(TestCase):
    def test_signed_in_request_is_tagged_with_user_and_route(self):
        user = User.objects.create_user(username='tagged', email='tagged@example.com', password='pw')
        self.client.force_login(user)

        response = self.client.get(reverse('recent_activity'))

        self.assertEqual(response['X-Entlist-User'], 'tagged')
        self.assertEqual(response['X-Entlist-View'], 'recent_activity')

    def test_anonymous_request_has_route_but_no_user(self):
        response = self.client.get(reverse('healthz'))

        self.assertNotIn('X-Entlist-User', response)
        self.assertEqual(response['X-Entlist-View'], 'healthz')

    def test_unmatched_url_has_no_route(self):
        response = self.client.get('/definitely-not-a-page/')

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('X-Entlist-View', response)
