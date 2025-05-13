from django import template
import json

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Template filter to get an item from a dictionary"""
    if dictionary is None:
        return None
    return dictionary.get(str(key))

@register.filter
def to_json(value):
    """Convert a Python object to JSON string"""
    return json.dumps(value)