import base64
import json
from functools import wraps
from django.core.cache import cache
from django.http import JsonResponse
from custom_auth.models import CustomUser

AUTH_CACHE_TTL = 300  # seconds; balances DB load against stale API-key revocation


def decode_config(encoded_config: str) -> dict:
    """Decode base64 encoded config from Stremio URL."""
    try:
        # Stremio uses URL-safe base64
        padding = 4 - len(encoded_config) % 4
        if padding != 4:
            encoded_config += '=' * padding
        decoded = base64.urlsafe_b64decode(encoded_config)
        return json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return {}


def get_user_from_config(encoded_config: str) -> CustomUser | None:
    """Extract user from encoded config containing API key (cached; Stremio polls this on every request)."""
    config = decode_config(encoded_config)
    api_key = config.get('api_key')
    
    if not api_key:
        return None
    
    cache_key = f"stremio_auth_user_{api_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None  # False means "looked up already, key doesn't exist"
    
    try:
        user = CustomUser.objects.get(api_key=api_key)
    except CustomUser.DoesNotExist:
        cache.set(cache_key, False, AUTH_CACHE_TTL)
        return None
    
    cache.set(cache_key, user, AUTH_CACHE_TTL)
    return user


def require_stremio_auth(view_func):
    """Decorator to require valid API key in Stremio config."""
    @wraps(view_func)
    def wrapper(request, config, *args, **kwargs):
        # Skip auth for OPTIONS preflight requests
        if request.method == 'OPTIONS':
            return view_func(request, config, *args, **kwargs)
        
        user = get_user_from_config(config)
        if not user:
            response = JsonResponse({
                'error': 'Invalid or missing API key'
            }, status=401)
            # Add CORS headers to error response too
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
            response['Access-Control-Allow-Headers'] = 'Content-Type'
            return response
        request.stremio_user = user
        return view_func(request, config, *args, **kwargs)
    return wrapper
