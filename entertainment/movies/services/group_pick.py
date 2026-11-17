"""'What should we watch tonight?' — pick a movie a whole group is likely to agree on.

The recommendations the site already serves are per-user. This is the other
question: several people are in the room, each with their own taste, and one
title has to satisfy all of them.

Scoring is deliberately transparent and cheap, so the answer can explain itself
and the request stays fast enough to sit behind a dialog:

* A member's taste is their own genre affinity — how much better or worse they
  rate a genre compared with their own average. Review counts are shrunk toward
  that average (:data:`PRIOR_N`) so three ratings do not outvote three hundred.
  Affinities are built from movie *and* TV reviews, because both share
  :class:`custom_auth.models.Genre` and a person who likes one usually likes the
  other.
* A candidate's score is mostly its **weakest** member's affinity, so a title one
  person is cold on cannot win on the strength of everyone else's enthusiasm.
* Titles on anyone's watchlist get a nudge; they already wanted to see it.
* The TMDB rating is only a tiebreaker.

Candidates are limited to released films that **nobody in the group has
reviewed** — the point of the feature is something new for all of them.
"""

from dataclasses import dataclass

from django.contrib.contenttypes.models import ContentType

from custom_auth.models import Genre, Review, Watchlist
from movies.models import Movie
from tvshows.models import TVShow

# How many reviews of a genre it takes before the genre average is trusted as
# much as the member's overall average.
PRIOR_N = 3.0
# One member's coldness counts this much against the group's enthusiasm.
WEAKEST_WEIGHT = 0.6
# Per member who already has the film on their watchlist.
WATCHLIST_BONUS = 0.8
# TMDB rating tiebreaker, around a 6.5 midpoint.
TMDB_WEIGHT = 0.12
TMDB_MIDPOINT = 6.5
# A genre affinity below this is not worth mentioning in the reason line.
NOTABLE_AFFINITY = 0.25

MAX_MEMBERS = 8
MAX_LIMIT = 8
DEFAULT_LIMIT = 5
POSTER_SIZE = 'w342'


@dataclass
class GroupPick:
    movie: dict
    score: float
    match: int
    reason: str
    watchlisted_by: list
    weakest: dict | None


def parse_member_ids(raw):
    """Parse a comma-separated user id list from the query string, ignoring junk."""
    ids = []
    for part in (raw or '').split(','):
        part = part.strip()
        if part.isdigit() and int(part) not in ids:
            ids.append(int(part))
        if len(ids) >= MAX_MEMBERS:
            break
    return ids


def eligible_members():
    """Users who have rated enough to have a taste: at least one movie or TV review.

    Everyone else can still be in the room, they just cannot move the ranking, so
    offering them as a member would be pretending to know something we do not.
    """
    from django.contrib.auth import get_user_model

    movie_ct = ContentType.objects.get_for_model(Movie)
    show_ct = ContentType.objects.get_for_model(TVShow)
    rated_ids = (
        Review.objects.filter(content_type__in=[movie_ct, show_ct])
        .order_by()
        .values_list('user_id', flat=True)
        .distinct()
    )
    return list(
        get_user_model().objects.filter(id__in=list(rated_ids)).order_by('username')
    )


def _genre_affinities(user_ids):
    """Return ``{user_id: {genre_id: affinity}}`` and ``{user_id: mean_rating}``.

    Affinity is a member's shrinkage-adjusted genre average minus their overall
    average, so it is comparable between people who rate generously and people
    who rate harshly.
    """
    movie_ct = ContentType.objects.get_for_model(Movie)
    show_ct = ContentType.objects.get_for_model(TVShow)

    # Sub-reviews (season / episode subgroup) are excluded: they duplicate the
    # show-level rating and would double-count a series.
    reviews = list(
        Review.objects.filter(
            user_id__in=user_ids,
            content_type__in=[movie_ct, show_ct],
            season__isnull=True,
            episode_subgroup__isnull=True,
        ).values_list('user_id', 'content_type_id', 'object_id', 'rating')
    )
    if not reviews:
        return {}, {}

    genre_map = _media_genres(
        {oid for _u, ct, oid, _r in reviews if ct == movie_ct.id},
        {oid for _u, ct, oid, _r in reviews if ct == show_ct.id},
    )

    per_user = {}
    for user_id, ct_id, object_id, rating in reviews:
        key = (ct_id, object_id)
        for genre_id in genre_map.get(key, ()):
            bucket = per_user.setdefault(user_id, {}).setdefault(genre_id, [])
            bucket.append(rating)

    means, affinities = {}, {}
    for user_id in user_ids:
        rated = [r for u, _ct, _oid, r in reviews if u == user_id]
        if not rated:
            means[user_id] = 0.0
            affinities[user_id] = {}
            continue
        mean = sum(rated) / len(rated)
        means[user_id] = mean
        affinities[user_id] = {
            genre_id: (sum(rs) + mean * PRIOR_N) / (len(rs) + PRIOR_N) - mean
            for genre_id, rs in per_user.get(user_id, {}).items()
        }
    return affinities, means


