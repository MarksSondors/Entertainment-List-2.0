"""Temporal Analysis for Network Graphs.

Tracks how the network evolves over time, detects trend changes,
and identifies seasonal patterns in movie watching behavior.
"""

import logging
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta
from collections import defaultdict
import statistics

from django.db.models import Count, Avg, Q
from django.contrib.contenttypes.models import ContentType
from custom_auth.models import Review
from movies.models import Movie

from ..constants import (
    TEMPORAL_ANALYSIS_MIN_DATAPOINTS,
    TEMPORAL_SMOOTHING_WINDOW,
    TREND_CHANGE_THRESHOLD,
)
from ..utils import _safe_divide

logger = logging.getLogger(__name__)


def analyze_graph_evolution(
    *,
    days_back: int = 365,
    interval_days: int = 30
) -> List[Dict[str, Any]]:
    """Analyze how the network has evolved over time.
    
    Tracks metrics like node count, edge count, density, and average ratings
    at regular intervals.
    
    Args:
        days_back: Number of days to look back (default: 365)
        interval_days: Size of each time bucket in days (default: 30)
    
    Returns:
        List of dicts with temporal metrics, ordered chronologically:
        - timestamp: Date of the measurement
        - user_count: Active users in period
        - movie_count: Movies reviewed in period
        - review_count: Total reviews in period
        - average_rating: Average rating in period
        - growth_rate: % growth from previous period
    
    Example:
        >>> evolution = analyze_graph_evolution(days_back=180, interval_days=30)
        >>> evolution[0]
        {
            'timestamp': '2024-04-01',
            'user_count': 12,
            'movie_count': 45,
            'review_count': 67,
            'average_rating': 7.8,
            'growth_rate': 0.15  # 15% growth
        }
    """
    logger.info(f"Analyzing graph evolution over {days_back} days with {interval_days}-day intervals")
    
    # Calculate time periods
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days_back)
    
    periods = []
    current_date = start_date
    
    while current_date < end_date:
        period_end = min(current_date + timedelta(days=interval_days), end_date)
        periods.append((current_date, period_end))
        current_date = period_end
    
    logger.info(f"Analyzing {len(periods)} time periods")
    
    # Get movie content type
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    # Analyze each period
    results = []
    previous_review_count = 0
    
    for period_start, period_end in periods:
        # Get reviews in this period
        reviews = Review.objects.filter(
            content_type=movie_ct,
            date_added__gte=period_start,
            date_added__lt=period_end
        )
        
        review_count = reviews.count()
        user_count = reviews.values('user').distinct().count()
        movie_count = reviews.values('object_id').distinct().count()
        avg_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0.0
        
        # Calculate growth rate
        growth_rate = 0.0
        if previous_review_count > 0:
            growth_rate = (review_count - previous_review_count) / previous_review_count
        
        results.append({
            'timestamp': period_start.strftime('%Y-%m-%d'),
            'period_start': period_start,
            'period_end': period_end,
            'user_count': user_count,
            'movie_count': movie_count,
            'review_count': review_count,
            'average_rating': round(avg_rating, 2),
            'growth_rate': round(growth_rate, 3),
        })
        
        previous_review_count = review_count
    
    logger.info(f"Analyzed {len(results)} periods")
    return results


