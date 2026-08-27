"""Poster overlay rendering for Stremio catalog grids (rating chip + status chip)."""
import hashlib
import io
from datetime import date, timedelta
from functools import lru_cache

import requests
from django.contrib.contenttypes.models import ContentType
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.db.models import Avg, Count
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from custom_auth.models import Review

from .formatters import get_poster_url

SOURCE_IMAGE_CACHE_TTL = 7 * 24 * 3600  # TMDB posters rarely change, cache long
RENDERED_POSTER_CACHE_TTL = 3600  # matches agreed staleness tolerance for ratings/next-episode
FETCH_TIMEOUT = 4
JPEG_QUALITY = 95

ACCENT = (0, 112, 216)  # brand blue, sampled from the site logo
WHITE = (255, 255, 255)
GLASS_TINT = (18, 18, 20, 150)
GLASS_BLUR_RADIUS = 6
CHIP_CORNER_RADIUS_RATIO = 0.28  # rounded rectangle, not a full pill


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.ImageFont:
    """A handful of distinct sizes are ever requested; avoid re-parsing the bitmap font each render."""
    return ImageFont.load_default(size=size)


@lru_cache(maxsize=8)
def _logo_icon(size: int) -> Image.Image:
    """Site logo, resized once per requested size and reused as the rating chip's icon."""
    path = finders.find('images/logo.png')
    assert isinstance(path, str), 'site logo static asset is missing'
    logo = Image.open(path).convert('RGBA')
    return logo.resize((size, size), Image.Resampling.LANCZOS)


def _encode_output(image: Image.Image) -> bytes:
    if image.mode != 'RGB':
        image = image.convert('RGB')
    buf = io.BytesIO()
    # WebP's lossy encoder ignores Pillow's `subsampling` kwarg entirely (still 4:2:0 under the
    # hood), which is what was blurring the chip/text/logo edges; JPEG actually honors it
    image.save(buf, format='JPEG', quality=JPEG_QUALITY, subsampling=0)
    return buf.getvalue()


def get_cached_poster(media, media_type: str, user, ctx: str | None) -> bytes | None:
    """Get-or-render the final per-user poster; shared by the live view and background warm tasks."""
    cache_key = f"stremio:posterimg:{media_type}:{media.id}:{user.id}:{ctx or ''}"
    image_bytes = cache.get(cache_key)
    if image_bytes is not None:
        return image_bytes

    try:
        image_bytes = render_catalog_poster(media, media_type, user, ctx)
    except Exception:
        image_bytes = None
    if image_bytes:
        cache.set(cache_key, image_bytes, RENDERED_POSTER_CACHE_TTL)
    return image_bytes


def render_catalog_poster(media, media_type: str, user, ctx: str | None) -> bytes | None:
    """Build the composited poster JPEG, or None if there's nothing to draw / source is unavailable."""
    poster_url = get_poster_url(media)
    if not poster_url:
        return None

    rating_text = _rating_badge(media)
    banner = _movie_banner(media) if media_type == 'movie' else _series_banner(media, user, ctx)

    if not rating_text and not banner:
        # nothing to draw at all: skip Pillow entirely and hand back the original bytes
        return _fetch_source_image(poster_url)

    base_bytes = _fetch_source_image(poster_url)
    if not base_bytes:
        return None

    if rating_text:
        # rating chip is identical for every viewer of this title, so it's rendered once (blur
        # included) and shared across users instead of redone per-request
        base_bytes = _render_rating_base(media, media_type, base_bytes, rating_text)
        if base_bytes is None:
            return None

    if not banner:
        try:
            image = Image.open(io.BytesIO(base_bytes))
        except Exception:
            return None
        return _encode_output(image)

    try:
        image = Image.open(io.BytesIO(base_bytes)).convert('RGBA')
    except Exception:
        return None

    width, height = image.size
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _draw_context_chip(image, draw, width, height, banner)

    composited = Image.alpha_composite(image, overlay)
    return _encode_output(composited)


def _render_rating_base(media, media_type: str, source_bytes: bytes, rating_text: str) -> bytes | None:
    """Poster + rating chip only, cached per media item (not per user) so the blur runs once."""
    cache_key = f"stremio:posterbase:{media_type}:{media.id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        image = Image.open(io.BytesIO(source_bytes)).convert('RGBA')
    except Exception:
        return None

    width, _height = image.size
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    _draw_rating_chip(image, overlay, width, rating_text)
    composited = Image.alpha_composite(image, overlay)

    buf = io.BytesIO()
    composited.save(buf, format='PNG')  # lossless intermediate; re-decoded once per user on top
    base_bytes = buf.getvalue()
    cache.set(cache_key, base_bytes, RENDERED_POSTER_CACHE_TTL)
    return base_bytes


def _fetch_source_image(url: str) -> bytes | None:
    """Download the raw poster bytes, cached long-term to avoid re-hitting TMDB."""
    cache_key = f"stremio:posrc:{hashlib.sha1(url.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        response = requests.get(url, timeout=FETCH_TIMEOUT)
        response.raise_for_status()
        content = response.content
    except requests.RequestException:
        return None
    cache.set(cache_key, content, SOURCE_IMAGE_CACHE_TTL)
    return content


def _rating_badge(media) -> str | None:
    """Community average rating text; omitted entirely when nobody has rated it yet."""
    ct = ContentType.objects.get_for_model(media.__class__)
    agg = Review.objects.filter(content_type=ct, object_id=media.id).aggregate(avg=Avg('rating'), count=Count('id'))
    if not agg['count']:
        return None
    return f"{agg['avg']:.1f}"


