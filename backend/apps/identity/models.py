import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user from the first migration; groups hold roles in Phase 2."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
