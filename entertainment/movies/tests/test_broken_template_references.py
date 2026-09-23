"""Regression test for a template that rendered a 500 in production.

``community_page.html`` reversed the URL name ``login``, which is not in the
URLconf (the login route is named ``login_page``/``login_request``). Any
anonymous visitor hit ``NoReverseMatch`` — i.e. the page 500'd exactly for the
people the "log in" prompt was written for.
"""
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse


class CommunityPageLoginLinkTests(TestCase):
    """The anonymous "please log in" prompt must not reverse a non-existent name."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._override = override_settings(SECURE_SSL_REDIRECT=False)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        super().tearDownClass()


    def test_anonymous_community_page_renders(self):
        response = self.client.get(reverse('community_page'))
        self.assertEqual(response.status_code, 200)

    def test_login_url_name_used_by_community_page_exists(self):
        self.assertEqual(reverse('login_page'), '/')
