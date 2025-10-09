"""Cache management utilities for network graph operations.

This module provides caching utilities to improve performance by storing
expensive graph computations and database queries with multiple cache levels.

Cache Levels:
- SHORT (10 min): User-specific, frequently changing data
- MEDIUM (1 hour): Graph data, moderate update frequency
- LONG (24 hours): Static metadata, rarely changes
"""

import logging
import hashlib
import json
from typing import Any, Optional, Callable, Dict, List
from functools import wraps
from django.core.cache import cache

from .constants import CACHE_TIMEOUT, CACHE_TIMEOUT_SHORT, CACHE_TIMEOUT_MEDIUM, CACHE_TIMEOUT_LONG

logger = logging.getLogger(__name__)


def _get_cache_key(prefix: str, *args) -> str:
    """Generate a cache key from prefix and arguments.
    
    Args:
        prefix: Prefix for the cache key
        *args: Arguments to include in the key
    
    Returns:
        Generated cache key string
        
    Example:
        >>> _get_cache_key("graph", 123, "analytics")
        'network_graph_graph_123_analytics'
    """
    return f"network_graph_{prefix}_{'_'.join(str(arg) for arg in args)}"


def get_cached(key: str) -> Optional[Any]:
    """Get a value from cache.
    
    Args:
        key: Cache key
    
    Returns:
        Cached value or None if not found
    """
    value = cache.get(key)
    if value is not None:
        logger.debug(f"Cache hit for key: {key}")
    return value


def set_cached(key: str, value: Any, timeout: int = CACHE_TIMEOUT) -> None:
    """Set a value in cache.
    
    Args:
        key: Cache key
        value: Value to cache
        timeout: Cache timeout in seconds (default: 1 hour)
    """
    cache.set(key, value, timeout)
    logger.debug(f"Cached value for key: {key} (timeout: {timeout}s)")


def delete_cached(key: str) -> None:
    """Delete a value from cache.
    
    Args:
        key: Cache key to delete
    """
    cache.delete(key)
    logger.debug(f"Deleted cache key: {key}")


def get_or_compute(key: str, compute_fn, timeout: int = CACHE_TIMEOUT) -> Any:
    """Get value from cache or compute it if not found.
    
    Args:
        key: Cache key
        compute_fn: Function to call if cache miss (no arguments)
        timeout: Cache timeout in seconds
    
    Returns:
        Cached or computed value
        
    Example:
        >>> def expensive_computation():
        ...     return sum(range(1000000))
        >>> result = get_or_compute("my_sum", expensive_computation)
    """
    value = get_cached(key)
    if value is not None:
        return value
    
    logger.debug(f"Cache miss for key: {key}, computing...")
    value = compute_fn()
    set_cached(key, value, timeout)
    return value


def invalidate_graph_caches(user_id: Optional[int] = None) -> None:
    """Invalidate all graph-related caches.
    
    Args:
        user_id: If provided, only invalidate caches for this user
    """
    if user_id:
        # Invalidate user-specific caches
        patterns = [
            _get_cache_key("network", user_id),
            _get_cache_key("predictions", user_id),
            _get_cache_key("similarities", user_id),
        ]
        for pattern in patterns:
            delete_cached(pattern)
        logger.info(f"Invalidated graph caches for user {user_id}")
    else:
        # Invalidate all graph caches
        patterns = [
            _get_cache_key("analytics_context"),
            _get_cache_key("communities"),
            _get_cache_key("centrality"),
        ]
        for pattern in patterns:
            delete_cached(pattern)
        logger.info("Invalidated all graph caches")


# ========================================
# MULTI-LEVEL CACHING STRATEGY
# ========================================

class CacheLevel:
    """Cache level definitions for different data types."""
    SHORT = CACHE_TIMEOUT_SHORT    # 10 minutes - user-specific data
    MEDIUM = CACHE_TIMEOUT_MEDIUM  # 1 hour - graph data
    LONG = CACHE_TIMEOUT_LONG      # 24 hours - static metadata


def _hash_args(*args, **kwargs) -> str:
    """Generate a hash from function arguments for cache key."""
    # Create a deterministic string from args and kwargs
    key_data = {
        'args': args,
        'kwargs': sorted(kwargs.items())
    }
    key_string = json.dumps(key_data, sort_keys=True, default=str)
    return hashlib.md5(key_string.encode()).hexdigest()[:16]


