"""Background tasks that pre-render Stremio poster overlays ahead of demand."""


def warm_poster(media_type: str, imdb_id: str, user_id: int, ctx: str | None = None) -> None:
    """Render+cache a single poster; silently no-ops if the user/media no longer exist."""
    from custom_auth.models import CustomUser
    from movies.models import Movie
    from tvshows.models import TVShow

    from .poster import get_cached_poster

    model = {'movie': Movie, 'series': TVShow}.get(media_type)
    if model is None:
        return
    try:
        user = CustomUser.objects.get(id=user_id)
        media = model.objects.get(imdb_id=imdb_id)
    except (CustomUser.DoesNotExist, model.DoesNotExist):
        return

    get_cached_poster(media, media_type, user, ctx)


def warm_user_posters(user_id: int) -> None:
    """Refresh one user's watchlist poster overlays so they're ready before they open Stremio."""
    from custom_auth.models import CustomUser, Watchlist
    from django.contrib.contenttypes.models import ContentType
    from movies.models import Movie
    from tvshows.models import TVShow

    from .poster import get_cached_poster

    try:
        user = CustomUser.objects.get(id=user_id)
    except CustomUser.DoesNotExist:
        return

    movie_ct = ContentType.objects.get_for_model(Movie)
    movie_ids = Watchlist.objects.filter(user=user, content_type=movie_ct).values_list('object_id', flat=True)
    movies = Movie.objects.filter(id__in=list(movie_ids)).exclude(imdb_id__isnull=True).exclude(imdb_id='')
    for movie in movies:
        get_cached_poster(movie, 'movie', user, None)

    tvshow_ct = ContentType.objects.get_for_model(TVShow)
    show_ids = Watchlist.objects.filter(user=user, content_type=tvshow_ct).values_list('object_id', flat=True)
    shows = TVShow.objects.filter(id__in=list(show_ids)).exclude(imdb_id__isnull=True).exclude(imdb_id='')
    for show in shows:
        get_cached_poster(show, 'series', user, None)
        get_cached_poster(show, 'series', user, 'cw')


def warm_all_posters() -> None:
    """Scheduled entrypoint: fan out poster warming for every addon user."""
    from django.db.models import Q
    from django_q.tasks import async_task

    from custom_auth.models import CustomUser

    user_ids = CustomUser.objects.exclude(Q(api_key__isnull=True) | Q(api_key='')).values_list('id', flat=True)
    for user_id in user_ids:
        async_task('stremio.tasks.warm_user_posters', user_id)


def warm_discover_external(user_id: int) -> None:
    """Refresh one user's Discover (external TMDB) cache ahead of its expiry."""
    from custom_auth.models import CustomUser

    from .views import _build_discover_external

    try:
        user = CustomUser.objects.get(id=user_id)
    except CustomUser.DoesNotExist:
        return

    _build_discover_external(user)


def warm_all_discover_external() -> None:
    """Scheduled entrypoint: fan out Discover cache warming for every addon user."""
    from django.db.models import Q
    from django_q.tasks import async_task

    from custom_auth.models import CustomUser

    user_ids = CustomUser.objects.exclude(Q(api_key__isnull=True) | Q(api_key='')).values_list('id', flat=True)
    for user_id in user_ids:
        async_task('stremio.tasks.warm_discover_external', user_id)
