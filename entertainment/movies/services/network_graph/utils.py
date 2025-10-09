"""Utility functions for network graph operations."""

import logging

logger = logging.getLogger(__name__)


def _safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns default value if denominator is zero.
    
    Args:
        numerator: Numerator value
        denominator: Denominator value
        default: Value to return if denominator is zero
    
    Returns:
        Result of division or default value
    """
    return numerator / denominator if denominator != 0 else default


def _clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max.
    
    Args:
        value: Value to clamp
        min_val: Minimum allowed value
        max_val: Maximum allowed value
    
    Returns:
        Clamped value
    """
    return max(min_val, min(value, max_val))


def _batch_process(items: list, batch_size: int = 100):
    """Process items in batches to reduce memory usage.
    
    Args:
        items: List of items to process
        batch_size: Size of each batch
    
    Yields:
        Batches of items
    """
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def calculate_node_size(base_size: float, count: int, scale: float, min_size: float, max_size: float) -> float:
    """Calculate node size based on count and configuration.
    
    Args:
        base_size: Base node size
        count: Count value (e.g., review count, movie count)
        scale: Scale factor
        min_size: Minimum allowed size
        max_size: Maximum allowed size
    
    Returns:
        Calculated node size
    """
    size = base_size + count * scale
    return _clamp(size, min_size, max_size)
