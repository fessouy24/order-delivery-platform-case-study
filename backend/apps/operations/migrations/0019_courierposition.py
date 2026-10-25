import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("operations", "0018_financecompletion"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="CourierPosition",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("latitude", models.FloatField()),
                ("longitude", models.FloatField()),
                ("accuracy", models.FloatField()),
                ("sampled_at", models.DateTimeField()),
                ("route_key", models.CharField(max_length=64)),
                (
                    "courier",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="delivery_position",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        )
    ]
