"""Docker HEALTHCHECK probe for the web container.

Hits /healthz on the local gunicorn with a Host header Django will accept
(first non-wildcard entry of ALLOWED_HOSTS), exiting non-zero if unhealthy.
"""
import os
import sys
import urllib.request


def pick_host():
    for host in os.environ.get('ALLOWED_HOSTS', '').split(','):
        host = host.strip().lstrip('.')
        if host and host != '*':
            return host
    return 'localhost'


def main():
    request = urllib.request.Request(
        'http://127.0.0.1:8000/healthz',
        headers={'Host': pick_host()},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return 0 if response.status == 200 else 1
    except Exception as exc:
        print(f'healthcheck failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
