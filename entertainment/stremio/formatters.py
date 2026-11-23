import re
from urllib.parse import quote

_TMDB_IMAGE_SIZE_RE = re.compile(r'(image\.tmdb\.org/t/p/)(?:original|w\d+)(/)')
_YOUTUBE_ID_RE = re.compile(r'(?:youtube\.com/(?:embed/|watch\?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})')

# TMDB statuses that mean a show won't get more episodes
FINISHED_TVSHOW_STATUSES = {'Ended', 'Canceled'}
META_CAST_LIMIT = 10
META_DIRECTOR_LIMIT = 5


def get_poster_url(media, size: str = 'w780') -> str | None:
    """Get full poster URL for a media item, downsized (w780 by default) to keep catalog posters small."""
    poster = getattr(media, 'poster', None) or getattr(media, 'poster_path', None)
    if not poster:
        return None
    poster = str(poster)

    # Already a full URL: if it's TMDB, force it down to `size` regardless of what size was stored
    if poster.startswith('http'):
        return _TMDB_IMAGE_SIZE_RE.sub(rf'\g<1>{size}\g<2>', poster)

    # TMDB poster path
    if poster.startswith('/'):
        return f"https://image.tmdb.org/t/p/{size}{poster}"

    return None


def resize_tmdb_image(url: str | None, size: str) -> str | None:
    """Rewrite a TMDB image URL to `size` (e.g. w500); non-TMDB URLs pass through unchanged."""
    if not url:
        return None
    return _TMDB_IMAGE_SIZE_RE.sub(rf'\g<1>{size}\g<2>', str(url))


def youtube_id(url: str | None) -> str | None:
    """Pull the 11-char video id out of a stored YouTube embed/watch/short link."""
    if not url:
        return None
    match = _YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def release_info(media) -> str | None:
    """Year for movies; "2019–2023" / "2019–" (still running) for series."""
    release = getattr(media, 'release_date', None)
    if release:
        return str(release.year)

    first = getattr(media, 'first_air_date', None)
    if not first:
        return None
    if getattr(media, 'status', None) not in FINISHED_TVSHOW_STATUSES:
        return f"{first.year}–"
    last = getattr(media, 'last_air_date', None)
    if not last or last.year == first.year:
        return str(first.year)
    return f"{first.year}–{last.year}"


def _genre_names(media) -> list[str]:
    genres = getattr(media, 'genres', None)
    if genres is None:
        return []
    if isinstance(genres, list):  # plain lists come from non-DB sources (TMDB-backed Discover items)
        return genres
    return [g.name for g in genres.all()]


def to_stremio_meta(media, media_type: str, review=None, *, community=None, show_plot: bool = True,
                    site_url: str | None = None, manifest_url: str | None = None,
                    genre_catalog: str | None = 'top-rated', imdb_rating: str | None = None) -> dict | None:
    """
    Convert a Movie or TVShow to Stremio meta format.

    Args:
        media: Movie or TVShow instance
        media_type: 'movie' or 'series'
        review: Optional Review instance or aggregated review data for the user
        community: Optional {'avg': float, 'count': int} across all users' reviews
        show_plot: False hides the overview (UserSettings.show_plot)
        site_url: Absolute URL of this title's page on the site
        manifest_url: This addon's manifest URL, used for genre deep links into its own catalogs
        genre_catalog: Catalog id the genre links open (one the user has enabled); None drops them
        imdb_rating: IMDb rating string (e.g. "8.5"); shown as Stremio's IMDb badge
    """
    imdb_id = getattr(media, 'imdb_id', None)
    if not imdb_id:
        return None

    meta = {
        'id': imdb_id,
        'type': media_type,
        'name': getattr(media, 'name', None) or getattr(media, 'title', ''),
        'poster': get_poster_url(media),
        'description': build_description(media, review, community=community, show_plot=show_plot),
    }

    info = release_info(media)
    if info:
        meta['releaseInfo'] = info
    _add_release_dates(meta, media, info)

    if imdb_rating:
        meta['imdbRating'] = imdb_rating  # only read by clients when `links` is absent; see _build_links

    if media_type == 'movie':
        # Lets Stremio open the stream list for the movie directly instead of a one-item video list
        meta['behaviorHints'] = {'defaultVideoId': imdb_id}

    genres = _genre_names(media)
    if genres:
        meta['genres'] = genres

    # Add runtime for movies
    if getattr(media, 'runtime', None):
        meta['runtime'] = f"{media.runtime} min"

    # Add background/fanart
    backdrop = getattr(media, 'backdrop', None) or getattr(media, 'backdrop_path', None)
    if backdrop:
        if str(backdrop).startswith('/'):
            meta['background'] = f"https://image.tmdb.org/t/p/original{backdrop}"
        elif str(backdrop).startswith('http'):
            meta['background'] = str(backdrop)

    cast = [mp.person.name for mp in media.cast[:META_CAST_LIMIT]] if hasattr(media, 'cast') else []
    if cast:
        meta['cast'] = cast

    directors_qs = getattr(media, 'directors', None) if media_type == 'movie' else getattr(media, 'creators', None)
    directors = list(directors_qs.values_list('name', flat=True)[:META_DIRECTOR_LIMIT]) if directors_qs is not None else []
    if directors:
        meta['director'] = directors

    yt_id = youtube_id(getattr(media, 'trailer', None))
    if yt_id:
        meta['trailers'] = [{'source': yt_id, 'type': 'Trailer'}]
        meta['trailerStreams'] = [{'title': 'Trailer', 'ytId': yt_id}]

    meta['links'] = _build_links(media_type, genres, cast, directors, site_url, manifest_url, genre_catalog,
                                 imdb_id=imdb_id, imdb_rating=imdb_rating,
                                 ratings=rating_links(review, community, site_url))
    return meta


