"""Explicit no-database test mode. Never used by a running application or CI DB tests."""

from config.settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.dummy"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
