import os
from pathlib import Path

from config.environment import application_environment, csv, postgres_database, required

BASE_DIR = Path(__file__).resolve().parent.parent
APP_ENV = application_environment()
DEBUG = APP_ENV == "development"
SECRET_KEY = required("DJANGO_SECRET_KEY")
if len(SECRET_KEY) < 50:
    raise ValueError("DJANGO_SECRET_KEY must contain at least 50 characters")
ALLOWED_HOSTS = csv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
if render_hostname:
    ALLOWED_HOSTS.append(render_hostname)
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ValueError("Configure explicit DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = csv("DJANGO_CSRF_TRUSTED_ORIGINS")
if render_hostname:
    CSRF_TRUSTED_ORIGINS.append(f"https://{render_hostname}")
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.identity",
    "apps.core",
    "apps.operations",
    "django_otp",
    "django_otp.plugins.otp_totp",
]
MIDDLEWARE = [
    "config.middleware.PrivateAPIResponses",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]
DATABASES = {"default": postgres_database()}
AUTH_USER_MODEL = "identity.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "config.exceptions.handle",
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
TIME_ZONE = os.environ.get("BUSINESS_TIMEZONE", "Africa/Cairo")
USE_TZ = True
LANGUAGE_CODE = "en-us"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 28800
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_REDIRECT_EXEMPT = [r"^health/(live|ready)$"]
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
# Preload enrollment is an explicit domain-owner decision, not an HTTPS prerequisite.
# Keep HSTS enabled; silence only the advisory about optional browser-list enrollment.
SECURE_HSTS_PRELOAD = os.environ.get("HSTS_PRELOAD", "false") == "true"
SILENCED_SYSTEM_CHECKS = [] if SECURE_HSTS_PRELOAD else ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# Only trust this header when API is reachable exclusively through the configured proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CELERY_BROKER_URL = required("REDIS_URL")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CELERY_BROKER_URL,
    }
}
REQUIRE_STAFF_MFA = os.environ.get("REQUIRE_STAFF_MFA", "false") == "true"
TRUST_PROXY_CLIENT_IP = os.environ.get("TRUST_PROXY_CLIENT_IP", "false") == "true"
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["apps.identity.permissions.VerifiedUser"]
CELERY_TASK_IGNORE_RESULT = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_SOFT_TIME_LIMIT = 60
CELERY_TASK_TIME_LIMIT = 90
CELERY_BEAT_SCHEDULE = {
    "finance-completion-outbox": {
        "task": "apps.operations.tasks.sweep_finance_completions",
        "schedule": 15.0,
    },
    "shopify-fulfillment-outbox": {
        "task": "apps.operations.tasks.sweep_shopify_fulfillments",
        "schedule": 15.0,
    },
    "production-work-refresh": {
        "task": "apps.operations.tasks.refresh_production_work",
        "schedule": 30.0,
    },
    "market-quotation-import": {
        "task": "apps.operations.tasks.poll_market_quotations",
        "schedule": 300.0,
    },
    "integration-outbox-sweep": {
        "task": "apps.operations.tasks.sweep_integrations",
        "schedule": 60.0,
    },
    "shopify-reconciliation": {"task": "apps.operations.tasks.poll_shopify", "schedule": 300.0},
    "delivery-quotation-batch": {
        "task": "apps.operations.tasks.daily_quotation_batch",
        "schedule": 60.0,
    },
    "operational-alerts": {"task": "apps.operations.tasks.generate_alerts", "schedule": 300.0},
}
PRODUCTION_PUSH_ENABLED = os.environ.get("PRODUCTION_PUSH_ENABLED", "false") == "true"
FCM_PROJECT_ID = os.environ.get("FCM_PROJECT_ID", "")
FCM_CREDENTIALS_FILE = os.environ.get("FCM_CREDENTIALS_FILE", "")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
