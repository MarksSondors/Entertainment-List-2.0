"""Performance monitoring and optimization utilities.

This module provides tools for monitoring performance, tracking metrics,
and optimizing graph operations for large datasets.
"""

import time
import logging
from functools import wraps
from typing import Callable, Any, Optional, Dict, List, Tuple
from collections import defaultdict

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logging.warning("psutil not available - memory monitoring will use estimated values")

logger = logging.getLogger(__name__)

# Performance metrics storage
_metrics = defaultdict(list)


# ========================================
# TIMING AND PROFILING
# ========================================

def timed(func: Callable) -> Callable:
    """Decorator to measure and log function execution time.
    
    Example:
        @timed
        def expensive_operation():
            # ... computation
            pass
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            elapsed = time.time() - start_time
            logger.info(f"{func.__name__} took {elapsed:.2f}s")
            
            # Store metric
            _metrics[func.__name__].append(elapsed)
    
    return wrapper


def timed_with_threshold(threshold_seconds: float = 1.0):
    """Decorator that only logs if execution exceeds threshold.
    
    Args:
        threshold_seconds: Only log if execution takes longer than this
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            
            if elapsed >= threshold_seconds:
                logger.warning(f"{func.__name__} took {elapsed:.2f}s (threshold: {threshold_seconds}s)")
            
            return result
        
        return wrapper
    return decorator


class Timer:
    """Context manager for timing code blocks.
    
    Example:
        with Timer("graph building"):
            build_graph()
    """
    
    def __init__(self, name: str, log_level: str = "info"):
        self.name = name
        self.log_level = log_level
        self.start_time = None
        self.elapsed = None
    
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, *args):
        self.elapsed = time.time() - self.start_time
        log_func = getattr(logger, self.log_level, logger.info)
        log_func(f"{self.name} completed in {self.elapsed:.2f}s")


# ========================================
# MEMORY MONITORING
# ========================================

def get_memory_usage() -> float:
    """Get current memory usage in MB.
    
    Returns:
        Current memory usage in MB (RSS - Resident Set Size)
    """
    if PSUTIL_AVAILABLE:
        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            return mem_info.rss / 1024 / 1024  # Convert to MB
        except Exception as e:
            logger.warning(f"Failed to get memory usage: {e}")
    
    # Fallback: return conservative estimate
    return 100.0  # Assume 100MB if psutil not available


def get_detailed_memory_usage() -> Dict[str, float]:
    """Get detailed memory usage statistics.
    
    Returns:
        Dictionary with memory usage in MB
    """
    if PSUTIL_AVAILABLE:
        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            
            return {
                'rss_mb': mem_info.rss / 1024 / 1024,  # Resident Set Size
                'vms_mb': mem_info.vms / 1024 / 1024,  # Virtual Memory Size
                'percent': process.memory_percent(),
            }
        except Exception as e:
            logger.warning(f"Failed to get detailed memory usage: {e}")
    
    # Fallback
    return {
        'rss_mb': 100.0,
        'vms_mb': 200.0,
        'percent': 10.0,
    }


def check_memory_limit(max_mb: float = 1024) -> bool:
    """Check if memory usage is within limits.
    
    Args:
        max_mb: Maximum allowed memory in MB
    
    Returns:
        True if within limits, False otherwise
    """
    current_mb = get_memory_usage()
    if current_mb > max_mb:
        logger.warning(f"Memory usage {current_mb:.1f}MB exceeds limit {max_mb}MB")
        return False
    return True


