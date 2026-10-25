import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_password_is_hashed_and_user_id_is_uuid():
    user = get_user_model().objects.create_user(
        username="warehouse", password="example-only-safe-password"
    )
    assert user.check_password("example-only-safe-password")
    assert user.password != "example-only-safe-password"
    assert user.id.version == 4
