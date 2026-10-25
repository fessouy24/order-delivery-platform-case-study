from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("operations", "0008_alter_shopifyreference_order")]
    operations = [
        migrations.AddField(
            model_name="item",
            name="photo",
            field=models.ImageField(blank=True, upload_to="catalog"),
        ),
    ]
