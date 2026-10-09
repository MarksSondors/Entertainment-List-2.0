from django.http import QueryDict
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from entertainment.query_params import query_float, query_int


class QueryParamParsingTests(SimpleTestCase):
    def test_valid_int_is_parsed(self):
        self.assertEqual(query_int(QueryDict('limit=7'), 'limit', 10), 7)

    def test_missing_or_malformed_int_falls_back_to_default(self):
        self.assertEqual(query_int(QueryDict(''), 'limit', 10), 10)
        self.assertEqual(query_int(QueryDict('limit=abc'), 'limit', 10), 10)
        self.assertEqual(query_int(QueryDict('limit=1.5'), 'limit', 10), 10)

    def test_int_is_clamped(self):
        self.assertEqual(query_int(QueryDict('limit=-5'), 'limit', 10, minimum=1, maximum=50), 1)
        self.assertEqual(query_int(QueryDict('limit=99999'), 'limit', 10, minimum=1, maximum=50), 50)

    def test_float_rejects_garbage_and_non_finite_values(self):
        self.assertEqual(query_float(QueryDict('r=7.5'), 'r', 0.0), 7.5)
        self.assertEqual(query_float(QueryDict('r=nope'), 'r', 0.0), 0.0)
        self.assertEqual(query_float(QueryDict('r=nan'), 'r', 1.0), 1.0)
        self.assertEqual(query_float(QueryDict('r=inf'), 'r', 1.0), 1.0)
        self.assertEqual(query_float(QueryDict('r=42'), 'r', 0.0, minimum=0.0, maximum=10.0), 10.0)


LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=LOCMEM_CACHE, SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['testserver', 'example.test'])
class SiteDomainTests(TestCase):
    def test_robots_txt_points_sitemap_at_requesting_host(self):
        response = self.client.get('/robots.txt', HTTP_HOST='example.test')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
        body = response.content.decode()
        self.assertIn('Disallow: /admin/', body)
        self.assertIn('Sitemap: http://example.test/sitemap.xml', body)

    @override_settings(SITE_DOMAIN='brand.example')
    def test_brand_comes_from_site_domain_setting(self):
        response = self.client.get(reverse('login_page'))
        self.assertContains(response, 'brand.example')


@override_settings(CACHES=LOCMEM_CACHE)
class HealthzTests(TestCase):
    def test_healthz_reports_ok(self):
        response = self.client.get(reverse('healthz'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertIn('task_clusters', response.json())

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r'^healthz$'])
    def test_healthz_is_not_redirected_to_https(self):
        # Container healthchecks call gunicorn over plain http
        self.assertEqual(self.client.get(reverse('healthz')).status_code, 200)
        other = self.client.get('/some-other-path/')
        self.assertEqual(other.status_code, 301)
        self.assertTrue(other['Location'].startswith('https://'))
