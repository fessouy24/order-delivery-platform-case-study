import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0019_courierposition")]
    operations = [
        migrations.CreateModel(
            name="OrderWorkbookSnapshot",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("source_name", models.CharField(max_length=255)),
                ("source_sha256", models.CharField(max_length=64, unique=True)),
                ("data", models.JSONField(default=dict)),
            ],
        )
    ]
