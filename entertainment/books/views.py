import json
import logging
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.services.books import BooksService
from custom_auth.models import CustomUser, Review, Watchlist
from .models import Book, BookCollection, BookSeries
from .parsers import create_book

logger = logging.getLogger(__name__)


def _parse_search_hits(search_result):
    """
    Parse Typesense JSON from Hardcover's search() query into a flat list of
    dicts suitable for the search modal.
    Returns (hits, total_count).
    """
    if not search_result:
        return [], 0

    raw_results = search_result.get('results')
    ids = search_result.get('ids', [])

    if not raw_results:
        return [], 0

    if isinstance(raw_results, str):
        try:
            raw_results = json.loads(raw_results)
        except (json.JSONDecodeError, TypeError):
            return [], 0

    hits = []
    for hit in raw_results.get('hits', []):
        doc = hit.get('document', {})
        # Cover image comes as a nested object
        image = doc.get('image') or {}
        cover_url = image.get('url') if isinstance(image, dict) else None

        author_names = doc.get('author_names', [])
        if isinstance(author_names, str):
            author_names = [author_names]

        hits.append({
            'id': doc.get('id'),  # Typesense stores IDs as strings
            'hardcover_id': int(doc.get('id')) if doc.get('id') else None,
            'title': doc.get('title', ''),
            'subtitle': doc.get('subtitle'),
            'author_names': author_names,
            'release_year': doc.get('release_year'),
            'rating': doc.get('rating'),
            'ratings_count': doc.get('ratings_count'),
            'pages': doc.get('pages'),
            'genres': doc.get('genres', []),
            'cover_url': cover_url,
            'series_names': doc.get('series_names', []),
            'featured_series': doc.get('featured_series'),
            'featured_series_position': doc.get('featured_series_position'),
        })

    total_count = raw_results.get('out_of', len(hits))
    return hits, total_count


