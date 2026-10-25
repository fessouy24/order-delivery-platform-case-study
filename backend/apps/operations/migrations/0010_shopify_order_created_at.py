from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0009_item_photo")]
    operations = [
        migrations.AddField(
            model_name="shopifyreference",
            name="order_created_at",
            field=models.DateTimeField(blank=True, null=True),
        )
    ]
