from django.urls import path
from . import views

app_name = 'stremio'

urlpatterns = [
    # Manifest without config (for initial addon discovery)
    path('manifest.json', views.manifest, name='manifest'),
    
    # Manifest with config
    path('<str:config>/manifest.json', views.manifest, name='manifest_with_config'),
    
    # Catalog endpoints
    path(
        '<str:config>/catalog/<str:media_type>/<str:catalog_id>.json',
        views.catalog,
        name='catalog'
    ),
    path(
        '<str:config>/catalog/<str:media_type>/<str:catalog_id>/<str:extra>.json',
        views.catalog,
        name='catalog_with_extra'
    ),
    
    # Meta endpoints
    path(
        '<str:config>/meta/<str:media_type>/<str:imdb_id>.json',
        views.meta,
        name='meta'
    ),
]
