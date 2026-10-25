from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0020_orderworkbooksnapshot")]
    operations = [
        migrations.AddField(
            model_name="order",
            name="no_answer_count",
            field=models.PositiveSmallIntegerField(default=0),
        )
    ]