def memory_limited(max_mb: float = 1024):
    """Decorator to check memory usage before function execution.
    
    Args:
        max_mb: Maximum allowed memory in MB
    
    Raises:
        MemoryError: If memory usage exceeds limit
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not check_memory_limit(max_mb):
                raise MemoryError(f"Memory usage exceeds {max_mb}MB limit")
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


class MemoryMonitor:
    """Context manager for monitoring memory usage.
    
    Example:
        with MemoryMonitor("graph building") as monitor:
            build_graph()
        print(f"Peak memory: {monitor.peak_mb}MB")
    """
    
    def __init__(self, name: str):
        self.name = name
        self.start_mb = 0
        self.peak_mb = 0
        self.end_mb = 0
    
    def __enter__(self):
        self.start_mb = get_memory_usage()
        self.peak_mb = self.start_mb
        return self
    
    def __exit__(self, *args):
        self.end_mb = get_memory_usage()
        delta = self.end_mb - self.start_mb
        logger.info(
            f"{self.name} - Memory: start={self.start_mb:.1f}MB, "
            f"end={self.end_mb:.1f}MB, delta={delta:+.1f}MB"
        )


# ========================================
# PERFORMANCE METRICS
# ========================================

def get_metrics_summary() -> Dict[str, Dict[str, float]]:
    """Get summary of collected performance metrics.
    
    Returns:
        Dictionary mapping function names to timing statistics
    """
    summary = {}
    for func_name, timings in _metrics.items():
        if timings:
            summary[func_name] = {
                'count': len(timings),
                'total': sum(timings),
                'avg': sum(timings) / len(timings),
                'min': min(timings),
                'max': max(timings),
            }
    return summary


def reset_metrics() -> None:
    """Reset all collected metrics."""
    _metrics.clear()
    logger.debug("Performance metrics reset")


def log_metrics_summary() -> None:
    """Log a summary of all collected metrics."""
    summary = get_metrics_summary()
    if not summary:
        logger.info("No performance metrics collected")
        return
    
    logger.info("=== Performance Metrics Summary ===")
    for func_name, stats in sorted(summary.items()):
        logger.info(
            f"{func_name}: "
            f"calls={stats['count']}, "
            f"avg={stats['avg']:.2f}s, "
            f"min={stats['min']:.2f}s, "
            f"max={stats['max']:.2f}s"
        )


# ========================================
# BATCH PROCESSING
# ========================================

def batch_process_with_progress(
    items: List[Any],
    process_func: Callable,
    batch_size: int = 100,
    log_progress: bool = True
) -> List[Any]:
    """Process items in batches with progress logging.
    
    Args:
        items: List of items to process
        process_func: Function to call for each batch
        batch_size: Size of each batch
        log_progress: Whether to log progress
    
    Returns:
        List of results from processing each batch
    """
    results = []
    total_batches = (len(items) + batch_size - 1) // batch_size
    
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        batch_num = i // batch_size + 1
        
        if log_progress:
            logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} items)")
        
        batch_result = process_func(batch)
        results.append(batch_result)
    
    return results


# ========================================
# GRAPH SIZE ESTIMATION
# ========================================

def estimate_graph_size(num_nodes: int, num_edges: int) -> Dict[str, Any]:
    """Estimate memory and performance impact of a graph.
    
    Args:
        num_nodes: Number of nodes in the graph
        num_edges: Number of edges in the graph
    
    Returns:
        Dictionary with size estimates and recommendations
    """
    # Rough estimates (actual size depends on data structures)
    node_size_bytes = 500  # Average bytes per node (with all attributes)
    edge_size_bytes = 200  # Average bytes per edge
    
    estimated_mb = (num_nodes * node_size_bytes + num_edges * edge_size_bytes) / (1024 * 1024)
    
    # Performance category
    if num_nodes < 100:
        category = "tiny"
        recommendation = "No optimizations needed"
    elif num_nodes < 500:
        category = "small"
        recommendation = "Good performance expected"
    elif num_nodes < 1000:
        category = "medium"
        recommendation = "Consider enabling caching"
    elif num_nodes < 5000:
        category = "large"
        recommendation = "Enable caching and consider sampling"
    else:
        category = "very_large"
        recommendation = "Requires aggressive sampling and optimization"
    
    return {
        'num_nodes': num_nodes,
        'num_edges': num_edges,
        'estimated_mb': round(estimated_mb, 2),
        'category': category,
        'recommendation': recommendation,
        'density': (2 * num_edges) / (num_nodes * (num_nodes - 1)) if num_nodes > 1 else 0,
    }


# ========================================
# PROGRESSIVE LOADING
# ========================================

def should_use_sampling(num_nodes: int, num_edges: int, threshold: int = 1000) -> bool:
    """Determine if graph sampling should be used.
    
    Args:
        num_nodes: Number of nodes
        num_edges: Number of edges
        threshold: Node count threshold for sampling
    
    Returns:
        True if sampling recommended
    """
    return num_nodes > threshold


def get_optimization_strategy(num_nodes: int, num_edges: int) -> Dict[str, Any]:
    """Get recommended optimization strategy for a graph.
    
    Args:
        num_nodes: Number of nodes
        num_edges: Number of edges
    
    Returns:
        Dictionary with optimization recommendations
    """
    estimate = estimate_graph_size(num_nodes, num_edges)
    
    strategy = {
        'use_caching': num_nodes > 100,
        'use_sampling': should_use_sampling(num_nodes, num_edges),
        'use_pagination': num_nodes > 500,
        'use_webgl': num_nodes > 500,
        'max_edges_display': num_edges,  # No limit on edge display
        'batch_size': 100 if num_nodes > 1000 else 500,
        'cache_timeout': 600 if num_nodes > 1000 else 3600,  # Shorter cache for large graphs
        'estimate': estimate,
    }
    
    return strategy
