import heapq
import logging
import time
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)


class _QueryStats:
    """connection.execute_wrapper hook: counts and times every query in a request."""

    SLOWEST_KEPT = 5

    def __init__(self):
        self.count = 0
        self.total_seconds = 0.0
        self.by_sql = Counter()
        self.slowest = []  # min-heap of (seconds, sql)

    def __call__(self, execute, sql, params, many, context):
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed = time.perf_counter() - start
            self.count += 1
            self.total_seconds += elapsed
            self.by_sql[sql] += 1
            if len(self.slowest) < self.SLOWEST_KEPT:
                heapq.heappush(self.slowest, (elapsed, sql))
            elif elapsed > self.slowest[0][0]:
                heapq.heapreplace(self.slowest, (elapsed, sql))


class SlowRequestMiddleware:
    """
    Record requests slower than settings.SLOW_REQUEST_MS (default 1000) as
    SlowRequest rows with their query count, DB time, most repeated query and
    slowest queries, so slow pages can be diagnosed from the Django admin.
    Rows older than SLOW_REQUEST_RETENTION_DAYS (default 30) are pruned.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        stats = _QueryStats()
        start = time.perf_counter()
        with connection.execute_wrapper(stats):
            response = self.get_response(request)
        duration_ms = (time.perf_counter() - start) * 1000

        threshold_ms = getattr(settings, 'SLOW_REQUEST_MS', 1000)
        if threshold_ms and duration_ms >= threshold_ms:
            try:
                self._record(request, response, duration_ms, stats)
            except Exception:
                # Never let bookkeeping break the actual response
                logger.warning("Could not record slow request %s", request.path, exc_info=True)
        return response

    def _record(self, request, response, duration_ms, stats):
        from custom_auth.models import SlowRequest

        match = getattr(request, 'resolver_match', None)
        path = request.path
        if match is not None and 'config' in match.kwargs:
            # Stremio URLs carry the user's API key in <config>; store the pattern
            path = '/' + match.route

        user = getattr(request, 'user', None)
        username = user.get_username() if user is not None and user.is_authenticated else ''

        repeated_query, repeated_count = ('', 0)
        if stats.by_sql:
            repeated_query, repeated_count = stats.by_sql.most_common(1)[0]
        slowest = '\n\n'.join(
            f"{seconds * 1000:.0f} ms\n{sql}"
            for seconds, sql in sorted(stats.slowest, reverse=True)
        )

        SlowRequest.objects.create(
            method=request.method[:10],
            path=path[:500],
            view_name=(match.view_name if match is not None else '')[:200],
            username=username[:150],
            status_code=response.status_code,
            duration_ms=round(duration_ms),
            db_time_ms=round(stats.total_seconds * 1000),
            query_count=stats.count,
            repeated_query=repeated_query,
            repeated_query_count=repeated_count,
            slowest_queries=slowest,
        )
        retention_days = getattr(settings, 'SLOW_REQUEST_RETENTION_DAYS', 30)
        SlowRequest.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=retention_days)
        ).delete()


class AccessLogTagsMiddleware:
    """
    Tag every response with the signed-in username and the URL route that
    handled it, so Traefik's access log (and the GoAccess dashboard at
    /traefik/stats/) can break traffic and response times down per user and
    per page type, not just per raw URL.

    The values are only the requester's own username and an internal route
    name; Traefik records them from these response headers.
    """

    USER_HEADER = 'X-Entlist-User'
    VIEW_HEADER = 'X-Entlist-View'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            response[self.USER_HEADER] = user.get_username()
        else:
            stremio_user = getattr(request, 'stremio_user', None)
            if stremio_user is not None:
                response[self.USER_HEADER] = stremio_user.get_username()

        match = getattr(request, 'resolver_match', None)
        if match is not None and match.view_name:
            response[self.VIEW_HEADER] = match.view_name

        return response


class UpdateLastActiveMiddleware:
    """
    Middleware that updates the user's last_active timestamp on each request.
    Uses a throttle to avoid updating on every single request.
    """
    
    # Only update every 60 seconds to reduce database writes
    UPDATE_INTERVAL_SECONDS = 60
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Only update for authenticated users
        if request.user.is_authenticated:
            self._update_last_active(request)
        
        return response
    
    def _update_last_active(self, request):
        """Update user's last_active timestamp with throttling."""
        user = request.user
        now = timezone.now()
        
        # Check if we should update (throttle to reduce DB writes)
        should_update = False
        
        if not user.last_active:
            should_update = True
        else:
            time_since_last_update = (now - user.last_active).total_seconds()
            if time_since_last_update >= self.UPDATE_INTERVAL_SECONDS:
                should_update = True
        
        if should_update:
            # Use update() to avoid triggering signals and save overhead
            from custom_auth.models import CustomUser
            CustomUser.objects.filter(pk=user.pk).update(last_active=now)
            # Also update the in-memory object for consistency
            user.last_active = now
