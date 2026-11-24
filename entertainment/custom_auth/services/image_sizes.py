"""Pick a TMDB image size that fits where the image is shown.

Every TMDB URL is stored at ``/t/p/original/`` (posters ~0.7 MB each, cast
photos up to several MB), but cards display them at a few hundred pixels.
TMDB serves the same path at fixed widths, so resizing is a URL rewrite:
``/t/p/original/abc.jpg`` -> ``/t/p/w342/abc.jpg``. Non-TMDB URLs (Hardcover,
RAWG, SteamGridDB, local media) pass through unchanged.

Sizes assume the installed PWA on a phone (~390 CSS px wide, 3x screens):
each role is roughly the largest device-pixel width it is drawn at.
Use the named roles rather than raw sizes so every page picks the same one.
Click-to-zoom views keep the original URL.
"""
from __future__ import annotations

SIZES = {
    'poster': 'w342',      # grid cards, rows, search results
    'poster_lg': 'w500',   # the main poster on a detail page
    'backdrop': 'w1280',   # full-page background
    'profile': 'w185',     # cast / person thumbnails
    'profile_lg': 'h632',  # the main photo on a person page
    'still': 'w300',       # episode stills
    'logo': 'w154',        # production company / network logos
}

_ORIGINAL = '/t/p/original/'


def tmdb_size(url, role_or_size: str = 'poster'):
    """Return ``url`` resized for ``role_or_size`` (a key of SIZES or a TMDB size like 'w185')."""
    if not url or not isinstance(url, str) or 'image.tmdb.org' not in url or _ORIGINAL not in url:
        return url
    size = SIZES.get(role_or_size, role_or_size)
    return url.replace(_ORIGINAL, f'/t/p/{size}/', 1)