def detect_trend_changes(
    temporal_data: List[Dict[str, Any]],
    metric: str = 'review_count',
    *,
    threshold: float = TREND_CHANGE_THRESHOLD
) -> List[Dict[str, Any]]:
    """Detect significant trend changes in temporal data.
    
    Identifies points where the trend shifts significantly (e.g., growth → decline).
    
    Args:
        temporal_data: List of temporal metrics from analyze_graph_evolution
        metric: Which metric to analyze (default: 'review_count')
        threshold: Minimum % change to consider significant (default: 0.15)
    
    Returns:
        List of detected trend changes with:
        - timestamp: When the change occurred
        - change_type: 'increase', 'decrease', or 'reversal'
        - magnitude: Size of the change
        - previous_trend: Trend before change
        - new_trend: Trend after change
    
    Example:
        >>> evolution = analyze_graph_evolution(days_back=180)
        >>> changes = detect_trend_changes(evolution, metric='review_count')
        >>> changes[0]
        {
            'timestamp': '2024-06-01',
            'change_type': 'increase',
            'magnitude': 0.35,  # 35% increase
            'previous_trend': 'flat',
            'new_trend': 'growing'
        }
    """
    if len(temporal_data) < TEMPORAL_ANALYSIS_MIN_DATAPOINTS:
        logger.warning(f"Insufficient data points for trend analysis: {len(temporal_data)}")
        return []
    
    logger.info(f"Detecting trend changes in metric '{metric}' with threshold {threshold}")
    
    # Extract metric values
    values = [d.get(metric, 0) for d in temporal_data]
    timestamps = [d.get('timestamp', '') for d in temporal_data]
    
    # Calculate moving average to smooth noise
    window_size = min(TEMPORAL_SMOOTHING_WINDOW, len(values) // 3)
    if window_size < 2:
        window_size = 2
    
    smoothed = []
    for i in range(len(values)):
        start_idx = max(0, i - window_size + 1)
        window = values[start_idx:i + 1]
        smoothed.append(sum(window) / len(window))
    
    # Detect trend changes
    changes = []
    
    for i in range(1, len(smoothed) - 1):
        prev_slope = smoothed[i] - smoothed[i - 1]
        next_slope = smoothed[i + 1] - smoothed[i]
        
        # Calculate relative change
        if smoothed[i] > 0:
            change_magnitude = abs(next_slope - prev_slope) / smoothed[i]
        else:
            change_magnitude = 0
        
        if change_magnitude > threshold:
            # Determine change type
            if prev_slope >= 0 and next_slope < prev_slope * 0.5:
                change_type = 'slowdown'
            elif prev_slope <= 0 and next_slope > abs(prev_slope) * 0.5:
                change_type = 'acceleration'
            elif prev_slope * next_slope < 0:
                change_type = 'reversal'
            elif next_slope > prev_slope * 1.5:
                change_type = 'spike'
            else:
                change_type = 'shift'
            
            # Categorize trends
            def categorize_trend(slope):
                if abs(slope) < smoothed[i] * 0.05:
                    return 'flat'
                return 'growing' if slope > 0 else 'declining'
            
            changes.append({
                'timestamp': timestamps[i],
                'change_type': change_type,
                'magnitude': round(change_magnitude, 3),
                'previous_trend': categorize_trend(prev_slope),
                'new_trend': categorize_trend(next_slope),
                'metric_value': round(smoothed[i], 2),
            })
    
    logger.info(f"Detected {len(changes)} trend changes")
    return changes


def get_temporal_metrics(
    *,
    days_back: int = 90,
    include_forecasts: bool = False
) -> Dict[str, Any]:
    """Get comprehensive temporal metrics for the network.
    
    Args:
        days_back: Number of days to analyze (default: 90)
        include_forecasts: Whether to include trend forecasts (default: False)
    
    Returns:
        Dict with temporal metrics:
        - current_period: Metrics for most recent period
        - historical_average: Average metrics over the timeframe
        - trend: Overall trend direction
        - volatility: How much metrics fluctuate
        - forecast: Predicted next period (if include_forecasts=True)
    """
    logger.info(f"Calculating temporal metrics for {days_back} days")
    
    # Get evolution data
    evolution = analyze_graph_evolution(days_back=days_back, interval_days=7)
    
    if not evolution:
        logger.warning("No evolution data available")
        return {
            'current_period': {},
            'historical_average': {},
            'trend': 'unknown',
            'volatility': 0.0,
        }
    
    # Current period (most recent)
    current = evolution[-1] if evolution else {}
    
    # Historical averages
    review_counts = [d['review_count'] for d in evolution]
    user_counts = [d['user_count'] for d in evolution]
    ratings = [d['average_rating'] for d in evolution if d['average_rating'] > 0]
    
    historical_avg = {
        'review_count': round(statistics.mean(review_counts) if review_counts else 0, 1),
        'user_count': round(statistics.mean(user_counts) if user_counts else 0, 1),
        'average_rating': round(statistics.mean(ratings) if ratings else 0, 2),
    }
    
    # Overall trend (comparing first half to second half)
    midpoint = len(evolution) // 2
    first_half_avg = statistics.mean(review_counts[:midpoint]) if midpoint > 0 else 0
    second_half_avg = statistics.mean(review_counts[midpoint:]) if midpoint < len(evolution) else 0
    
    if first_half_avg > 0:
        trend_change = (second_half_avg - first_half_avg) / first_half_avg
        if trend_change > 0.1:
            trend = 'growing'
        elif trend_change < -0.1:
            trend = 'declining'
        else:
            trend = 'stable'
    else:
        trend = 'unknown'
    
    # Volatility (coefficient of variation)
    volatility = 0.0
    if review_counts and historical_avg['review_count'] > 0:
        std_dev = statistics.stdev(review_counts) if len(review_counts) > 1 else 0
        volatility = std_dev / historical_avg['review_count']
    
    result = {
        'current_period': current,
        'historical_average': historical_avg,
        'trend': trend,
        'trend_change': round(trend_change if 'trend_change' in locals() else 0, 3),
        'volatility': round(volatility, 3),
        'data_points': len(evolution),
    }
    
    # Simple linear forecast
    if include_forecasts and len(review_counts) >= 3:
        # Use last 3 points for trend
        recent_values = review_counts[-3:]
        avg_change = (recent_values[-1] - recent_values[0]) / len(recent_values)
        forecast_value = max(0, recent_values[-1] + avg_change)
        
        result['forecast'] = {
            'review_count': round(forecast_value, 1),
            'confidence': 'low' if volatility > 0.3 else 'medium' if volatility > 0.15 else 'high'
        }
    
    logger.info(f"Temporal metrics calculated: trend={trend}, volatility={volatility:.3f}")
    return result


def calculate_growth_rate(
    metric: str = 'review_count',
    *,
    period_days: int = 30,
    comparison_period_days: int = 30
) -> Dict[str, Any]:
    """Calculate growth rate for a specific metric.
    
    Compares current period to a previous period to determine growth.
    
    Args:
        metric: Metric to measure ('review_count', 'user_count', 'movie_count')
        period_days: Days in current period (default: 30)
        comparison_period_days: Days in comparison period (default: 30)
    
    Returns:
        Dict with growth metrics:
        - current_value: Value in current period
        - previous_value: Value in comparison period
        - absolute_growth: Difference between periods
        - growth_rate: Percentage growth
        - trend: 'accelerating', 'stable', or 'decelerating'
    """
    logger.info(f"Calculating growth rate for '{metric}'")
    
    now = datetime.now()
    current_start = now - timedelta(days=period_days)
    previous_start = current_start - timedelta(days=comparison_period_days)
    
    movie_ct = ContentType.objects.get_for_model(Movie)
    
    # Current period
    current_reviews = Review.objects.filter(
        content_type=movie_ct,
        date_added__gte=current_start
    )
    
    # Previous period
    previous_reviews = Review.objects.filter(
        content_type=movie_ct,
        date_added__gte=previous_start,
        date_added__lt=current_start
    )
    
    # Calculate metrics
    if metric == 'review_count':
        current_value = current_reviews.count()
        previous_value = previous_reviews.count()
    elif metric == 'user_count':
        current_value = current_reviews.values('user').distinct().count()
        previous_value = previous_reviews.values('user').distinct().count()
    elif metric == 'movie_count':
        current_value = current_reviews.values('object_id').distinct().count()
        previous_value = previous_reviews.values('object_id').distinct().count()
    else:
        logger.error(f"Unknown metric: {metric}")
        return {'error': f'Unknown metric: {metric}'}
    
    # Calculate growth
    absolute_growth = current_value - previous_value
    growth_rate = _safe_divide(absolute_growth, previous_value)
    
    # Determine trend
    if abs(growth_rate) < 0.05:
        trend = 'stable'
    elif growth_rate > 0.2:
        trend = 'accelerating'
    elif growth_rate < -0.2:
        trend = 'decelerating'
    elif growth_rate > 0:
        trend = 'growing'
    else:
        trend = 'declining'
    
    result = {
        'metric': metric,
        'current_value': current_value,
        'previous_value': previous_value,
        'absolute_growth': absolute_growth,
        'growth_rate': round(growth_rate, 3),
        'growth_percentage': round(growth_rate * 100, 1),
        'trend': trend,
        'period_days': period_days,
        'comparison_period_days': comparison_period_days,
    }
    
    logger.info(f"Growth rate: {growth_rate:.1%} ({trend})")
    return result


def identify_seasonal_patterns(
    *,
    days_back: int = 730,  # 2 years
    metric: str = 'review_count'
) -> Dict[str, Any]:
    """Identify seasonal patterns in movie watching behavior.
    
    Analyzes historical data to find recurring patterns by month, day of week, etc.
    
    Args:
        days_back: Number of days to analyze (default: 730 = 2 years)
        metric: Metric to analyze (default: 'review_count')
    
    Returns:
        Dict with seasonal patterns:
        - by_month: Average values by month (1-12)
        - by_day_of_week: Average values by day (0=Monday, 6=Sunday)
        - peak_month: Month with highest activity
        - peak_day: Day of week with highest activity
        - seasonality_strength: How pronounced the patterns are (0-1)
    """
    logger.info(f"Identifying seasonal patterns over {days_back} days")
    
    # Get evolution data with daily intervals
    evolution = analyze_graph_evolution(days_back=days_back, interval_days=1)
    
    if len(evolution) < 30:
        logger.warning(f"Insufficient data for seasonal analysis: {len(evolution)} days")
        return {'error': 'Insufficient data'}
    
    # Group by month and day of week
    by_month = defaultdict(list)
    by_day_of_week = defaultdict(list)
    
    for period in evolution:
        if 'period_start' in period:
            date = period['period_start']
            value = period.get(metric, 0)
            
            by_month[date.month].append(value)
            by_day_of_week[date.weekday()].append(value)
    
    # Calculate averages
    month_averages = {
        month: round(statistics.mean(values), 2)
        for month, values in by_month.items() if values
    }
    
    day_averages = {
        day: round(statistics.mean(values), 2)
        for day, values in by_day_of_week.items() if values
    }
    
    # Find peaks
    peak_month = max(month_averages.items(), key=lambda x: x[1])[0] if month_averages else None
    peak_day = max(day_averages.items(), key=lambda x: x[1])[0] if day_averages else None
    
    # Calculate seasonality strength (coefficient of variation)
    month_values = list(month_averages.values())
    if month_values and len(month_values) > 1:
        month_mean = statistics.mean(month_values)
        month_std = statistics.stdev(month_values)
        seasonality_strength = min(month_std / month_mean if month_mean > 0 else 0, 1.0)
    else:
        seasonality_strength = 0.0
    
    # Month names for readability
    month_names = {
        1: 'January', 2: 'February', 3: 'March', 4: 'April',
        5: 'May', 6: 'June', 7: 'July', 8: 'August',
        9: 'September', 10: 'October', 11: 'November', 12: 'December'
    }
    
    day_names = {
        0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday',
        4: 'Friday', 5: 'Saturday', 6: 'Sunday'
    }
    
    result = {
        'by_month': month_averages,
        'by_day_of_week': day_averages,
        'peak_month': peak_month,
        'peak_month_name': month_names.get(peak_month, 'Unknown') if peak_month else None,
        'peak_day': peak_day,
        'peak_day_name': day_names.get(peak_day, 'Unknown') if peak_day else None,
        'seasonality_strength': round(seasonality_strength, 3),
        'data_points': len(evolution),
        'metric': metric,
    }
    
    logger.info(
        f"Seasonal patterns: peak_month={month_names.get(peak_month, 'Unknown')}, "
        f"peak_day={day_names.get(peak_day, 'Unknown')}, strength={seasonality_strength:.3f}"
    )
    return result
