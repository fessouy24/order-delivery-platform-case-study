"""Persistence tests against a separate PostgreSQL database; never the application DB."""

from copy import deepcopy

from config.settings import DATABASES as POSTGRES_DATABASES
from config.settings_test import *  # noqa: F403

DATABASES = deepcopy(POSTGRES_DATABASES)
DATABASES["default"]["TEST"] = {"NAME": "test_department_regression"}
