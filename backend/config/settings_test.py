"""Portable persistence tests only. PostgreSQL remains mandatory for the application."""

from config.settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CELERY_TASK_ALWAYS_EAGER = True
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
REQUIRE_STAFF_MFA = False
