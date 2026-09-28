"""
Base settings shared by every environment. Never import this directly —
use development.py or production.py, selected via DJANGO_SETTINGS_MODULE.
"""
from datetime import timedelta
from pathlib import Path

import environ
from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-secret-key-change-me")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
    # local apps
    "apps.core",
    "apps.accounts",
    "apps.operators",
    "apps.routes",
    "apps.fleet",
    "apps.trips",
    "apps.bookings",
    "apps.payments",
    "apps.notifications",
    "apps.boarding",
    "apps.reports",
    "apps.content",
    "apps.support",
    "apps.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.LocalePreferenceMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "apps" / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.context_processors.display_currency",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

AUTH_USER_MODEL = "accounts.User"

# --- Database ---------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", default="bbms"),
        "USER": env("DB_USER", default="root"),
        "PASSWORD": env("DB_PASSWORD", default=""),
        "HOST": env("DB_HOST", default="127.0.0.1"),
        "PORT": env("DB_PORT", default="3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": (
                "SET sql_mode='STRICT_TRANS_TABLES,NO_ZERO_DATE,"
                "NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO',"
                # READ COMMITTED (not the MySQL/InnoDB default REPEATABLE READ):
                # the seat-hold path relies on this to avoid gap locks — see
                # docs/SRS.md §6.3. Set per-connection here, not per-transaction,
                # so it never collides with an already-open transaction (e.g.
                # pytest-django's per-test atomic wrapper).
                "transaction_isolation='READ-COMMITTED',"
                # Django doesn't put ENGINE=InnoDB in its own CREATE TABLE
                # DDL — it trusts the server's own default. Step 18 found
                # this dev project's WAMP MariaDB server has
                # default_storage_engine=MyISAM (a stock WAMP setting, not
                # something this project's docker-compose files ever set),
                # so every table had silently been non-transactional this
                # whole time — SELECT ... FOR UPDATE and @transaction.atomic
                # rollback semantics (CLAUDE.md's hard rule: InnoDB only)
                # were never actually in effect. Forcing this per-connection
                # means CREATE TABLE is correct regardless of the server's
                # own default, on any MySQL-compatible server.
                "default_storage_engine=InnoDB"
            ),
        },
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
    }
}

# --- Password validation -----------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- i18n / timezone ----------------------------------------------------------
LANGUAGE_CODE = "km"
LANGUAGES = [
    ("km", "Khmer"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "Asia/Phnom_Penh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "apps" / "web" / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Session-based auth for the server-rendered passenger UI (apps.web) ------------
# Fully separate from the DRF API's JWT auth below — Django's session/auth
# middleware (already installed) drives this, never touches simplejwt tokens.
LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:home"
LOGOUT_REDIRECT_URL = "web:home"

# Bootstrap 5 uses alert-danger, not Django's default "error" tag.
MESSAGE_TAGS = {
    message_constants.ERROR: "danger",
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF ----------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.bbms_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "300/min",
        "otp": "5/min",
        "login": "10/min",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "BBMS API",
    "DESCRIPTION": "Bus Booking Management System — Cambodia",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        # accounts.User.preferred_currency and bookings.Booking.currency share
        # the same KHR/USD choice set; give the shared enum one stable name.
        "CurrencyEnum": "apps.bookings.models.Booking.Currency",
        # payments.Payment.status and payments.Refund.status share the exact
        # same pending/succeeded/failed choice set.
        "PaymentRefundStatusEnum": "apps.payments.models.Payment.Status",
        # Step 15's operator serializers/query params reuse these model choice
        # sets; pin the names the pre-Step-15 schema already published so the
        # generated frontend types don't get renamed to hash-suffixed enums.
        "TripStatusEnum": "apps.trips.models.Trip.Status",
        "BookingStatusEnum": "apps.bookings.models.Booking.Status",
        "BoardingRecordStatusEnum": "apps.boarding.models.BoardingRecord.Status",
        "StaffRoleEnum": "apps.accounts.models.OperatorStaff.StaffRole",
        "CreatableStaffRoleEnum": "apps.operators.services.CREATABLE_STAFF_ROLES",
        # Step 16 (admin panel) choice sets that share a field name.
        "RoleEnum": "apps.accounts.models.User.Role",
        "OperatorStatusEnum": "apps.operators.models.Operator.Status",
        "SupportTicketStatusEnum": "apps.support.models.SupportTicket.Status",
        "ReviewNoteKindEnum": "apps.operators.models.OperatorReviewNote.Kind",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- CORS -----------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# --- Cache / Redis ----------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        # protocol=2 (RESP2): the local dev Redis (5.0.x) predates RESP3/HELLO.
        "OPTIONS": {"protocol": 2},
    }
}

# --- Celery -------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
# No result backend: nothing in this codebase calls .get()/AsyncResult on a
# dispatched task (every task is fire-and-forget — notifications, the seat
# hold sweep, etc.), and enabling one is actively harmful here. redis-py 8's
# pub/sub (which the redis result backend's consume_from/subscribe uses to
# wait for a result) now requires RESP3 unconditionally, but the local dev
# Redis (5.0.x) doesn't support RESP3/HELLO at all — no protocol setting
# resolves that contradiction, so the only correct fix is to not use a
# result backend. Confirmed live: with CELERY_RESULT_BACKEND set, a real
# task dispatch 500'd with "Maintenance notifications are only supported
# with hiredis and RESP3 parsers!" even after patching redis.Connection for
# RESP2 (below) — that patch remains correct and needed for the broker
# connection and the CACHES backend, which only need plain command/response,
# not pub/sub push parsing.
CELERY_TASK_IGNORE_RESULT = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# --- BBMS domain settings --------------------------------------------------------
SEAT_HOLD_MINUTES = env.int("SEAT_HOLD_MINUTES", default=10)
KHR_PER_USD = env.float("KHR_PER_USD", default=4100.0)
QR_TOKEN_MAX_AGE_SECONDS = env.int("QR_TOKEN_MAX_AGE_SECONDS", default=60 * 60 * 24 * 3)

# --- OTP (Step 4) -----------------------------------------------------------------
OTP_LENGTH = env.int("OTP_LENGTH", default=6)
OTP_TTL_SECONDS = env.int("OTP_TTL_SECONDS", default=5 * 60)
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)

