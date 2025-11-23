from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    if dictionary is None:
        return None
    return dictionary.get(str(key)) if isinstance(key, str) else dictionary.get(key)

@register.filter
def tmdb_image_size(url, size='w154'):
    if not url:
        return url
    if 'image.tmdb.org' in url and '/original/' in url:
        return url.replace('/original/', f'/{size}/')
    return url