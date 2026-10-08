from django.conf import settings


def site(request):
    """Expose the public site domain (DOMAIN in .env) to templates for branding."""
    return {'site_domain': settings.SITE_DOMAIN}