def _movie_banner(movie) -> dict | None:
    today = date.today()
    if movie.release_date and movie.release_date > today:
        return {'icon': 'calendar', 'text': f"RELEASES {movie.release_date.strftime('%b %d, %Y').upper()}"}
    if movie.digital_release_date and movie.digital_release_date > today:
        return {'icon': 'film', 'text': 'IN THEATERS'}
    if movie.digital_release_date and today - movie.digital_release_date <= timedelta(days=30):
        return {'icon': 'disc', 'text': 'NEW ON DIGITAL'}
    return None


def _series_banner(tvshow, user, ctx: str | None) -> dict | None:
    from tvshows.models import Episode, WatchedEpisode

    today = timezone.now().date()
    aired = Episode.objects.filter(
        season__show=tvshow, season__season_number__gt=0,
        air_date__isnull=False, air_date__lte=today,
    )
    total_aired = aired.count()
    if not total_aired:
        return None
    watched_aired = WatchedEpisode.objects.filter(user=user, episode__in=aired).count()

    if ctx == 'cw':
        pct = min(int(watched_aired / total_aired * 100), 100)
        return {'icon': 'play', 'text': f"{watched_aired}/{total_aired} EPISODES", 'progress': pct}

    if watched_aired < total_aired:
        return None
    next_ep = tvshow.get_next_episode()
    if not next_ep or not next_ep.air_date:
        return None
    date_str = next_ep.air_date.strftime('%b %d').upper()
    return {'icon': 'next', 'text': f"NEXT: {date_str} \u00b7 S{next_ep.season.season_number}E{next_ep.episode_number}"}


def _apply_glass_panel(image: Image.Image, box: tuple, radius: float) -> None:
    """Blur + tint the poster region behind a chip, simulating frosted glass, painted directly onto `image`."""
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1 = max(x1, 0), max(y1, 0)
    x2, y2 = min(x2, image.width), min(y2, image.height)
    if x2 <= x1 or y2 <= y1:
        return

    region = image.crop((x1, y1, x2, y2))
    blurred = region.filter(ImageFilter.GaussianBlur(radius=GLASS_BLUR_RADIUS))
    glass = Image.alpha_composite(blurred, Image.new('RGBA', blurred.size, GLASS_TINT))

    mask = Image.new('L', glass.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, glass.size[0] - 1, glass.size[1] - 1], radius=radius, fill=255)
    image.paste(glass, (x1, y1), mask)


def _draw_rating_chip(image: Image.Image, overlay: Image.Image, width: int, text: str) -> None:
    draw = ImageDraw.Draw(overlay)
    font = _font(max(30, round(width * 0.08)))
    margin = max(16, round(width * 0.035))
    pad_x = max(14, round(width * 0.032))
    pad_y = max(10, round(width * 0.024))

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    icon_size = text_h + 10

    chip_w = icon_size + 8 + text_w + pad_x * 2
    chip_h = max(icon_size, text_h) + pad_y * 2
    x2 = width - margin
    y1 = margin
    x1 = x2 - chip_w
    y2 = y1 + chip_h
    radius = max(10, round(chip_h * CHIP_CORNER_RADIUS_RATIO))

    _apply_glass_panel(image, (x1, y1, x2, y2), radius)
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, outline=(*ACCENT, 230), width=2)
    logo = _logo_icon(icon_size)
    overlay.paste(logo, (int(x1 + pad_x), int((y1 + y2) / 2 - icon_size / 2)), logo)
    draw.text(
        (x1 + pad_x + icon_size + 8, y1 + pad_y - bbox[1]), text, font=font, fill=WHITE,
        stroke_width=1, stroke_fill=(0, 0, 0, 160),
    )


def _draw_context_chip(image: Image.Image, draw: ImageDraw.ImageDraw, width: int, height: int, banner: dict) -> None:
    """Minimal caption: plain text over a soft scrim, no box and no icon."""
    font = _font(max(30, round(width * 0.082)))
    margin = max(16, round(width * 0.035))
    bar_h = max(10, round(width * 0.028))
    has_progress = banner.get('progress') is not None

    scrim_h = round(height * 0.22)
    y0 = height - scrim_h
    for i in range(scrim_h):
        alpha = int(190 * (i / scrim_h))
        draw.line([(0, y0 + i), (width, y0 + i)], fill=(0, 0, 0, alpha))

    text = banner['text']
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    bottom_reserve = margin + (bar_h + 12 if has_progress else 0)
    ty = height - bottom_reserve - text_h

    draw.text((margin, ty - bbox[1]), text, font=font, fill=WHITE, stroke_width=2, stroke_fill=(0, 0, 0, 200))

    if has_progress:
        bar_y = height - margin - bar_h
        bar_w = width - margin * 2
        draw.rounded_rectangle(
            [margin, bar_y, margin + bar_w, bar_y + bar_h], radius=bar_h / 2,
            fill=(255, 255, 255, 90), outline=(0, 0, 0, 140), width=1,
        )
        filled_w = int(bar_w * banner['progress'] / 100)
        if filled_w > 0:
            draw.rounded_rectangle([margin, bar_y, margin + filled_w, bar_y + bar_h], radius=bar_h / 2, fill=(*ACCENT, 255))
