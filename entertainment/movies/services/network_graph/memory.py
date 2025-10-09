"""Memory management and safeguards for graph processing.

This module provides memory monitoring, limits, and graceful degradation
to prevent OOM crashes when processing large graphs.
"""

import logging
from typing import Dict, Any, Optional, Tuple, List
from functools import wraps

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logging.warning("psutil not available - memory monitoring will use fallback values")

from .constants import MAX_NODES_DEFAULT, MAX_EDGES_DEFAULT
from .types import NodeDict, EdgeDict
from .performance import (
    get_memory_usage,
    estimate_graph_size,
    get_optimization_strategy,
)

logger = logging.getLogger(__name__)


# ========================================
# MEMORY CONFIGURATION
# ========================================

# Memory thresholds in MB
MEMORY_WARNING_THRESHOLD = 500  # Warn when graph uses >500MB
MEMORY_CRITICAL_THRESHOLD = 1000  # Critical when >1GB
SYSTEM_MEMORY_THRESHOLD = 0.8  # Critical when >80% of available RAM

# Fallback limits when memory is constrained
FALLBACK_MAX_NODES = 500
FALLBACK_MAX_EDGES = None  # No edge limit even in fallback


# ========================================
# MEMORY CHECKING
# ========================================

def check_available_memory() -> Dict[str, Any]:
    """Check current system memory availability.
    
    Returns:
        Dictionary with memory stats and recommendations
    """
    if PSUTIL_AVAILABLE:
        try:
            memory = psutil.virtual_memory()
            
            available_mb = memory.available / (1024 * 1024)
            total_mb = memory.total / (1024 * 1024)
            used_percent = memory.percent
            
            # Determine status
            if used_percent >= SYSTEM_MEMORY_THRESHOLD * 100:
                status = 'critical'
                recommendation = 'reduce'
            elif available_mb < MEMORY_CRITICAL_THRESHOLD:
                status = 'low'
                recommendation = 'limit'
            elif available_mb < MEMORY_WARNING_THRESHOLD * 2:
                status = 'moderate'
                recommendation = 'monitor'
            else:
                status = 'good'
                recommendation = 'normal'
            
            return {
                'available_mb': available_mb,
                'total_mb': total_mb,
                'used_percent': used_percent,
                'status': status,
                'recommendation': recommendation,
            }
        except Exception as e:
            logger.error(f"Failed to check memory: {e}")
    
    # Fallback when psutil not available
    logger.info("psutil not available - using conservative memory estimates")
    return {
        'available_mb': 2000,  # Conservative estimate
        'total_mb': 4096,
        'used_percent': 50,
        'status': 'moderate',
        'recommendation': 'limit',
    }


def check_graph_memory_feasibility(
    num_nodes: int,
    num_edges: int,
    include_analytics: bool = False
) -> Tuple[bool, Dict[str, Any]]:
    """Check if a graph can be processed with available memory.
    
    Args:
        num_nodes: Number of nodes in graph
        num_edges: Number of edges in graph
        include_analytics: Whether analytics will be included (currently ignored)
    
    Returns:
        Tuple of (is_feasible, details dictionary)
    """
    # Estimate graph memory requirements
    result = estimate_graph_size(num_nodes, num_edges)
    estimated_mb = result['estimated_mb']
    
    # Check available memory
    memory_info = check_available_memory()
    available_mb = memory_info['available_mb']
    
    # Check if feasible (leave some headroom)
    safety_factor = 0.7  # Use only 70% of available memory
    is_feasible = estimated_mb <= (available_mb * safety_factor)
    
    details = {
        'num_nodes': num_nodes,
        'num_edges': num_edges,
        'estimated_mb': estimated_mb,
        'available_mb': available_mb,
        'is_feasible': is_feasible,
        'memory_status': memory_info['status'],
        'recommendation': memory_info['recommendation'],
    }
    
    if not is_feasible:
        # Calculate how much we need to reduce
        target_mb = available_mb * safety_factor
        reduction_factor = target_mb / estimated_mb
        
        details['reduction_needed'] = 1 - reduction_factor
        details['target_nodes'] = int(num_nodes * reduction_factor)
        details['target_edges'] = num_edges  # Keep edges unlimited
    
    return is_feasible, details


# ========================================
# MEMORY SAFEGUARDS DECORATOR
# ========================================

