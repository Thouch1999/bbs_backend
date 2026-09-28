from .production import *  # noqa: F401,F403

# Demo/presentation deploy on a bare IP with no domain or TLS certificate —
# everything else in production.py (secret-key checks, ALLOWED_HOSTS from
# env, etc.) still applies; only the HTTPS-only pieces are relaxed here so
# plain http://<ip> works. Do not reuse this module for a real deploy that
# has a domain + certificate — use production.py directly instead.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
