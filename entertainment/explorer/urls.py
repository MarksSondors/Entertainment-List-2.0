from django.urls import include, path
from rest_framework.routers import DefaultRouter

from books.explorer_views import BookExplorerViewSet
from custom_auth.explorer_views import PersonExplorerViewSet
from games.explorer_views import GameExplorerViewSet
from movies.explorer_views import MovieExplorerViewSet
from tvshows.explorer_views import TVShowExplorerViewSet

from .all_media import all_media
from .tree import tree
from .views import explorer_page


router = DefaultRouter()
router.register(r"movies", MovieExplorerViewSet, basename="explorer-movies")
router.register(r"tvshows", TVShowExplorerViewSet, basename="explorer-tvshows")
router.register(r"books", BookExplorerViewSet, basename="explorer-books")
router.register(r"games", GameExplorerViewSet, basename="explorer-games")
router.register(r"people", PersonExplorerViewSet, basename="explorer-people")


urlpatterns = [
    path("", explorer_page, name="explorer_page"),
    path("api/tree/", tree, name="explorer_tree"),
    path("api/all/", all_media, name="explorer_all_media"),
    path("api/", include(router.urls)),
]
