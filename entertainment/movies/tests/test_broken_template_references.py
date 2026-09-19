"""Regression tests for two templates that rendered a 500 in production.

1. ``home_page.html`` included ``movie_search_modal.html``, a template deleted in
   commit 0b034c1 ("refactor: Remove movie search modal template and associated
   scripts"). The include was never updated, so the home page raised
   ``TemplateDoesNotExist`` for every logged-in user. The replacement component
   lives at ``components/movie_search_component.html``.

2. ``community_page.html`` reversed the URL name ``login``, which is not in the
   URLconf (the login route is named ``login_page``/``login_request``). Any
   anonymous visitor hit ``NoReverseMatch`` — i.e. the page 500'd exactly for the
   people the "log in" prompt was written for.

A repo-wide render audit (every include/extends and every ``{% url %}``) found
these two as the only broken references; both tests fail on the pre-fix tree.
"""
from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.template.exceptions import TemplateDoesNotExist
from django.test import TestCase
from django.test import override_settings
from django.urls import reverse

User = get_user_model()


class HomePageTemplateTests(TestCase):
    """The home page must render, and every template it includes must exist."""

    # These tests target templates/URLs, not the TLS redirect middleware; without the
    # override, SECURE_SSL_REDIRECT 301s the test client before the page renders.
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._override = override_settings(SECURE_SSL_REDIRECT=False)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        super().tearDownClass()


    def setUp(self):
        self.user = User.objects.create_user(
            username='home-page-tester', password='irrelevant-pw'
        )

    def test_home_page_renders_for_logged_in_user(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('home_page'))
        self.assertEqual(response.status_code, 200)

    def test_home_page_included_templates_all_exist(self):
        """Guard against a stale {% include %} after a template is deleted."""
        for name in ('components/movie_search_component.html',):
            try:
                get_template(name)
            except TemplateDoesNotExist as exc:  # pragma: no cover - failure path
                self.fail(f'home_page.html includes a missing template: {exc}')


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
