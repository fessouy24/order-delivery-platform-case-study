from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import get_hasher, identify_hasher


def test_password_hashing_without_database():
    user = get_user_model()(username="warehouse")
    user.set_password("example-only-safe-password")
    assert identify_hasher(user.password).algorithm == get_hasher().algorithm
    assert user.check_password("example-only-safe-password")
    assert not user.check_password("incorrect-password")
