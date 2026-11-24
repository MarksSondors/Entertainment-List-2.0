"""Small thumbnails for ``CustomUser.profile_picture``.

Uploaded avatars are full-size originals (often 1000+ px and several hundred
KB), but the site shows them at 28-120 px. This module renders a WebP whose
longest side is ``THUMB_MAX`` px next to the original and returns its URL. The
original is never modified, so the profile zoom view can still show it.

The thumbnail filename embeds a fingerprint of the source file, because nginx
serves ``/media/`` with a one-year ``immutable`` cache: replacing an avatar
must produce a new URL rather than overwrite the old one.
"""
from __future__ import annotations

import hashlib
import logging
import posixpath
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageOps, ImageSequence

logger = logging.getLogger(__name__)

THUMB_MAX = 256
THUMB_DIR = 'profile_pictures/thumbs'
WEBP_QUALITY = 80
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


def thumbnail_name(user) -> str | None:
    field = user.profile_picture
    if not field:
        return None
    fingerprint = _fingerprint(field)
    if fingerprint is None:
        return None
    return posixpath.join(THUMB_DIR, f'{user.pk}-{fingerprint}.webp')


def _shrink(frame: Image.Image) -> Image.Image:
    frame = frame.convert('RGBA' if frame.mode in ('RGBA', 'LA', 'P') else 'RGB')
    frame.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)  # keeps aspect ratio, never upscales
    return frame


def _render(source) -> bytes:
    with Image.open(source) as img:
        out = BytesIO()
        if getattr(img, 'is_animated', False) and img.n_frames > 1:
            frames, durations = [], []
            for index, frame in enumerate(ImageSequence.Iterator(img)):
                if index >= MAX_ANIMATION_FRAMES:
                    break
                durations.append(frame.info.get('duration', 100))
                frames.append(_shrink(frame.copy()))
            frames[0].save(
                out, 'WEBP', save_all=True, append_images=frames[1:],
                duration=durations, loop=img.info.get('loop', 0),
                quality=WEBP_QUALITY - 8, method=4,
            )
        else:
            _shrink(ImageOps.exif_transpose(img)).save(out, 'WEBP', quality=WEBP_QUALITY, method=6)
        return out.getvalue()


def ensure_thumbnail(user) -> str | None:
    """Create the thumbnail if needed and return its storage name.

    Returns ``None`` when the user has no avatar or the source file is
    missing/unreadable, so callers can fall back to the original URL.
    """
    name = thumbnail_name(user)
    if name is None:
        return None
    if default_storage.exists(name):
        return name
    try:
        with user.profile_picture.open('rb') as source:
            data = _render(source)
    except Exception:  # Corrupt or unsupported upload: never break the page.
        logger.exception('Could not build avatar thumbnail for user %s', user.pk)
        return None
    _delete_stale(user, keep=name)
    return default_storage.save(name, ContentFile(data))


def thumbnail_url(user) -> str | None:
    name = ensure_thumbnail(user)
    return default_storage.url(name) if name else None


def _delete_stale(user, keep: str) -> None:
    """Remove older thumbnails of this user (from a replaced avatar)."""
    try:
        _, files = default_storage.listdir(THUMB_DIR)
    except (OSError, NotImplementedError):
        return
    prefix = f'{user.pk}-'
    for filename in files:
        path = posixpath.join(THUMB_DIR, filename)
        if filename.startswith(prefix) and path != keep:
            try:
                default_storage.delete(path)
            except OSError:
                pass
