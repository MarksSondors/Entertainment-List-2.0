"""Hooks for django-axes (brute-force login protection). Wired up in settings.AXES_*."""
from django.conf import settings
from django.shortcuts import render

from axes.helpers import get_cool_off


def get_client_ip(request):
    """Real client IP behind our reverse proxies (Traefik -> nginx -> Django).

    Each trusted proxy appends the address it saw to X-Forwarded-For, so the client is
    TRUSTED_PROXY_COUNT entries from the right. Anything further left came from the client
    and can be forged, so it's ignored.
    """
    count = settings.TRUSTED_PROXY_COUNT
    if count:
        hops = [h.strip() for h in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if h.strip()]
        if hops:
            return hops[-count] if len(hops) >= count else hops[0]
    return request.META.get('REMOTE_ADDR')


def lockout_response(request, original_response, credentials):
    """Re-render the login page with a lockout notice instead of Axes' plain-text 429."""
    minutes = max(1, round(get_cool_off(request).total_seconds() / 60))
    wait = 'an hour' if minutes == 60 else f'{minutes} minutes'
    return render(request, 'login_page.html', {
        'lockout_message': f'Too many failed sign-in attempts. Try again in {wait}.',
        'username': (credentials or {}).get('username', ''),
        'next': request.POST.get('next', ''),
    }, status=429)
