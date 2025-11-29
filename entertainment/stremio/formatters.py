def get_poster_url(media) -> str | None:
    """Get full poster URL for a media item."""
    poster = getattr(media, 'poster', None) or getattr(media, 'poster_path', None)
    if not poster:
        return None
    
    # If it's already a full URL
    if str(poster).startswith('http'):
        return str(poster)
    
    # TMDB poster path
    if str(poster).startswith('/'):
        return f"https://image.tmdb.org/t/p/w500{poster}"
    
    return None


def to_stremio_meta(media, media_type: str, review=None) -> dict:
    """
    Convert a Movie or TVShow to Stremio meta format.
    
    Args:
        media: Movie or TVShow instance
        media_type: 'movie' or 'series'
        review: Optional Review instance or aggregated review data for the user
    """
    imdb_id = getattr(media, 'imdb_id', None)
    if not imdb_id:
        return None
    
    # Build description with review if available
    description = build_description(media, review)
    
    meta = {
        'id': imdb_id,
        'type': media_type,
        'name': getattr(media, 'name', None) or getattr(media, 'title', ''),
        'poster': get_poster_url(media),
        'description': description,
    }
    
    # Add optional fields
    if hasattr(media, 'release_date') and media.release_date:
        meta['releaseInfo'] = str(media.release_date.year)
    elif hasattr(media, 'first_air_date') and media.first_air_date:
        meta['releaseInfo'] = str(media.first_air_date.year)
    
    # Add genres
    if hasattr(media, 'genres'):
        genres = media.genres.all()
        if genres:
            meta['genres'] = [g.name for g in genres]
    
    # Add runtime for movies
    if hasattr(media, 'runtime') and media.runtime:
        meta['runtime'] = f"{media.runtime} min"
    
    # Add background/fanart
    backdrop = getattr(media, 'backdrop', None) or getattr(media, 'backdrop_path', None)
    if backdrop:
        if str(backdrop).startswith('/'):
            meta['background'] = f"https://image.tmdb.org/t/p/original{backdrop}"
        elif str(backdrop).startswith('http'):
            meta['background'] = str(backdrop)
    
    return meta


def build_description(media, review=None) -> str:
    """Build description including user's review if available."""
    parts = []
    
    # Add user review section
    if review:
        if isinstance(review, dict):
            # Aggregated review data for TV shows
            rating = review.get('avg_rating')
            review_text = review.get('latest_review')
            season_ratings = review.get('season_ratings', [])
            
            if rating:
                parts.append(f"⭐ Your Rating: {rating:.1f}/10")
            
            if season_ratings:
                season_strs = [f"S{s['season']}: {s['rating']}/10" for s in season_ratings]
                parts.append(f"📺 Season Ratings: {', '.join(season_strs)}")
            
            if review_text:
                parts.append(f"📝 Your Review: {review_text}")
        else:
            # Single review instance
            if review.rating:
                parts.append(f"⭐ Your Rating: {review.rating}/10")
            if review.review_text:
                parts.append(f"📝 Your Review: {review.review_text}")
    
    # Add separator if we have review content
    if parts:
        parts.append("─" * 30)
    
    # Add original description/overview
    overview = getattr(media, 'overview', None) or getattr(media, 'description', '')
    if overview:
        parts.append(overview)
    
    return '\n\n'.join(parts) if parts else ''


def to_stremio_catalog_item(media, media_type: str) -> dict | None:
    """Convert media to minimal Stremio catalog item format."""
    imdb_id = getattr(media, 'imdb_id', None)
    if not imdb_id:
        return None
    
    item = {
        'id': imdb_id,
        'type': media_type,
        'name': getattr(media, 'name', None) or getattr(media, 'title', ''),
        'poster': get_poster_url(media),
    }
    
    # Add optional fields for richer catalog display
    overview = getattr(media, 'overview', None) or getattr(media, 'description', '')
    if overview:
        # Truncate for catalog view
        item['description'] = overview[:200] + '...' if len(overview) > 200 else overview
    
    return item
