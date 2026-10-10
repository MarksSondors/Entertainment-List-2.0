from django.utils import timezone


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
