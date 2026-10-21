"""Card-sized thumbnails for ``Genre.background_image``.

Uploaded genre backgrounds are full-resolution originals (often several MiB),
but the discover page only shows them in ~200x280 cards. This module renders
a cover-cropped WebP at 2x card size next to the original and returns its URL.

The thumbnail filename embeds a fingerprint of the source file, because nginx
serves ``/media/`` with a one-year ``immutable`` cache: replacing a genre
image must produce a new URL rather than overwrite the old one.
"""
from __future__ import annotations

import hashlib
import logging
import posixpath

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps, ImageSequence

logger = logging.getLogger(__name__)

THUMB_SIZE = (400, 560)
THUMB_DIR = 'genre_backgrounds/thumbs'
WEBP_QUALITY = 78
MAX_ANIMATION_FRAMES = 150


def _fingerprint(field) -> str | None:
    """Short hash of the source name, size and mtime; ``None`` if the file is missing."""
    storage = field.storage
    try:
        size = storage.size(field.name)
    except (OSError, NotImplementedError):
        return None
    try:
        mtime = storage.get_modified_time(field.name).timestamp()
    except (OSError, NotImplementedError, AttributeError):
        mtime = 0
    raw = f'{field.name}|{size}|{mtime}'.encode()
    return hashlib.sha1(raw).hexdigest()[:12]


def thumbnail_name(genre) -> str | None:
    field = genre.background_image
    if not field:
        return None
    fingerprint = _fingerprint(field)
    if fingerprint is None:
        return None
    return posixpath.join(THUMB_DIR, f'{genre.pk}-{fingerprint}.webp')


def _render(source) -> bytes:
    """Cover-crop the image to ``THUMB_SIZE`` and encode it as WebP, keeping animation."""
    from io import BytesIO

    with Image.open(source) as img:
        out = BytesIO()
        if getattr(img, 'is_animated', False) and img.n_frames > 1:
            frames, durations = [], []
            for index, frame in enumerate(ImageSequence.Iterator(img)):
                if index >= MAX_ANIMATION_FRAMES:
                    break
                durations.append(frame.info.get('duration', 100))
                frames.append(ImageOps.fit(frame.convert('RGBA'), THUMB_SIZE, Image.LANCZOS))
            frames[0].save(
                out, 'WEBP', save_all=True, append_images=frames[1:],
                duration=durations, loop=img.info.get('loop', 0),
                quality=WEBP_QUALITY - 8, method=4,
            )
        else:
            frame = ImageOps.exif_transpose(img)
            frame = frame.convert('RGBA' if frame.mode in ('RGBA', 'LA', 'P') else 'RGB')
            ImageOps.fit(frame, THUMB_SIZE, Image.LANCZOS).save(
                out, 'WEBP', quality=WEBP_QUALITY, method=6,
            )
        return out.getvalue()


def ensure_thumbnail(genre) -> str | None:
    """Create the thumbnail if needed and return its storage name.

    Returns ``None`` when the genre has no image or the source file is
    missing/unreadable, so callers can fall back to the plain card.
    """
    name = thumbnail_name(genre)
    if name is None:
        return None
    if default_storage.exists(name):
        return name
    try:
        with genre.background_image.open('rb') as source:
            data = _render(source)
    except Exception:  # Corrupt or unsupported upload: never break the page.
        logger.exception('Could not build thumbnail for genre %s', genre.pk)
        return None
    _delete_stale(genre, keep=name)
    return default_storage.save(name, ContentFile(data))


def thumbnail_url(genre) -> str | None:
    name = ensure_thumbnail(genre)
    return default_storage.url(name) if name else None


def _delete_stale(genre, keep: str) -> None:
    """Remove older thumbnails of this genre (from a replaced source image)."""
    try:
        _, files = default_storage.listdir(THUMB_DIR)
    except (OSError, NotImplementedError):
        return
    prefix = f'{genre.pk}-'
    for filename in files:
        path = posixpath.join(THUMB_DIR, filename)
        if filename.startswith(prefix) and path != keep:
            try:
                default_storage.delete(path)
            except OSError:
                pass
