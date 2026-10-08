"""
URL configuration for entertainment project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from django.views.generic.base import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.sitemaps.views import sitemap

from .sitemaps import sitemaps as sitemap_registry

@require_http_methods(["GET"])
def chrome_devtools_manifest(request):
    """Handle Chrome DevTools PWA manifest request"""
    return JsonResponse({
        "version": "1.0",
        "app_id": "entertainment-list",
        "app_name": "Entertainment List"
    })

@require_http_methods(["GET"])
def well_known_handler(request, path):
    """Handle various .well-known requests"""
    if path == "appspecific/com.chrome.devtools.json":
        return chrome_devtools_manifest(request)
    
    # Return empty JSON for other .well-known requests to avoid 404s
    return JsonResponse({}, status=204)

@require_http_methods(["GET"])
def healthz(request):
    """Liveness/readiness probe for container healthchecks: verifies DB and cache are reachable."""
    from django.core.cache import cache
    from django.db import connection

    try:
        connection.ensure_connection()
        cache.get('healthz')
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})

ROBOTS_DISALLOW = ['/admin/', '/api/', '/watchlist/', '/profile/', '/accounts/']


@require_http_methods(["GET"])
def robots_txt(request):
    """robots.txt with a Sitemap URL built from the request host (no hardcoded domain)."""
    from django.http import HttpResponse
    from django.urls import reverse

    lines = ['User-agent: *']
    lines += [f'Disallow: {path}' for path in ROBOTS_DISALLOW]
    lines += ['', f"Sitemap: {request.build_absolute_uri(reverse('django.contrib.sitemaps.views.sitemap'))}", '']
    return HttpResponse('\n'.join(lines), content_type='text/plain')

urlpatterns = [
    path('healthz', healthz, name='healthz'),
    path('admin/', admin.site.urls),
    
    # Custom auth appv
    path('', include('custom_auth.urls')),
    path('movies/', include('movies.urls')),
    path('tvshows/', include('tvshows.urls')),
    path('books/', include('books.urls')),
    path('music/', include('music.urls')),
    path('games/', include('games.urls')),
    path('api/notifications/', include('notifications.urls')),
    path('stremio/', include('stremio.urls')),
    path('explorer/', include('explorer.urls')),
    
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),    path('favicon.ico', RedirectView.as_view(url=staticfiles_storage.url('favicon/favicon.ico'))),
    
    # Chrome DevTools PWA support and other .well-known requests
    path('.well-known/appspecific/com.chrome.devtools.json', chrome_devtools_manifest, name='chrome-devtools-manifest'),
    path('.well-known/<path:path>', well_known_handler, name='well-known-handler'),

    # SEO: robots.txt and sitemap.xml
    path('robots.txt', robots_txt, name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': sitemap_registry}, name='django.contrib.sitemaps.views.sitemap'),

]

# Project-wide 404 handler (Win98-styled). Rendered whenever a page 404s,
# including movie/TV pages whose TMDB id has gone stale.
handler404 = 'movies.views.tmdb_404'

if settings.DEBUG:
    try:
        import debug_toolbar
        urlpatterns += [
            path('__debug__/', include(debug_toolbar.urls)),
        ]
    except ImportError:
        pass
    
    try:
        urlpatterns += [
            path('silk/', include('silk.urls', namespace='silk')),
        ]
    except ImportError:
        pass
        
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
