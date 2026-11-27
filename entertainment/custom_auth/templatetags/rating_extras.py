"""Number formatting for rating badges: ``{{ votes|compact_count }}`` -> ``2.1M``."""
from django import template

register = template.Library()


@register.filter
def compact_count(value):
    """812 -> '812', 1234 -> '1.2K', 45321 -> '45K', 2134567 -> '2.1M'. Blank for non-numbers."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return ''
    for div, suffix in ((1_000_000, 'M'), (1_000, 'K')):
        if n >= div:
            v = n / div
            text = f'{v:.1f}' if v < 9.95 else f'{v:.0f}'
            if suffix == 'K' and text == '1000':  # 999,999 rounds up into millions
                return '1M'
            return text.removesuffix('.0') + suffix
    return str(n)


@register.filter
def thousands(value):
    """2134567 -> '2,134,567'. Blank for non-numbers."""
    try:
        return f'{int(value):,}'
    except (TypeError, ValueError):
        return ''
