from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0021_order_no_answer_count")]
    operations = [
        migrations.AddField(
            model_name="shopifyreference",
            name="commercial_snapshot",
            field=models.JSONField(default=dict),
        )
    ]