def _build_links(media_type: str, genres: list[str], cast: list[str], directors: list[str],
                 site_url: str | None, manifest_url: str | None,
                 genre_catalog: str | None = 'top-rated', *, imdb_id: str | None = None,
                 imdb_rating: str | None = None,
                 ratings: tuple[list[dict], list[dict]] = ([], [])) -> list[dict]:
    """Newer Stremio clients render genres/cast/directors from `links` rather than the legacy flat fields.

    stremio-core only turns the legacy `imdbRating` into its IMDb badge when a meta has *no* `links`
    (MetaItemPreviewLegacy -> MetaItemPreview), so with links present the rating must be a link too,
    in the same shape Cinemeta sends.
    """
    links = []
    if imdb_rating and imdb_id:
        links.append({'name': imdb_rating, 'category': 'imdb', 'url': f"https://imdb.com/title/{imdb_id}"})
    if manifest_url and genre_catalog:
        encoded_manifest = quote(manifest_url, safe='')
        links += [
            {'name': g, 'category': 'Genres',
             'url': f"stremio:///discover/{encoded_manifest}/{media_type}/{genre_catalog}?genre={quote(g)}"}
            for g in genres
        ]
    # Ratings sit right after genres, ahead of the long cast list; groups render in first-seen order
    site_ratings, season_ratings = ratings
    links += site_ratings
    if site_url:
        links.append({'name': 'Open in Entertainment List', 'category': SITE_LINK_CATEGORY, 'url': site_url})
    links += season_ratings
    links += [{'name': n, 'category': 'Cast', 'url': f"stremio:///search?search={quote(n)}"} for n in cast]
    links += [{'name': n, 'category': 'Directors', 'url': f"stremio:///search?search={quote(n)}"} for n in directors]
    return links


def _add_release_dates(target: dict, media, info: str | None) -> None:
    """`released` (ISO) and `year` like Cinemeta: Stremio Web reads releaseInfo, other clients these."""
    released = getattr(media, 'release_date', None) or getattr(media, 'first_air_date', None)
    if released:
        target['released'] = f"{released.isoformat()}T00:00:00.000Z"
    if info:
        target['year'] = info


def build_description(media, review=None, *, community=None, show_plot: bool = True) -> str:
    """Plot only. Ratings are rendered as link chips (see rating_links) so they don't bury the overview."""
    overview = getattr(media, 'overview', None) or getattr(media, 'description', '')
    return overview if show_plot and overview else ''


def _fmt_rating(value) -> str:
    """9 -> "9", 8.25 -> "8.3": chips are small, so no "/10" and no trailing ".0"."""
    value = round(float(value), 1)
    return str(int(value)) if value == int(value) else f"{value:.1f}"


SITE_LINK_CATEGORY = 'Entertainment List'
SEASON_RATINGS_CATEGORY = 'Season ratings'


def rating_links(review=None, community=None, site_url: str | None = None) -> tuple[list[dict], list[dict]]:
    """(site group, season group) chips: "You ★ 8.3", "Community ★ 8.3 (11)", "S1 7", ...

    Stremio only renders links that carry a URL, so the chips point at the title's site page.
    """
    if not site_url:
        return [], []
    site, seasons = [], []
    rating = None
    if isinstance(review, dict):  # aggregated TV review data
        rating = review.get('avg_rating')
        seasons = [
            {'name': f"{s['label']} {_fmt_rating(s['rating'])}", 'category': SEASON_RATINGS_CATEGORY, 'url': site_url}
            for s in review.get('season_ratings', [])
        ]
    elif review is not None:
        rating = review.rating
    if rating:
        site.append({'name': f"You ★ {_fmt_rating(rating)}", 'category': SITE_LINK_CATEGORY, 'url': site_url})
    if community and community.get('count'):
        site.append({
            'name': f"Community ★ {_fmt_rating(community['avg'])} ({community['count']})",
            'category': SITE_LINK_CATEGORY, 'url': site_url,
        })
    return site, seasons


def to_stremio_catalog_item(media, media_type: str, poster_url: str | None = None, *,
                            show_plot: bool = True, imdb_rating: str | None = None) -> dict | None:
    """Convert media to minimal Stremio catalog item format."""
    imdb_id = getattr(media, 'imdb_id', None)
    if not imdb_id:
        return None

    item = {
        'id': imdb_id,
        'type': media_type,
        'name': getattr(media, 'name', None) or getattr(media, 'title', ''),
        'poster': poster_url or get_poster_url(media),
    }

    # Add optional fields for richer catalog display
    if imdb_rating:
        item['imdbRating'] = imdb_rating

    overview = getattr(media, 'overview', None) or getattr(media, 'description', '')
    if show_plot and overview:
        # Truncate for catalog view
        item['description'] = overview[:200] + '...' if len(overview) > 200 else overview

    info = release_info(media)
    if info:
        item['releaseInfo'] = info
    _add_release_dates(item, media, info)

    genres = _genre_names(media)
    if genres:
        item['genres'] = genres

    return item
