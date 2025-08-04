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

urlpatterns = [
    path('admin/', admin.site.urls),

    
    # Custom auth appv
    path('', include('custom_auth.urls')),
    path('movies/', include('movies.urls')),
    path('tvshows/', include('tvshows.urls')),
    path('books/', include('books.urls')),
    path('music/', include('music.urls')),
    path('games/', include('games.urls')),
    
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),    path('api/schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/schema/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),    path('favicon.ico', RedirectView.as_view(url=staticfiles_storage.url('favicon/favicon.ico'))),
    
    # Chrome DevTools PWA support and other .well-known requests
    path('.well-known/appspecific/com.chrome.devtools.json', chrome_devtools_manifest, name='chrome-devtools-manifest'),
    path('.well-known/<path:path>', well_known_handler, name='well-known-handler'),

]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns += [
        path('__debug__/', include(debug_toolbar.urls)),
        path('silk/', include('silk.urls', namespace='silk')),
    ]
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