def _media_genres(movie_ids, show_ids):
    """``{(content_type_id, object_id): [genre_id, ...]}`` for movies and shows."""
    movie_ct_id = ContentType.objects.get_for_model(Movie).id
    show_ct_id = ContentType.objects.get_for_model(TVShow).id
    mapping = {}
    if movie_ids:
        through = Movie.genres.through
        for object_id, genre_id in through.objects.filter(
            movie_id__in=movie_ids
        ).values_list('movie_id', 'genre_id'):
            mapping.setdefault((movie_ct_id, object_id), []).append(genre_id)
    if show_ids:
        through = TVShow.genres.through
        for object_id, genre_id in through.objects.filter(
            tvshow_id__in=show_ids
        ).values_list('tvshow_id', 'genre_id'):
            mapping.setdefault((show_ct_id, object_id), []).append(genre_id)
    return mapping


def _member_score(affinities, user_id, genre_ids):
    """How much ``user_id`` is expected to enjoy a title with these genres."""
    affinity = affinities.get(user_id) or {}
    values = [affinity[g] for g in genre_ids if g in affinity]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _sized_poster(url):
    if url and 'image.tmdb.org/t/p/' in url:
        return url.replace('/t/p/original/', f'/t/p/{POSTER_SIZE}/')
    return url or ''


def _runtime_display(runtime):
    hours, minutes = divmod(runtime or 0, 60)
    if not runtime:
        return ''
    return f'{hours}h {minutes}m' if hours else f'{minutes}m'


def _match_percent(score):
    """Map an affinity score onto something a human reads as a percentage."""
    return max(35, min(99, round(50 + score * 22)))


def _reason(pairs, watchlisted_by, affinities, member_ids, usernames):
    """One line explaining why this film surfaced.

    ``pairs`` is ``[(genre_id, name), ...]``; the first one is TMDB's primary
    genre and the only one worth naming in a single sentence.
    """
    if watchlisted_by:
        return f"Already on {_join_names(watchlisted_by)}'s watchlist"
    if pairs:
        primary_id, primary_name = pairs[0]
        likes = [
            usernames[user_id] for user_id in member_ids
            if (affinities.get(user_id) or {}).get(primary_id, 0) >= NOTABLE_AFFINITY
        ]
        if likes and len(likes) == len(member_ids):
            return f'Everybody rates {primary_name} above their own average'
        if len(likes) >= 2:
            return f'{_join_names(likes)} rate {primary_name} highly'
    return 'Best combined score for a film none of you have rated'


def _join_names(names):
    if not names:
        return ''
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f'{names[0]} and {names[1]}'
    return ', '.join(names[:-1]) + f' and {names[-1]}'


def empty_reason(meta):
    """Why an empty result happened, phrased for the dialog's status line."""
    names = _join_names(meta.get('members') or [])
    if not names:
        return 'Pick at least one person.'
    verb = 'has' if len(meta['members']) == 1 else 'have'
    return f'Nothing left that {names} {verb} not already rated.'