# --- Trips / seat locking (Step 6) -------------------------------------------------
# Short-lived Redis lock serializing hold/release/confirm on one trip at a time —
# fails fast rather than piling up requests on the DB's row lock. See SRS 6.3.
SEAT_LOCK_TTL_SECONDS = env.int("SEAT_LOCK_TTL_SECONDS", default=5)
TRIP_SEARCH_CACHE_TTL_SECONDS = env.int("TRIP_SEARCH_CACHE_TTL_SECONDS", default=30)

# --- Bookings (Step 7) --------------------------------------------------------------
# Cancellation refund policy — deliberately data, not code (SRS: "put the
# cancellation policy in settings/DB, not hardcoded in the function"). Tiers
# are checked most-generous-first; the first tier whose min_hours_before_departure
# is met wins. Keep a 0-hour floor tier so every cancellation resolves to a rate.
CANCELLATION_REFUND_TIERS = [
    {"min_hours_before_departure": 48, "refund_percent": 90},
    {"min_hours_before_departure": 24, "refund_percent": 50},
    {"min_hours_before_departure": 0, "refund_percent": 0},
]
RESCHEDULE_FEE_USD = env.float("RESCHEDULE_FEE_USD", default=1.00)
BOOKING_FEE_USD = env.float("BOOKING_FEE_USD", default=0.00)

# --- Payments (Step 8) ---------------------------------------------------------------
# Real gateway credentials (filled in when merchant accounts exist — see .env.example).
ABA_PAYWAY_MERCHANT_ID = env("ABA_PAYWAY_MERCHANT_ID", default="")
ABA_PAYWAY_API_KEY = env("ABA_PAYWAY_API_KEY", default="")
WING_MERCHANT_ID = env("WING_MERCHANT_ID", default="")
WING_API_KEY = env("WING_API_KEY", default="")
ACLEDA_MERCHANT_ID = env("ACLEDA_MERCHANT_ID", default="")
ACLEDA_API_KEY = env("ACLEDA_API_KEY", default="")

# MockGateway's webhook signature key — dev/test only, never used in production.
MOCK_GATEWAY_SECRET = env("MOCK_GATEWAY_SECRET", default="dev-mock-gateway-secret-change-me")

# Hard cap on any single request body/upload (operator licence documents are
# the largest upload path; apps.core.validators.validate_licence_document
# also enforces a tighter 10MB field-level limit once the file is parsed —
# this setting caps memory/disk spend before that validation even runs).
DATA_UPLOAD_MAX_MEMORY_SIZE = env.int("DATA_UPLOAD_MAX_MEMORY_SIZE", default=15 * 1024 * 1024)
FILE_UPLOAD_MAX_MEMORY_SIZE = env.int("FILE_UPLOAD_MAX_MEMORY_SIZE", default=15 * 1024 * 1024)

# FR4.3: a pay-at-counter booking's seats are held this long before the sweep
# reclaims them, giving the passenger time to reach a physical counter.
COUNTER_PAYMENT_DEADLINE_MINUTES = env.int("COUNTER_PAYMENT_DEADLINE_MINUTES", default=120)

# --- Notifications (Step 9) ----------------------------------------------------------
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@bbms.test")
# "console" (dev/test default) logs instead of calling a real provider. Any
# other value is a real provider name the sms.py TODO still needs wiring up.
SMS_PROVIDER = env("SMS_PROVIDER", default="console")
SMS_PROVIDER_API_KEY = env("SMS_PROVIDER_API_KEY", default="")
SMS_SENDER_ID = env("SMS_SENDER_ID", default="BBMS")
# Empty (dev/test default) makes the telegram channel log instead of calling
# the real Bot API.
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="")
# How long before departure the beat task sends the reminder (SRS 7.16/4.5).
DEPARTURE_REMINDER_HOURS_BEFORE = env.int("DEPARTURE_REMINDER_HOURS_BEFORE", default=3)
NOTIFICATION_MAX_RETRIES = env.int("NOTIFICATION_MAX_RETRIES", default=3)
