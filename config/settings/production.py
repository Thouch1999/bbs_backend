from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# base.py's SECRET_KEY/MOCK_GATEWAY_SECRET fall back to well-known insecure
# defaults so local dev works without a .env. In production a missing env
# var must fail loudly at boot, not silently run with those defaults (the
# QR-token HMAC salt and Django's own signing both derive from SECRET_KEY).
_INSECURE_DEFAULTS = {
    "DJANGO_SECRET_KEY": "dev-insecure-secret-key-change-me",
    "MOCK_GATEWAY_SECRET": "dev-mock-gateway-secret-change-me",
}
for _env_name, _insecure_default in _INSECURE_DEFAULTS.items():
    if env(_env_name, default=_insecure_default) == _insecure_default:
        raise ImproperlyConfigured(
            f"{_env_name} must be set to a real secret in production "
            f"(it is missing or still the insecure dev default)."
        )
del _env_name, _insecure_default

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = True

SENTRY_DSN = env("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
