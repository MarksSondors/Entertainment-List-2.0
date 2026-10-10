"""SlowRequestMiddleware records slow requests with their database cost."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from custom_auth.middleware import SlowRequestMiddleware
from custom_auth.models import SlowRequest

User = get_user_model()

LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False)
class SlowRequestMiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='slowpoke', email='slowpoke@example.com', password='pw')
        self.client.force_login(self.user)

    @override_settings(SLOW_REQUEST_MS=0.001)  # every request counts as slow
    def test_slow_request_is_recorded_with_its_queries(self):
        self.client.get(reverse('recent_activity'), {'page': 1})

        record = SlowRequest.objects.get(view_name='recent_activity')
        self.assertEqual((record.method, record.path, record.username, record.status_code),
                         ('GET', '/activity/recent/', 'slowpoke', 200))
        self.assertGreater(record.query_count, 0)
        self.assertGreaterEqual(record.repeated_query_count, 1)
        self.assertTrue(record.repeated_query)
        self.assertIn(' ms\n', record.slowest_queries)
        self.assertGreaterEqual(record.duration_ms, record.db_time_ms)

    @override_settings(SLOW_REQUEST_MS=60_000)
    def test_fast_request_is_not_recorded(self):
        self.client.get(reverse('recent_activity'))
        self.assertFalse(SlowRequest.objects.exists())

    @override_settings(SLOW_REQUEST_MS=0)
    def test_zero_disables_recording(self):
        self.client.get(reverse('recent_activity'))
        self.assertFalse(SlowRequest.objects.exists())

    @override_settings(SLOW_REQUEST_MS=0.001)
    def test_stremio_api_key_is_not_stored(self):
        self.client.get('/stremio/SECRETKEY123/manifest.json')

        record = SlowRequest.objects.get(view_name='stremio:manifest_with_config')
        self.assertNotIn('SECRETKEY123', record.path)
        self.assertEqual(record.path, '/stremio/<str:config>/manifest.json')

    @override_settings(SLOW_REQUEST_MS=0.001)
    def test_recording_failure_does_not_break_the_response(self):
        request = RequestFactory().get('/whatever/')
        middleware = SlowRequestMiddleware(lambda req: mock.Mock(status_code=200))
        with mock.patch.object(SlowRequest.objects, 'create', side_effect=RuntimeError('db down')):
            response = middleware(request)
        self.assertEqual(response.status_code, 200)