def pick_for_group(member_ids, exclude_ids=(), limit=DEFAULT_LIMIT):
    """Rank films nobody in the group has reviewed.

    Returns ``(picks, meta)`` where ``picks`` is a list of :class:`GroupPick` and
    ``meta`` carries the pool size and the members actually scored.
    """
    member_ids = [uid for uid in member_ids if uid][:MAX_MEMBERS]
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    if not member_ids:
        return [], {'pool_size': 0, 'members': []}

    from django.contrib.auth import get_user_model

    users = {u.id: u for u in get_user_model().objects.filter(id__in=member_ids)}
    member_ids = [uid for uid in member_ids if uid in users]

    movie_ct = ContentType.objects.get_for_model(Movie)
    seen_ids = (
        Review.objects.filter(content_type=movie_ct, user_id__in=member_ids)
        .order_by()
        .values_list('object_id', flat=True)
    )

    pool = (
        Movie.objects.filter(status='Released', runtime__gt=0)
        .exclude(id__in=list(seen_ids))
        .values(
            'id', 'tmdb_id', 'title', 'release_date', 'runtime', 'rating',
            'poster', 'collection_id',
        )
    )
    candidates = list(pool)
    pool_size = len(candidates)
    if not candidates:
        return [], {'pool_size': 0, 'members': [users[uid].username for uid in member_ids]}

    exclude = set(exclude_ids or ())
    if exclude:
        candidates = [c for c in candidates if c['id'] not in exclude]
        if not candidates:
            return [], {'pool_size': pool_size, 'members': [users[uid].username for uid in member_ids]}

    genre_map = _media_genres({c['id'] for c in candidates}, set())
    movie_ct_id = movie_ct.id
    affinities, _means = _genre_affinities(member_ids)

    watchlist = {}
    for movie_id, user_id in Watchlist.objects.filter(
        content_type=movie_ct, user_id__in=member_ids,
        object_id__in=[c['id'] for c in candidates],
    ).values_list('object_id', 'user_id'):
        watchlist.setdefault(movie_id, []).append(user_id)

    scored = []
    for candidate in candidates:
        genre_ids = genre_map.get((movie_ct_id, candidate['id']), [])
        per_member = [_member_score(affinities, uid, genre_ids) for uid in member_ids]
        if not per_member:
            continue
        weakest = min(per_member)
        average = sum(per_member) / len(per_member)
        score = WEAKEST_WEIGHT * weakest + (1 - WEAKEST_WEIGHT) * average
        score += WATCHLIST_BONUS * len(watchlist.get(candidate['id'], []))
        score += TMDB_WEIGHT * ((candidate['rating'] or 0) - TMDB_MIDPOINT)
        scored.append((score, candidate, genre_ids, per_member))

    if not scored:
        return [], {'pool_size': pool_size, 'members': [users[uid].username for uid in member_ids]}

    scored.sort(key=lambda row: (-row[0], row[1]['title'], row[1]['id']))

    # One film per collection: a group shortlist of three Marvel sequels is not
    # a shortlist.
    genre_names = dict(Genre.objects.values_list('id', 'name'))
    usernames = {uid: users[uid].username for uid in member_ids}
    picks, used_collections = [], set()
    for score, candidate, genre_ids, per_member in scored:
        collection_id = candidate['collection_id']
        if collection_id and collection_id in used_collections:
            continue
        watchlisted_by = [usernames[uid] for uid in watchlist.get(candidate['id'], [])]
        weakest_id = member_ids[per_member.index(min(per_member))]
        names = [(g, genre_names[g]) for g in genre_ids if g in genre_names]
        picks.append(GroupPick(
            movie={
                'id': candidate['id'],
                'tmdb_id': candidate['tmdb_id'],
                'title': candidate['title'],
                'year': candidate['release_date'].year if candidate['release_date'] else None,
                'runtime': candidate['runtime'],
                'runtime_display': _runtime_display(candidate['runtime']),
                'rating': round(candidate['rating'], 1) if candidate['rating'] is not None else None,
                'poster': _sized_poster(candidate['poster']),
                'url': f"/movies/{candidate['tmdb_id']}/",
                'genres': [name for _id, name in names[:3]],
            },
            score=round(score, 3),
            match=_match_percent(score),
            reason=_reason(names, watchlisted_by, affinities, member_ids, usernames),
            watchlisted_by=watchlisted_by,
            weakest={
                'username': usernames[weakest_id],
                'score': round(min(per_member), 2),
            } if len(member_ids) > 1 else None,
        ))
        if collection_id:
            used_collections.add(collection_id)
        if len(picks) >= limit:
            break

    return picks, {'pool_size': pool_size, 'members': [users[uid].username for uid in member_ids]}


def serialize(picks, meta):
    return {
        'members': meta['members'],
        'pool_size': meta['pool_size'],
        'picks': [
            {
                'id': p.movie['id'],
                'tmdb_id': p.movie['tmdb_id'],
                'title': p.movie['title'],
                'year': p.movie['year'],
                'runtime': p.movie['runtime'],
                'runtime_display': p.movie['runtime_display'],
                'rating': p.movie['rating'],
                'poster': p.movie['poster'],
                'url': p.movie['url'],
                'genres': p.movie['genres'],
                'score': p.score,
                'match': p.match,
                'reason': p.reason,
                'watchlisted_by': p.watchlisted_by,
                'weakest': p.weakest,
            }
            for p in picks
        ],
    }
