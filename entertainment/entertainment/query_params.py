"""Lenient parsing of numeric query-string parameters.

Malformed or out-of-range values fall back to the default / get clamped
instead of raising and turning a typo'd URL into an HTTP 500.
"""


def _clamp(value, minimum, maximum):
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def query_int(params, name, default, minimum=None, maximum=None):
    """Read ``params[name]`` as an int, clamped to ``[minimum, maximum]``."""
    try:
        value = int(params.get(name, default))
    except (TypeError, ValueError):
        value = default
    return _clamp(value, minimum, maximum)


def query_float(params, name, default, minimum=None, maximum=None):
    """Read ``params[name]`` as a finite float, clamped to ``[minimum, maximum]``."""
    try:
        value = float(params.get(name, default))
    except (TypeError, ValueError):
        value = default
    if value != value or value in (float('inf'), float('-inf')):
        value = default
    return _clamp(value, minimum, maximum)
