import base64
import binascii
import json
import time
from functools import wraps
from django.core.cache import cache
from django.http import JsonResponse
from custom_auth.models import CustomUser

AUTH_CACHE_TTL = 300  # seconds; balances DB load against stale API-key revocation
# Per-user request budgets per minute; generous enough for a full Stremio board load, low enough to stop abuse
RATE_LIMITS = {'api': 600, 'poster': 3000}


def auth_cache_key(api_key: str) -> str:
    return f"stremio_auth_user_{api_key}"


def forget_api_key(api_key: str | None) -> None:
    """Drop the cached key->user lookup so a replaced API key stops working immediately."""
    if api_key:
        cache.delete(auth_cache_key(api_key))


def decode_config(encoded_config: str) -> dict:
    """Decode base64 encoded config from Stremio URL; anything that isn't a JSON object yields {}."""
    try:
        # Stremio uses URL-safe base64
        padding = 4 - len(encoded_config) % 4
        if padding != 4:
            encoded_config += '=' * padding
        decoded = json.loads(base64.urlsafe_b64decode(encoded_config))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def get_user_from_config(encoded_config: str) -> CustomUser | None:
    """Extract user from encoded config containing API key (cached; Stremio polls this on every request)."""
    config = decode_config(encoded_config)
    api_key = config.get('api_key')

    if not api_key or not isinstance(api_key, str):
        return None

    cache_key = auth_cache_key(api_key)
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


def is_rate_limited(user_id: int, bucket: str = 'api') -> bool:
    """Fixed one-minute window counter per user and bucket, kept in the shared cache."""
    key = f"stremio_rl_{bucket}_{user_id}_{int(time.time() // 60)}"
    if cache.add(key, 1, 60):
        return False
    try:
        count = cache.incr(key)
    except ValueError:  # window key expired between add() and incr()
        cache.set(key, 1, 60)
        return False
    return count > RATE_LIMITS[bucket]


def _json_error(message: str, status: int) -> JsonResponse:
    response = JsonResponse({'error': message}, status=status)
    # Add CORS headers to error responses too, or Stremio web can't read them
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


def require_stremio_auth(view_func):
    """Decorator to require valid API key in Stremio config."""
    @wraps(view_func)
    def wrapper(request, config, *args, **kwargs):
        # Skip auth for OPTIONS preflight requests
        if request.method == 'OPTIONS':
            return view_func(request, config, *args, **kwargs)

        user = get_user_from_config(config)
        if not user:
            return _json_error('Invalid or missing API key', 401)
        if is_rate_limited(user.id):
            return _json_error('Too many requests', 429)
        request.stremio_user = user
        return view_func(request, config, *args, **kwargs)
    return wrapper
