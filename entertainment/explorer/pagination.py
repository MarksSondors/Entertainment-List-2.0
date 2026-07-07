"""Pagination shared by all explorer list endpoints."""
from rest_framework.pagination import CursorPagination, PageNumberPagination


class ExplorerPagination(PageNumberPagination):
    page_size = 48
    page_size_query_param = "page_size"
    max_page_size = 200


class PeopleCursorPagination(CursorPagination):
    """Opt-in cursor pagination for very deep People pages.

    Frontend switches to this when the numbered result set exceeds ~5000
    entries; falls back to `ExplorerPagination` for typical browsing.
    """

    page_size = 48
    max_page_size = 200
    ordering = "-date_of_birth"
    page_size_query_param = "page_size"
    cursor_query_param = "cursor"
