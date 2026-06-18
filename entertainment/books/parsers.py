import re
import unicodedata
import logging
import requests
from datetime import datetime

from django.contrib.contenttypes.models import ContentType

from api.services.books import BooksService
from custom_auth.models import Person, Keyword, CustomUser, Watchlist, MediaPerson
from .models import Book, BookSeries, Publisher, BookCollection, BookGenre

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenLibrary helpers (for Wikidata cross-linking)
# ---------------------------------------------------------------------------

def get_openlibrary_author_data(openlibrary_id):
    """Fetch author data from the OpenLibrary API."""
    try:
        response = requests.get(
            f"https://openlibrary.org/authors/{openlibrary_id}.json",
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Failed to fetch OpenLibrary data for author %s: %s", openlibrary_id, exc)
        return None


def extract_openlibrary_id_from_author(author_data):
    """
    Extract an OpenLibrary author ID from a Hardcover author's ``links`` jsonb field.
    Hardcover stores external links as a list of objects with ``url`` keys.
    """
    if not author_data:
        return None

    links = author_data.get('links')
    if isinstance(links, list):
        for link in links:
            url = link.get('url', '') if isinstance(link, dict) else str(link)
            match = re.search(r'openlibrary\.org/authors/([A-Z0-9]+)', url)
            if match:
                return match.group(1)
    elif isinstance(links, dict):
        for url in links.values():
            match = re.search(r'openlibrary\.org/authors/([A-Z0-9]+)', str(url))
            if match:
                return match.group(1)
    return None


def get_wikidata_id_from_openlibrary(ol_author_data):
    """Extract Wikidata Q-identifier from OpenLibrary author data."""
    if not ol_author_data:
        return None

    for link in ol_author_data.get('links', []):
        if isinstance(link, dict):
            url = link.get('url', '')
            if 'wikidata' in link.get('title', '').lower() or 'wikidata' in url:
                match = re.search(r'wikidata\.org/wiki/(Q\d+)', url)
                if match:
                    return match.group(1)

    identifiers = ol_author_data.get('identifiers', {})
    wikidata = identifiers.get('wikidata')
    if isinstance(wikidata, list) and wikidata:
        return wikidata[0]
    if isinstance(wikidata, str):
        return wikidata
    return None


# ---------------------------------------------------------------------------
# Person name normalisation + fuzzy matching (Tier 4)
# ---------------------------------------------------------------------------

def _normalise_name(name):
    """Lowercase, strip accents, remove punctuation, collapse whitespace."""
    if not name:
        return ''
    name = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^\w\s-]", '', name)
    name = re.sub(r'\b(\w)\.', r'\1', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


_AUTHOR_ROLE_FLAGS = (
    'is_novelist', 'is_book_author', 'is_writer', 'is_book',
    'is_comic_artist', 'is_graphic_novelist', 'is_original_story',
)


def _fuzzy_person_match(name, birth_year):
    """
    Tier-4 fallback: find an existing Person by normalised name + optional birth year.
    Returns the best candidate or None.
    """
    if not name:
        return None

    norm = _normalise_name(name)
    first_token = name.split()[0] if ' ' in name else name

    candidates = list(
        Person.objects.filter(name__iexact=name) |
        Person.objects.exclude(name__iexact=name).filter(name__icontains=first_token)
    )
    candidates = [p for p in candidates if _normalise_name(p.name) == norm]
    if not candidates:
        return None

    if birth_year:
        by_year = [
            p for p in candidates
            if p.date_of_birth and abs(p.date_of_birth.year - birth_year) <= 1
        ]
        if by_year:
            candidates = by_year

    if not candidates:
        return None

    author_flagged = [p for p in candidates
                      if any(getattr(p, flag, False) for flag in _AUTHOR_ROLE_FLAGS)]
    return (author_flagged or candidates)[0]


# ---------------------------------------------------------------------------
# Book data extraction
# ---------------------------------------------------------------------------

def _get_cover_url(cached_image):
    """Extract the best cover URL from Hardcover's cached_image jsonb field."""
    if not cached_image:
        return None
    if isinstance(cached_image, dict):
        return cached_image.get('url')
    return None


def extract_book_data(book_details):
    """Extract and normalise scalar fields from a Hardcover book object."""
    release_date = None
    raw_date = book_details.get('release_date')
    if raw_date:
        try:
            release_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass

    edition = book_details.get('default_physical_edition') or {}
    language_obj = edition.get('language') or {}
    title = book_details.get('title') or ''

    return {
        'title': title,
        'original_title': title,  # Books don't have a separate original title
        'subtitle': book_details.get('subtitle'),
        'description': book_details.get('description'),
        'isbn_10': edition.get('isbn_10'),
        'isbn_13': edition.get('isbn_13'),
        'pages': edition.get('pages'),
        'published_date': release_date,
        'language': language_obj.get('language'),
        'image_url': _get_cover_url(book_details.get('cached_image')),
        'hardcover_id': book_details.get('id'),
        'rating': book_details.get('rating'),
    }


# ---------------------------------------------------------------------------
# Genres / keywords
# ---------------------------------------------------------------------------

# Minimum number of distinct users who must have applied a tag to this specific
# book before it is stored.  Each duplicate tagging row = one user's vote.
_GENRE_MIN_VOTES = 2
_GENRE_MAX_RESULTS = 5
_KEYWORD_MIN_VOTES = 5


def process_genres_keywords(book_details):
    """
    Genres and keywords from Hardcover.

    The taggings table has one row per (user, book, tag).  The `count` field
    on the tag object is a GLOBAL count (how many books have that tag) and is
    useless for per-book filtering.  Instead we count duplicate tag IDs in the
    taggings list for this specific book — that gives us the number of users
    who applied each tag.

    Thresholds:
      _GENRE_MIN_VOTES   (default 3) — keeps Classics/Russian Literature/Fiction
                          for Crime & Punishment while dropping single-user noise.
      _KEYWORD_MIN_VOTES (default 5) — keeps high-signal mood/trope keywords.

    Tag routing by tag_category_id:
      1     → BookGenre  (separate from TMDB movie/TV genres)
      other → Keyword    (shared model across movies/games/books)

    Typesense search results supply a flat 'genres' list as a final fallback
    (no per-book vote data available there, so all entries are accepted).
    """
    from collections import Counter

    genre_instances = []
    keyword_instances = []

    # --- Count per-book user votes for each tag ---
    genre_votes = Counter()    # tag_id -> vote count
    keyword_votes = Counter()  # tag_id -> vote count
    tag_meta = {}              # tag_id -> {name, category_id}

    for tagging in book_details.get('taggings', []):
        tag = tagging.get('tag', {}) if isinstance(tagging, dict) else {}
        tag_id = tag.get('id')
        tag_name = (tag.get('tag') or '').strip()
        category_id = tag.get('tag_category_id')
        if not tag_id or not tag_name:
            continue
        tag_meta[tag_id] = {'name': tag_name, 'category_id': category_id}
        if category_id == 1:
            genre_votes[tag_id] += 1
        else:
            keyword_votes[tag_id] += 1

    # --- Build genre instances (threshold: _GENRE_MIN_VOTES, cap: _GENRE_MAX_RESULTS) ---
    # If the book is obscure and fewer than 2 genres pass the threshold,
    # lower the bar to 1 vote so it still gets some genre data.
    genres_seen = set()
    effective_min = _GENRE_MIN_VOTES
    candidates = genre_votes.most_common()
    qualifying = [tid for tid, v in candidates if v >= effective_min]
    if len(qualifying) < 2:
        effective_min = 1
    for tag_id, votes in candidates:
        if votes < effective_min or len(genre_instances) >= _GENRE_MAX_RESULTS:
            break
        meta = tag_meta[tag_id]
        norm = meta['name'].lower()
        if norm in genres_seen:
            continue
        genres_seen.add(norm)
        g, _ = BookGenre.objects.get_or_create(
            name=meta['name'],
            defaults={'hardcover_tag_id': tag_id},
        )
        if g.hardcover_tag_id != tag_id:
            g.hardcover_tag_id = tag_id
            g.save(update_fields=['hardcover_tag_id'])
        genre_instances.append(g)

    # --- Build keyword instances (threshold: _KEYWORD_MIN_VOTES) ---
    keywords_seen = set()
    for tag_id, votes in keyword_votes.most_common():
        if votes < _KEYWORD_MIN_VOTES:
            break
        meta = tag_meta[tag_id]
        norm = meta['name'].lower()
        if norm in keywords_seen:
            continue
        keywords_seen.add(norm)
        kw = Keyword.objects.filter(hardcover_tag_id=tag_id).first()
        if not kw:
            kw = Keyword.objects.filter(name__iexact=norm).first()
            if kw:
                if not kw.hardcover_tag_id:
                    kw.hardcover_tag_id = tag_id
                    kw.save(update_fields=['hardcover_tag_id'])
            else:
                kw = Keyword.objects.create(name=norm, hardcover_tag_id=tag_id)
        keyword_instances.append(kw)

    # --- Typesense fallback: flat genres list (no vote data) ---
    for genre_name in book_details.get('genres', []):
        if isinstance(genre_name, dict):
            genre_name = genre_name.get('name', '')
        genre_name = (genre_name or '').strip()
        if genre_name and genre_name.lower() not in genres_seen:
            genres_seen.add(genre_name.lower())
            g, _ = BookGenre.objects.get_or_create(name=genre_name)
            genre_instances.append(g)

    return genre_instances, keyword_instances


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def process_series(book, book_details):
    """Associate the featured series (if any) from book_series entries."""
    book_series_list = book_details.get('book_series', [])
    if not book_series_list:
        return

    featured = next((bs for bs in book_series_list if bs.get('featured')), None)
    entry = featured or book_series_list[0]

    series_data = entry.get('series', {})
    series_id = series_data.get('id')
    series_name = series_data.get('name')
    if not series_id or not series_name:
        return

    series, _ = BookSeries.objects.get_or_create(
        hardcover_id=series_id,
        defaults={'name': series_name}
    )
    book.series = series
    book.series_position = entry.get('position')
    book.save(update_fields=['series', 'series_position'])


# ---------------------------------------------------------------------------
# Publishers
# ---------------------------------------------------------------------------

def process_publishers(book, book_details):
    """Extract publisher from the default physical edition."""
    edition = book_details.get('default_physical_edition') or {}
    pub_data = edition.get('publisher')
    if not pub_data:
        return

    pub, _ = Publisher.objects.get_or_create(
        hardcover_id=pub_data.get('id'),
        defaults={'name': pub_data.get('name', '')}
    )
    book.publishers.add(pub)


# ---------------------------------------------------------------------------
# Authors (4-tier cross-linking algorithm)
# ---------------------------------------------------------------------------

def process_authors(book, book_details):
    """
    Create/link Person records for each author contribution on a book.

    Matching tiers (stops at first hit):
      1. hardcover_id   – direct integer ID match
      2. wikidata_id    – via OpenLibrary lookup from author links
      3. imdb_id        – if present in author links (rare)
      4. Fuzzy          – normalised name + birth year ±1 yr, boosted by author flags
    """
    book_content_type = ContentType.objects.get_for_model(book)

    contributions = book_details.get('contributions', [])
    author_contributions = [
        c for c in contributions
        if (c.get('contribution') or '').lower() in ('author', 'primary author', '')
    ]
    if not author_contributions:
        author_contributions = contributions

    for index, contrib in enumerate(author_contributions):
        author = contrib.get('author', {})
        if not author:
            continue

        author_id = author.get('id')
        author_name = author.get('name', '')
        author_bio = author.get('bio', '')

        born_date_raw = author.get('born_date')
        birth_year = None
        if born_date_raw:
            try:
                birth_year = datetime.strptime(born_date_raw, '%Y-%m-%d').year
            except (ValueError, TypeError):
                try:
                    birth_year = int(str(born_date_raw)[:4])
                except (ValueError, TypeError):
                    pass

        person_instance = None

        # Tier 1: hardcover_id
        if author_id:
            person_instance = Person.objects.filter(hardcover_id=author_id).first()

        # Tier 2: wikidata_id via OpenLibrary
        openlibrary_id = extract_openlibrary_id_from_author(author)
        ol_data = None
        wikidata_id = None

        # Always resolve wikidata_id when the matched person is missing it,
        # so we can back-fill it even when Tier 1 (hardcover_id) matched.
        needs_wikidata = not person_instance or not person_instance.wikidata_id
        if needs_wikidata and openlibrary_id:
            ol_data = get_openlibrary_author_data(openlibrary_id)
            wikidata_id = get_wikidata_id_from_openlibrary(ol_data)

        if not person_instance and wikidata_id:
            person_instance = Person.objects.filter(wikidata_id=wikidata_id).first()

        # Tier 3: imdb_id from author links
        if not person_instance:
            links = author.get('links') or {}
            imdb_id_from_links = None
            if isinstance(links, dict):
                link_values = links.values()
            elif isinstance(links, list):
                link_values = [lnk.get('url', '') if isinstance(lnk, dict) else str(lnk) for lnk in links]
            else:
                link_values = []
            for url in link_values:
                m = re.search(r'imdb\.com/name/(nm\d+)', str(url))
                if m:
                    imdb_id_from_links = m.group(1)
                    break
            if imdb_id_from_links:
                person_instance = Person.objects.filter(imdb_id=imdb_id_from_links).first()

        # Tier 4: Fuzzy name + birth year
        if not person_instance:
            person_instance = _fuzzy_person_match(author_name, birth_year)

        # -- Create or update ---------------------------------------------------
        if not person_instance:
            create_kwargs = dict(
                name=author_name,
                bio=author_bio or None,
                hardcover_id=author_id,
                wikidata_id=wikidata_id,
                openlibrary_id=openlibrary_id,
                is_writer=True,
                is_book_author=True,
            )
            if born_date_raw:
                try:
                    from dateutil import parser as date_parser
                    create_kwargs['date_of_birth'] = date_parser.parse(born_date_raw).date()
                except Exception:
                    pass
            death_date_raw = author.get('death_date')
            if death_date_raw:
                try:
                    from dateutil import parser as date_parser
                    create_kwargs['date_of_death'] = date_parser.parse(death_date_raw).date()
                except Exception:
                    pass
            cached_img = author.get('cached_image')
            if cached_img and isinstance(cached_img, dict):
                create_kwargs['profile_picture'] = cached_img.get('url')
            person_instance = Person.objects.create(**create_kwargs)
        else:
            update_fields = []
            if not person_instance.hardcover_id and author_id:
                person_instance.hardcover_id = author_id
                update_fields.append('hardcover_id')
            if not person_instance.wikidata_id and wikidata_id:
                person_instance.wikidata_id = wikidata_id
                update_fields.append('wikidata_id')
            if not person_instance.openlibrary_id and openlibrary_id:
                person_instance.openlibrary_id = openlibrary_id
                update_fields.append('openlibrary_id')
            if not person_instance.bio and author_bio:
                person_instance.bio = author_bio
                update_fields.append('bio')
            if not person_instance.is_book_author:
                person_instance.is_book_author = True
                person_instance.is_writer = True
                update_fields += ['is_book_author', 'is_writer']
            if update_fields:
                person_instance.save(update_fields=update_fields)

        # -- Link author to book ------------------------------------------------
        MediaPerson.objects.get_or_create(
            content_type=book_content_type,
            object_id=book.id,
            person=person_instance,
            defaults={'role': 'Author', 'order': index}
        )
        book.authors.add(person_instance)


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def add_to_book_watchlist(book, user_id):
    Watchlist.objects.get_or_create(
        user_id=user_id,
        content_type=ContentType.objects.get_for_model(book),
        object_id=book.id,
    )


# ---------------------------------------------------------------------------
# Main creation function
# ---------------------------------------------------------------------------

def create_book(book_id, add_to_watchlist=False, user_id=None):
    """Create (or return existing) a Book from a Hardcover integer book ID."""
    existing = Book.objects.filter(hardcover_id=book_id).first()
    if existing:
        if add_to_watchlist and user_id:
            add_to_book_watchlist(existing, user_id)
        return existing

    books_service = BooksService()
    book_details = books_service.get_book_details(book_id)
    if not book_details:
        logger.error("No book details returned from Hardcover for ID %s", book_id)
        return None

    book_dict = extract_book_data(book_details)
    genre_instances, keyword_instances = process_genres_keywords(book_details)

    user = CustomUser.objects.filter(id=user_id).first() if user_id else None
    book = Book.objects.create(**book_dict, added_by=user)
    book.genres.set(genre_instances)
    book.keywords.set(keyword_instances)

    process_series(book, book_details)
    process_publishers(book, book_details)
    process_authors(book, book_details)

    if add_to_watchlist and user_id:
        add_to_book_watchlist(book, user_id)

    return book


