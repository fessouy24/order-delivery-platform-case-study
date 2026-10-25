import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from rest_framework.test import APIClient

from tests.scenarios import operating_day as operating_day
from tests.scenarios import setup_order as setup_order


@pytest.fixture(autouse=True)
def clear_test_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user_factory(db):
    def create(role="Sales", username=None):
        user = get_user_model().objects.create_user(
            username=username or role.replace(" ", "_").lower(), password="test-long-password-5682"
        )
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
        return user

    return create


@pytest.fixture
def api():
    return APIClient()
