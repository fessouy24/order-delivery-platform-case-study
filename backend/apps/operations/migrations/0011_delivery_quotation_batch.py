import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0010_shopify_order_created_at")]
    operations = [
        migrations.AddField(
            model_name="integrationjob",
            name="website_batch_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="webhookinbox",
            name="quotation_batch_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="DeliveryQuotationBatch",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("day", models.DateField(unique=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("snapshot", models.JSONField(blank=True, null=True)),
                ("next_event", models.PositiveIntegerField(default=0)),
            ],
        ),
    ]