def cached(timeout: int = CACHE_TIMEOUT, key_prefix: str = "graph"):
    """Decorator to cache function results.
    
    Args:
        timeout: Cache timeout in seconds
        key_prefix: Prefix for cache key
    
    Example:
        @cached(timeout=CacheLevel.MEDIUM, key_prefix="communities")
        def detect_communities(nodes, edges):
            # Expensive computation
            return result
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key from function name and arguments
            arg_hash = _hash_args(*args, **kwargs)
            cache_key = _get_cache_key(key_prefix, func.__name__, arg_hash)
            
            # Try to get from cache
            result = get_cached(cache_key)
            if result is not None:
                logger.debug(f"Cache hit for {func.__name__}")
                return result
            
            # Compute and cache
            logger.debug(f"Cache miss for {func.__name__}, computing...")
            result = func(*args, **kwargs)
            set_cached(cache_key, result, timeout)
            return result
        
        return wrapper
    return decorator


def cache_user_data(user_id: int):
    """Decorator for user-specific data (short cache)."""
    return cached(timeout=CacheLevel.SHORT, key_prefix=f"user_{user_id}")


def cache_graph_data(graph_type: str = "network"):
    """Decorator for graph data (medium cache)."""
    return cached(timeout=CacheLevel.MEDIUM, key_prefix=graph_type)


def cache_metadata():
    """Decorator for static metadata (long cache)."""
    return cached(timeout=CacheLevel.LONG, key_prefix="metadata")


# ========================================
# BULK CACHE OPERATIONS
# ========================================

def get_many_cached(keys: List[str]) -> Dict[str, Any]:
    """Get multiple values from cache at once.
    
    Args:
        keys: List of cache keys
    
    Returns:
        Dictionary mapping keys to values (missing keys are omitted)
    """
    results = cache.get_many(keys)
    logger.debug(f"Bulk cache get: {len(results)}/{len(keys)} hits")
    return results


def set_many_cached(data: Dict[str, Any], timeout: int = CACHE_TIMEOUT) -> None:
    """Set multiple values in cache at once.
    
    Args:
        data: Dictionary mapping keys to values
        timeout: Cache timeout in seconds
    """
    cache.set_many(data, timeout)
    logger.debug(f"Bulk cache set: {len(data)} items")


def delete_many_cached(keys: List[str]) -> None:
    """Delete multiple values from cache at once.
    
    Args:
        keys: List of cache keys to delete
    """
    cache.delete_many(keys)
    logger.debug(f"Bulk cache delete: {len(keys)} items")


# ========================================
# CACHE WARMING
# ========================================

def warm_cache_for_user(user_id: int) -> None:
    """Pre-populate cache with commonly accessed user data.
    
    Args:
        user_id: User ID to warm cache for
    """
    from .queries import get_user_rating_matrix_optimized
    
    try:
        # Pre-load user's rating matrix
        cache_key = _get_cache_key("user_ratings", user_id)
        if not get_cached(cache_key):
            logger.info(f"Warming cache for user {user_id}")
            ratings = get_user_rating_matrix_optimized([user_id])
            set_cached(cache_key, ratings, CacheLevel.SHORT)
    except Exception as e:
        logger.warning(f"Cache warming failed for user {user_id}: {e}")


def warm_cache_for_analytics() -> None:
    """Pre-populate cache with analytics data during low-traffic periods."""
    try:
        from .builders import build_movie_analytics_graph_context
        
        cache_key = _get_cache_key("analytics_context")
        if not get_cached(cache_key):
            logger.info("Warming analytics cache")
            context = build_movie_analytics_graph_context()
            set_cached(cache_key, context, CacheLevel.MEDIUM)
    except Exception as e:
        logger.warning(f"Analytics cache warming failed: {e}")


# ========================================
# CACHE STATISTICS
# ========================================

def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics for monitoring.
    
    Returns:
        Dictionary with cache statistics
    """
    # This is a simple implementation - enhance based on your cache backend
    stats = {
        'backend': cache.__class__.__name__,
        'timeout_short': CacheLevel.SHORT,
        'timeout_medium': CacheLevel.MEDIUM,
        'timeout_long': CacheLevel.LONG,
    }
    return stats
