import base64
import json
from functools import wraps
from django.http import JsonResponse
from custom_auth.models import CustomUser


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
    """Extract user from encoded config containing API key."""
    config = decode_config(encoded_config)
    api_key = config.get('api_key')
    
    if not api_key:
        return None
    
    try:
        return CustomUser.objects.get(api_key=api_key)
    except CustomUser.DoesNotExist:
        return None


def require_stremio_auth(view_func):
    """Decorator to require valid API key in Stremio config."""
    @wraps(view_func)
    def wrapper(request, config, *args, **kwargs):
        user = get_user_from_config(config)
        if not user:
            return JsonResponse({
                'error': 'Invalid or missing API key'
            }, status=401)
        request.stremio_user = user
        return view_func(request, config, *args, **kwargs)
    return wrapper
