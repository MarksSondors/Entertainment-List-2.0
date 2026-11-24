"""``{{ url|tmdb:'poster' }}``: resized TMDB image URLs (see services/image_sizes.py).

Registered as a template builtin in settings, so no ``{% load %}`` is needed.
"""
from django import template

from custom_auth.services.image_sizes import tmdb_size

register = template.Library()


@register.filter(name='tmdb')
def tmdb(url, role='poster'):
    return tmdb_size(url, role)