def memory_safeguard(
    max_nodes: int = MAX_NODES_DEFAULT,
    max_edges: int = MAX_EDGES_DEFAULT,
    auto_reduce: bool = True
):
    """Decorator to add memory safeguards to graph building functions.
    
    Args:
        max_nodes: Maximum allowed nodes
        max_edges: Maximum allowed edges
        auto_reduce: Automatically reduce graph if too large
    
    Returns:
        Decorated function with memory checks
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Extract or estimate graph size from kwargs
            estimated_nodes = kwargs.get('max_nodes', max_nodes)
            estimated_edges = kwargs.get('max_edges', max_edges)
            include_analytics = kwargs.get('include_analytics', False)
            
            # Check memory before processing
            is_feasible, details = check_graph_memory_feasibility(
                estimated_nodes,
                estimated_edges,
                include_analytics
            )
            
            if not is_feasible:
                logger.warning(
                    f"Graph may exceed memory limits: "
                    f"estimated {details['estimated_mb']:.1f}MB, "
                    f"available {details['available_mb']:.1f}MB"
                )
                
                if auto_reduce:
                    # Automatically reduce graph size
                    logger.info(
                        f"Auto-reducing graph: "
                        f"{estimated_nodes}→{details['target_nodes']} nodes, "
                        f"{estimated_edges}→{details['target_edges']} edges"
                    )
                    kwargs['max_nodes'] = details['target_nodes']
                    kwargs['max_edges'] = details['target_edges']
                    kwargs['_memory_reduced'] = True
                else:
                    # Raise exception if auto_reduce is disabled
                    raise MemoryError(
                        f"Graph too large for available memory. "
                        f"Estimated: {details['estimated_mb']:.1f}MB, "
                        f"Available: {details['available_mb']:.1f}MB. "
                        f"Reduce graph size or enable auto_reduce."
                    )
            
            # Execute function with memory monitoring
            initial_memory = get_memory_usage()
            logger.info(f"Starting {func.__name__} (memory: {initial_memory:.1f}MB)")
            
            try:
                result = func(*args, **kwargs)
                
                final_memory = get_memory_usage()
                memory_delta = final_memory - initial_memory
                
                logger.info(
                    f"Completed {func.__name__} "
                    f"(memory: {final_memory:.1f}MB, delta: +{memory_delta:.1f}MB)"
                )
                
                # Add memory stats to result if it's a dictionary
                if isinstance(result, dict) and 'stats' in result:
                    result['stats']['memory_usage_mb'] = final_memory
                    result['stats']['memory_delta_mb'] = memory_delta
                
                return result
                
            except MemoryError as e:
                logger.error(f"Out of memory in {func.__name__}: {e}")
                
                # Try fallback with reduced size
                if auto_reduce and not kwargs.get('_fallback_attempted'):
                    logger.warning("Attempting fallback with reduced graph size")
                    kwargs['max_nodes'] = FALLBACK_MAX_NODES
                    kwargs['max_edges'] = FALLBACK_MAX_EDGES
                    kwargs['_fallback_attempted'] = True
                    return wrapper(*args, **kwargs)
                else:
                    raise
            
            finally:
                # Log final memory state
                final_memory = get_memory_usage()
                logger.info(f"Final memory after {func.__name__}: {final_memory:.1f}MB")
        
        return wrapper
    return decorator


# ========================================
# ADAPTIVE PROCESSING
# ========================================

def get_adaptive_graph_limits(
    requested_nodes: int,
    requested_edges: int,
    include_analytics: bool = False
) -> Dict[str, Any]:
    """Get adaptive graph limits based on available memory.
    
    Args:
        requested_nodes: Requested number of nodes
        requested_edges: Requested number of edges
        include_analytics: Whether analytics will be included
    
    Returns:
        Dictionary with recommended limits
    """
    memory_info = check_available_memory()
    
    # Get optimization strategy
    strategy = get_optimization_strategy(requested_nodes, requested_edges)
    
    # Start with requested limits
    max_nodes = requested_nodes
    max_edges = requested_edges
    
    # Adjust based on memory status
    if memory_info['recommendation'] == 'reduce':
        # Critical memory - use minimal limits
        max_nodes = min(max_nodes, FALLBACK_MAX_NODES)
        # No edge limiting even in critical memory
        logger.warning("Critical memory: using fallback node limits, edges unlimited")
        
    elif memory_info['recommendation'] == 'limit':
        # Low memory - reduce nodes by 50%, keep edges unlimited
        max_nodes = min(max_nodes, requested_nodes // 2)
        logger.warning("Low memory: reducing node limits by 50%, edges unlimited")
        
    elif memory_info['recommendation'] == 'monitor':
        # Moderate memory - reduce nodes by 25%, keep edges unlimited
        max_nodes = min(max_nodes, int(requested_nodes * 0.75))
        logger.info("Moderate memory: reducing node limits by 25%, edges unlimited")
    
    return {
        'max_nodes': max_nodes,
        'max_edges': max_edges,
        'memory_status': memory_info['status'],
        'optimization_strategy': strategy,
        'sampling_required': strategy['use_sampling'],
        'cache_strategy': 'aggressive' if memory_info['status'] in ['low', 'critical'] else 'normal',
    }


def process_with_memory_management(
    nodes: List[NodeDict],
    edges: List[EdgeDict],
    max_nodes: Optional[int] = None,
    max_edges: Optional[int] = None,
    auto_sample: bool = True
) -> Tuple[List[NodeDict], List[EdgeDict], Dict[str, Any]]:
    """Process graph data with automatic memory management.
    
    Args:
        nodes: List of nodes
        edges: List of edges
        max_nodes: Maximum nodes (adaptive if None)
        max_edges: Maximum edges (adaptive if None)
        auto_sample: Automatically sample if too large
    
    Returns:
        Tuple of (processed nodes, processed edges, stats)
    """
    from .sampling import sample_graph
    
    initial_memory = get_memory_usage()
    original_node_count = len(nodes)
    original_edge_count = len(edges)
    
    # Get adaptive limits if not specified
    if max_nodes is None or max_edges is None:
        limits = get_adaptive_graph_limits(
            len(nodes),
            len(edges)
        )
        max_nodes = limits['max_nodes']
        max_edges = limits['max_edges']
    
    # Check if sampling is needed
    needs_sampling = (len(nodes) > max_nodes) or (len(edges) > max_edges)
    
    if needs_sampling and auto_sample:
        logger.info(
            f"Sampling graph: {len(nodes)}→{max_nodes} nodes, "
            f"{len(edges)}→{max_edges} edges"
        )
        nodes, edges, sampling_stats = sample_graph(
            nodes, edges, max_nodes, max_edges
        )
    else:
        sampling_stats = {'sampling_applied': False}
    
    final_memory = get_memory_usage()
    
    stats = {
        **sampling_stats,
        'original_nodes': original_node_count,
        'original_edges': original_edge_count,
        'final_nodes': len(nodes),
        'final_edges': len(edges),
        'initial_memory_mb': initial_memory,
        'final_memory_mb': final_memory,
        'memory_delta_mb': final_memory - initial_memory,
    }
    
    return nodes, edges, stats
