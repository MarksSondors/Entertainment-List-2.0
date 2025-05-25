from .models import Book, BookSeries, Publisher, BookCollection
from api.services.books import BooksService
from custom_auth.models import Person, Genre, Keyword, CustomUser, Watchlist
from django.contrib.contenttypes.models import ContentType
from datetime import datetime
import requests
import re
import time
import logging

# Set up logger
logger = logging.getLogger(__name__)

def extract_book_data(book_details):
    """Extract and format basic book data from API response"""
    # Parse the date if it exists
    published_date = None
    if book_details.get('published_date'):
        try:
            published_date = datetime.strptime(book_details.get('published_date'), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass
    
    return {
        'title': book_details.get('title'),
        'subtitle': book_details.get('subtitle'),
        'description': book_details.get('description'),
        'isbn_10': book_details.get('isbn_10'),
        'isbn_13': book_details.get('isbn_13'),
        'pages': book_details.get('pages'),
        'published_date': published_date,
        'language': book_details.get('language'),
        'image_url': book_details.get('image_url'),
        'hardcover_id': book_details.get('id'),
    }

def get_openlibrary_author_data(author_id):
    """
    Fetch author data from OpenLibrary API
    
    Args:
        author_id: OpenLibrary author ID (e.g., OL23919A)
        
    Returns:
        Dict with author data or None if not found
    """
    try:
        response = requests.get(f"https://openlibrary.org/authors/{author_id}.json", timeout=10)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Failed to fetch OpenLibrary data for author {author_id}: {e}")
        return None

def extract_openlibrary_id_from_hardcover(author_data):
    """
    Extract OpenLibrary ID from Hardcover author data
    
    Hardcover may include links to external sources including OpenLibrary
    """
    # This is hypothetical - adjust based on actual Hardcover API response
    # Common places where OpenLibrary links might be found:
    # 1. In a dedicated field like external_ids.openlibrary
    # 2. In an external_urls array/object
    # 3. In the bio text as a link
    
    # Check for OpenLibrary ID in external_ids if available
    external_ids = author_data.get('external_ids', {})
    if external_ids and 'openlibrary' in external_ids:
        return external_ids['openlibrary']
    
    # Check for OpenLibrary URL in external_urls if available
    external_urls = author_data.get('external_urls', [])
    if external_urls:
        for url in external_urls:
            if 'openlibrary.org/authors/' in url:
                # Extract ID from URL like https://openlibrary.org/authors/OL23919A
                match = re.search(r'openlibrary\.org/authors/([A-Z0-9]+)', url)
                if match:
                    return match.group(1)
    
    # As a last resort, check bio text for OpenLibrary links
    bio = author_data.get('bio', '')
    if bio and 'openlibrary.org/authors/' in bio:
        match = re.search(r'openlibrary\.org/authors/([A-Z0-9]+)', bio)
        if match:
            return match.group(1)
    
    return None

def get_wikidata_id_from_openlibrary(ol_author_data):
    """Extract Wikidata ID from OpenLibrary author data"""
    if not ol_author_data:
        return None
    
    # Look for Wikidata ID in OpenLibrary's links or identifiers
    links = ol_author_data.get('links', [])
    for link in links:
        if isinstance(link, dict) and link.get('title') == 'Wikidata':
            url = link.get('url', '')
            # Extract ID from URL like https://www.wikidata.org/wiki/Q42
            match = re.search(r'wikidata\.org/wiki/(Q\d+)', url)
            if match:
                return match.group(1)
    
    # Check alternate location for Wikidata ID
    identifiers = ol_author_data.get('identifiers', {})
    if identifiers and 'wikidata' in identifiers:
        wikidata_id = identifiers['wikidata']
        if isinstance(wikidata_id, list) and wikidata_id:
            return wikidata_id[0]
        return wikidata_id
    
    return None

def process_genres_keywords(book_details):
    """Process and create genre and keyword instances"""
    # Process genres
    genres = book_details.get('genres', [])
    genre_instances = []
    for genre in genres:
        genre_instance, _ = Genre.objects.get_or_create(
            name=genre.get('name')
        )
        genre_instances.append(genre_instance)

    # Process keywords - if available in the API
    keywords = book_details.get('keywords', [])
    keyword_instances = []
    for keyword in keywords:
        keyword_instance, _ = Keyword.objects.get_or_create(
            name=keyword.get('name')
        )
        keyword_instances.append(keyword_instance)

    return genre_instances, keyword_instances

def process_series(book, book_details, books_service):
    """Process and associate series if applicable"""
    series_info = book_details.get('series')
    if series_info:
        series, _ = BookSeries.objects.get_or_create(
            hardcover_id=series_info.get('id'),
            defaults={
                'name': series_info.get('name'),
                'description': series_info.get('description', '')
            }
        )
        book.series = series
        book.series_position = series_info.get('position')
        book.save()

def process_publishers(book, book_details):
    """Process and associate publishers"""
    publishers = book_details.get('publishers', [])
    publisher_instances = []
    
    for publisher in publishers:
        publisher_instance, _ = Publisher.objects.get_or_create(
            hardcover_id=publisher.get('id'),
            defaults={'name': publisher.get('name')}
        )
        publisher_instances.append(publisher_instance)
    
    if publisher_instances:
        book.publishers.set(publisher_instances)

def process_authors(book, book_details, books_service):
    """Process authors for the book with OpenLibrary and Wikidata integration"""
    book_content_type = ContentType.objects.get_for_model(book)
    authors = book_details.get('authors', [])
    
    for index, author in enumerate(authors):
        author_id = author.get('id')
        author_name = author.get('name')
        author_bio = author.get('bio', '')
        
        # First, try to find author by Hardcover ID
        person_instance = Person.objects.filter(hardcover_id=author_id).first()
        
        # If not found by Hardcover ID, try by name (case-insensitive)
        if not person_instance and author_name:
            person_instance = Person.objects.filter(name__iexact=author_name).first()
        
        # Get additional data from OpenLibrary
        openlibrary_id = extract_openlibrary_id_from_hardcover(author)
        ol_author_data = None
        wikidata_id = None
        
        if openlibrary_id:
            # Get author data from OpenLibrary
            ol_author_data = get_openlibrary_author_data(openlibrary_id)
            if ol_author_data:
                wikidata_id = get_wikidata_id_from_openlibrary(ol_author_data)
                
                # If still not found, try by wikidata_id
                if not person_instance and wikidata_id:
                    person_instance = Person.objects.filter(wikidata_id=wikidata_id).first()
        
        # Create or update the author
        if not person_instance:
            # Create new author with all available information
            person_instance = Person.objects.create(
                name=author_name,
                bio=author_bio,
                hardcover_id=author_id,
                wikidata_id=wikidata_id,
                openlibrary_id=openlibrary_id,
                is_writer=True,  # Author is a writer
                is_book_author=True  # Mark as a book author
            )
            
            # Additional data from OpenLibrary if available
            if ol_author_data:
                birth_date = ol_author_data.get('birth_date')
                death_date = ol_author_data.get('death_date')
                
                if birth_date and not person_instance.date_of_birth:
                    try:
                        # Try to parse date in various formats
                        # OpenLibrary might have dates as "12 April 1954" or "1954-04-12" or "1954"
                        from dateutil import parser as date_parser
                        person_instance.date_of_birth = date_parser.parse(birth_date).date()
                    except (ValueError, TypeError):
                        logger.warning(f"Could not parse birth date: {birth_date}")
                
                if death_date and not person_instance.date_of_death:
                    try:
                        person_instance.date_of_death = date_parser.parse(death_date).date()
                    except (ValueError, TypeError):
                        logger.warning(f"Could not parse death date: {death_date}")
                
                # Save any OpenLibrary data updates
                person_instance.save()
        else:
            # Update existing author with any new information
            updated = False
            
            # Update Hardcover ID if missing
            if not person_instance.hardcover_id and author_id:
                person_instance.hardcover_id = author_id
                updated = True
            
            # Update Wikidata ID if missing
            if not person_instance.wikidata_id and wikidata_id:
                person_instance.wikidata_id = wikidata_id
                updated = True
            
            # Update OpenLibrary ID if missing
            if hasattr(person_instance, 'openlibrary_id') and not person_instance.openlibrary_id and openlibrary_id:
                person_instance.openlibrary_id = openlibrary_id
                updated = True
            
            # Update bio if missing
            if not person_instance.bio and author_bio:
                person_instance.bio = author_bio
                updated = True
            
            # Update additional fields from OpenLibrary if missing
            if ol_author_data:
                if not person_instance.date_of_birth and ol_author_data.get('birth_date'):
                    try:
                        from dateutil import parser as date_parser
                        person_instance.date_of_birth = date_parser.parse(ol_author_data.get('birth_date')).date()
                        updated = True
                    except (ValueError, TypeError, ImportError):
                        pass
                
                if not person_instance.date_of_death and ol_author_data.get('death_date'):
                    try:
                        from dateutil import parser as date_parser
                        person_instance.date_of_death = date_parser.parse(ol_author_data.get('death_date')).date()
                        updated = True
                    except (ValueError, TypeError, ImportError):
                        pass
            
            # Set as book author if not already
            if not person_instance.is_book_author:
                person_instance.is_book_author = True
                person_instance.is_writer = True
                updated = True
            
            # Save if updates were made
            if updated:
                person_instance.save()
        
        # Create MediaPerson entry for the author-book relationship
        from custom_auth.models import MediaPerson
        MediaPerson.objects.get_or_create(
            content_type=book_content_type,
            object_id=book.id,
            person=person_instance,
            defaults={
                'role': "Author",
                'order': index
            }
        )
        
        # Also add to the many-to-many relationship
        book.authors.add(person_instance)

def add_to_book_watchlist(book, user_id):
    """Add book to watchlist"""
    Watchlist.objects.create(
        user_id=user_id,
        content_type=ContentType.objects.get_for_model(book),
        object_id=book.id
    )

def create_book(book_id, add_to_watchlist=False, user_id=None):
    """Main function to create a book from Hardcover ID"""
    books_service = BooksService()
    
    # Get book details from API
    result = books_service.get_book_details(book_id)
    book_details = result.get('books_by_pk')
    
    if not book_details:
        return None
    
    # Extract basic book data
    book_dict = extract_book_data(book_details)
    
    # Process metadata
    genre_instances, keyword_instances = process_genres_keywords(book_details)
    
    # Create the book instance
    book = Book.objects.create(
        **book_dict, 
        added_by=CustomUser.objects.filter(id=user_id).first() if user_id else None
    )
    
    # Associate genres and keywords
    book.genres.set(genre_instances)
    book.keywords.set(keyword_instances)
    
    # Process series information
    process_series(book, book_details, books_service)
    
    # Process publishers
    process_publishers(book, book_details)
    
    # Process authors
    process_authors(book, book_details, books_service)
    
    # Add to watchlist if specified
    if add_to_watchlist and user_id:
        add_to_book_watchlist(book, user_id)
    
    return book

def search_and_create_book(search_query, user_id=None, add_to_watchlist=False):
    """Search for a book and create it if found"""
    books_service = BooksService()
    search_results = books_service.search_books(search_query, limit=1)
    
    if not search_results or 'books' not in search_results or not search_results['books']:
        return None
    
    # Get the first book from search results
    book_data = search_results['books'][0]
    
    # Check if the book already exists in our database
    existing_book = Book.objects.filter(hardcover_id=book_data['id']).first()
    if existing_book:
        # If the book exists and user wants to add to watchlist
        if add_to_watchlist and user_id:
            add_to_book_watchlist(existing_book, user_id)
        return existing_book
    
    # Create the book if it doesn't exist
    return create_book(book_data['id'], add_to_watchlist, user_id)