class HardcoverSearchView(APIView):
    """Search Hardcover for books and return results annotated with DB/watchlist status."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.GET.get('q', '').strip()
        page = int(request.GET.get('page', 1))
        per_page = int(request.GET.get('per_page', 25))

        if not query:
            return Response({'error': 'Query parameter "q" is required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            service = BooksService()
            search_result = service.search_books(query, per_page=per_page, page=page)
            search_data = search_result.get('search') or search_result
        except Exception as exc:
            logger.error("Hardcover search failed: %s", exc)
            return Response({'error': 'Search service unavailable.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        hits, total_count = _parse_search_hits(search_data)

        hardcover_ids = [h['hardcover_id'] for h in hits if h['hardcover_id']]
        in_db_ids = set(
            Book.objects.filter(hardcover_id__in=hardcover_ids)
            .values_list('hardcover_id', flat=True)
        )

        book_content_type = ContentType.objects.get_for_model(Book)
        user_watchlist_book_ids = set(
            Watchlist.objects.filter(
                user=request.user,
                content_type=book_content_type,
                object_id__in=Book.objects.filter(hardcover_id__in=hardcover_ids)
                .values_list('id', flat=True),
            ).values_list('object_id', flat=True)
        )
        watchlist_hardcover_ids = set(
            Book.objects.filter(id__in=user_watchlist_book_ids)
            .values_list('hardcover_id', flat=True)
        )

        for hit in hits:
            hid = hit['hardcover_id']
            hit['in_database'] = hid in in_db_ids
            hit['in_watchlist'] = hid in watchlist_hardcover_ids

        return Response({
            'results': hits,
            'total': total_count,
            'page': page,
            'per_page': per_page,
        }, status=status.HTTP_200_OK)


class AddBookView(APIView):
    """Enqueue creation of a book by Hardcover ID (and optionally add to watchlist)."""
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, hardcover_id):
        add_to_watchlist = request.data.get('add_to_watchlist', True)

        existing = Book.objects.filter(hardcover_id=hardcover_id).first()
        if existing:
            if add_to_watchlist:
                Watchlist.objects.get_or_create(
                    user=request.user,
                    content_type=ContentType.objects.get_for_model(Book),
                    object_id=existing.id,
                )
            return Response({'status': 'exists', 'id': existing.id},
                            status=status.HTTP_200_OK)

        try:
            book = create_book(hardcover_id, add_to_watchlist=add_to_watchlist, user_id=request.user.id)
        except Exception:
            logger.exception("create_book failed for hardcover_id=%s", hardcover_id)
            return Response({'error': 'Failed to add book.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not book:
            return Response({'error': 'Book not found on Hardcover.'}, status=status.HTTP_404_NOT_FOUND)

        return Response({'status': 'added', 'id': book.id}, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------

@login_required
def book_page(request, book_id):
    book = get_object_or_404(Book, id=book_id)
    book_content_type = ContentType.objects.get_for_model(Book)

    user_watchlist = Watchlist.objects.filter(
        user=request.user,
        content_type=book_content_type,
        object_id=book.id,
    ).exists()

    watchlist_users = (
        CustomUser.objects.filter(
            watchlist_items__content_type=book_content_type,
            watchlist_items__object_id=book.id,
        ).distinct()
    )

    user_rating_data = Review.objects.filter(
        content_type=book_content_type,
        object_id=book.id,
    ).aggregate(avg_rating=Avg('rating'), rating_count=Count('rating'))

    user_avg_rating = user_rating_data['avg_rating']
    user_rating_count = user_rating_data['rating_count']
    if user_avg_rating is not None:
        user_avg_rating = round(user_avg_rating, 1)

    return render(request, 'books/book_page.html', {
        'book': book,
        'user_watchlist': user_watchlist,
        'watchlist_users': watchlist_users,
        'user_avg_rating': user_avg_rating,
        'user_rating_count': user_rating_count,
        'content_type_id': book_content_type.id,
    })


@login_required
def book_collection_page(request, collection_id):
    collection = get_object_or_404(BookCollection, id=collection_id)
    books = Book.objects.filter(collection=collection).prefetch_related('authors').order_by('series_position', 'title')

    book_content_type = ContentType.objects.get_for_model(Book)
    book_ids = books.values_list('id', flat=True)

    ratings_dict = {
        r['object_id']: r['rating']
        for r in Review.objects.filter(
            user=request.user,
            content_type=book_content_type,
            object_id__in=book_ids,
        ).values('object_id', 'rating')
    }
    watchlist_ids = set(
        Watchlist.objects.filter(
            user=request.user,
            content_type=book_content_type,
            object_id__in=book_ids,
        ).values_list('object_id', flat=True)
    )

    for b in books:
        b.user_rating = int(ratings_dict[b.id]) if ratings_dict.get(b.id) is not None else None
        b.in_watchlist = b.id in watchlist_ids

    return render(request, 'books/book_collection_page.html', {
        'collection': collection,
        'books': books,
    })


@login_required
def book_series_page(request, series_id):
    series = get_object_or_404(BookSeries, id=series_id)
    db_books = {
        b.hardcover_id: b
        for b in Book.objects.filter(series=series).prefetch_related('authors')
    }

    book_content_type = ContentType.objects.get_for_model(Book)
    db_ids = list(db_books.keys())
    local_ids = [b.id for b in db_books.values()]

    ratings_dict = {
        r['object_id']: r['rating']
        for r in Review.objects.filter(
            user=request.user,
            content_type=book_content_type,
            object_id__in=local_ids,
        ).values('object_id', 'rating')
    } if request.user.is_authenticated else {}

    watchlist_ids = set(
        Watchlist.objects.filter(
            user=request.user,
            content_type=book_content_type,
            object_id__in=local_ids,
        ).values_list('object_id', flat=True)
    ) if request.user.is_authenticated else set()

    # Fetch all books in series from Hardcover API
    api_entries = []
    if series.hardcover_id:
        svc = BooksService()
        try:
            api_entries = svc.get_series_books(series.hardcover_id)
        except Exception:
            logger.warning("Failed to fetch series books from Hardcover for series %s", series.hardcover_id)
        # Backfill description from API if not yet stored locally
        if not series.description:
            try:
                info = svc.get_series_info(series.hardcover_id)
                logger.debug("Series info from Hardcover: %s", info)
                if info and info.get('description'):
                    series.description = info['description']
                    series.save(update_fields=['description'])
            except Exception as exc:
                logger.warning("Failed to fetch series info from Hardcover for series %s: %s", series.hardcover_id, exc)

    # Build merged list: one entry per position slot.
    # Multiple books at the same position are language/format variants — keep
    # the DB-matched one first, otherwise the one with the most ratings.
    position_groups = defaultdict(list)
    for entry in api_entries:
        book_data = entry.get('book') or {}
        hc_id = book_data.get('id')
        if not hc_id:
            continue
        position_groups[entry.get('position')].append({
            'hc_id': hc_id,
            'book_data': book_data,
            'db_book': db_books.get(hc_id),
            'ratings_count': book_data.get('ratings_count') or 0,
        })

    def _build_entry(position, candidate):
        book_data = candidate['book_data']
        db_book = candidate['db_book']
        cached_image = book_data.get('cached_image') or {}
        if isinstance(cached_image, str):
            try:
                cached_image = json.loads(cached_image)
            except Exception:
                cached_image = {}
        cover_url = cached_image.get('url') if isinstance(cached_image, dict) else None
        author_names = ', '.join(
            c['author']['name']
            for c in book_data.get('contributions', [])
            if c.get('author', {}).get('name')
        )
        return {
            'position': position,
            'hardcover_id': candidate['hc_id'],
            'title': book_data.get('title', ''),
            'subtitle': book_data.get('subtitle'),
            'rating': book_data.get('rating'),
            'release_year': book_data.get('release_year'),
            'cover_url': cover_url,
            'author_names': author_names,
            'db_book': db_book,
            'in_db': db_book is not None,
            'user_rating': int(ratings_dict[db_book.id]) if db_book and ratings_dict.get(db_book.id) is not None else None,
            'in_watchlist': db_book.id in watchlist_ids if db_book else False,
        }

    series_entries = []
    for position, candidates in sorted(position_groups.items(),
                                       key=lambda x: (x[0] is None, x[0] or 0)):
        # Skip split-volume positions (x.1, x.2, x.3 etc.) — these are a
        # single book published as multiple physical volumes in some countries.
        # Only integer positions and x.5 (companion novellas) are real entries.
        if position is not None:
            frac = round(position % 1, 6)
            if frac not in (0.0, 0.5):
                continue

        # Prefer a book already in our DB; otherwise the most-rated English
        # edition (highest ratings_count, falling back to any with a rating).
        winner = next((c for c in candidates if c['db_book']), None)
        if winner is None:
            rated = [c for c in candidates if c['ratings_count'] > 0]
            winner = max(rated, key=lambda c: c['ratings_count']) if rated else candidates[0]
        series_entries.append(_build_entry(position, winner))

    # Fall back to pure DB list if API unavailable
    if not series_entries:
        for b in sorted(db_books.values(), key=lambda x: (x.series_position or 9999, x.title)):
            b.user_rating = int(ratings_dict[b.id]) if ratings_dict.get(b.id) is not None else None
            b.in_watchlist = b.id in watchlist_ids
            series_entries.append({
                'position': b.series_position,
                'hardcover_id': b.hardcover_id,
                'title': b.title,
                'subtitle': b.subtitle,
                'rating': b.rating,
                'release_year': b.published_date.year if b.published_date else None,
                'cover_url': b.image_url,
                'author_names': b.author_names,
                'db_book': b,
                'in_db': True,
                'user_rating': b.user_rating,
                'in_watchlist': b.in_watchlist,
            })

    return render(request, 'books/book_series_page.html', {
        'series': series,
        'series_entries': series_entries,
        'total_count': len(series_entries),
        'in_db_count': sum(1 for e in series_entries if e['in_db']),
    })


# ---------------------------------------------------------------------------
# Watchlist API
# ---------------------------------------------------------------------------

class WatchlistBookView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        book_id = request.data.get('id')
        if not book_id:
            return Response({'error': 'Book ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(Book)
        if Review.objects.filter(user=request.user, content_type=content_type, object_id=book_id).exists():
            return Response(
                {'error': 'Cannot add to watchlist: book has already been reviewed'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _, created = Watchlist.objects.get_or_create(
            user=request.user,
            content_type=content_type,
            object_id=book_id,
        )
        if created:
            return Response({'message': 'Book added to watchlist'}, status=status.HTTP_201_CREATED)
        return Response({'message': 'Book already in watchlist'}, status=status.HTTP_200_OK)

    def delete(self, request):
        book_id = request.data.get('id')
        if not book_id:
            return Response({'error': 'Book ID is required'}, status=status.HTTP_400_BAD_REQUEST)

        content_type = ContentType.objects.get_for_model(Book)
        try:
            Watchlist.objects.get(user=request.user, content_type=content_type, object_id=book_id).delete()
            return Response({'message': 'Book removed from watchlist'}, status=status.HTTP_204_NO_CONTENT)
        except Watchlist.DoesNotExist:
            return Response({'error': 'Book not in watchlist'}, status=status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Reviews API
# ---------------------------------------------------------------------------

class BookReviewView(APIView):
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        book_id = request.GET.get('book_id')
        if not book_id:
            return Response({'error': 'book_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        get_object_or_404(Book, id=book_id)
        content_type = ContentType.objects.get_for_model(Book)
        reviews = Review.objects.filter(
            content_type=content_type, object_id=book_id
        ).select_related('user').order_by('-date_added')
        return Response([
            {
                'id': r.id,
                'user': r.user.username,
                'rating': r.rating,
                'review_text': r.review_text,
                'date_added': r.date_added,
            }
            for r in reviews
        ], status=status.HTTP_200_OK)

    def post(self, request):
        book_id = request.data.get('book_id')
        rating = request.data.get('rating')
        review_text = request.data.get('review_text', '')
        date_added = request.data.get('date_added')

        if not book_id:
            return Response({'error': 'book_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        if not rating:
            return Response({'error': 'rating is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            rating = float(rating)
            if not (0 <= rating <= 10):
                raise ValueError
        except (ValueError, TypeError):
            return Response({'error': 'Rating must be a number between 0 and 10'}, status=status.HTTP_400_BAD_REQUEST)

        get_object_or_404(Book, id=book_id)
        content_type = ContentType.objects.get_for_model(Book)

        if Review.objects.filter(user=request.user, content_type=content_type, object_id=book_id).exists():
            return Response({'error': 'You have already reviewed this book'}, status=status.HTTP_400_BAD_REQUEST)

        today = timezone.now().date()
        if date_added:
            try:
                date_added = timezone.datetime.strptime(date_added, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            date_added = today

        if date_added == today:
            date_added_dt = timezone.now()
        elif date_added < today:
            date_added_dt = timezone.make_aware(
                timezone.datetime.combine(date_added, timezone.datetime.min.time())
            )
        else:
            return Response({'error': 'Date cannot be in the future'}, status=status.HTTP_400_BAD_REQUEST)

        review = Review.objects.create(
            user=request.user,
            content_type=content_type,
            object_id=book_id,
            rating=rating,
            review_text=review_text,
        )
        review.date_added = date_added_dt
        review.save()

        # Remove from watchlist now that it's been reviewed
        Watchlist.objects.filter(
            user=request.user, content_type=content_type, object_id=book_id
        ).delete()

        return Response({'message': 'Review added successfully', 'id': review.id}, status=status.HTTP_201_CREATED)

    def put(self, request):
        review_id = request.data.get('review_id')
        rating = request.data.get('rating')
        review_text = request.data.get('review_text')
        date_added = request.data.get('date_added')

        if not review_id:
            return Response({'error': 'review_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        review = get_object_or_404(Review, id=review_id, user=request.user)

        if rating is not None:
            try:
                rating = float(rating)
                if not (0 <= rating <= 10):
                    raise ValueError
                review.rating = rating
            except (ValueError, TypeError):
                return Response({'error': 'Rating must be a number between 0 and 10'}, status=status.HTTP_400_BAD_REQUEST)

        if review_text is not None:
            review.review_text = review_text

        today = timezone.now().date()
        if date_added:
            try:
                date_added = timezone.datetime.strptime(date_added, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
            if date_added == today:
                if review.date_added.date() == today:
                    date_added = review.date_added
                else:
                    date_added = timezone.now()
            elif date_added < today:
                date_added = timezone.make_aware(
                    timezone.datetime.combine(date_added, timezone.datetime.min.time())
                )
            else:
                return Response({'error': 'Date cannot be in the future'}, status=status.HTTP_400_BAD_REQUEST)
            review.date_added = date_added

        review.save()
        return Response({'message': 'Review updated successfully'}, status=status.HTTP_200_OK)

    def delete(self, request):
        review_id = request.data.get('review_id')
        if not review_id:
            return Response({'error': 'review_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        review = get_object_or_404(Review, id=review_id, user=request.user)
        review.delete()
        return Response({'message': 'Review deleted successfully'}, status=status.HTTP_204_NO_CONTENT)

