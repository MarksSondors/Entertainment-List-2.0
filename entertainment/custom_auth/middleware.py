from django.utils import timezone


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
