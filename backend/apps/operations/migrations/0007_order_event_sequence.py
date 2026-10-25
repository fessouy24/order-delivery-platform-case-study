from django.db import migrations, models


def backfill_sequence(apps, schema_editor):
    event_model = apps.get_model("operations", "OrderEvent")
    previous_order, number = None, 0
    for event in event_model.objects.order_by("order_id", "created_at", "id").iterator():
        if event.order_id != previous_order:
            previous_order, number = event.order_id, 0
        number += 1
        event_model.objects.filter(pk=event.pk).update(sequence=number)


class Migration(migrations.Migration):
    dependencies = [("operations", "0006_order_review_required")]
    operations = [
        migrations.AddField(
            model_name="orderevent",
            name="sequence",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.RunPython(backfill_sequence, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="orderevent",
            constraint=models.UniqueConstraint(
                fields=["order", "sequence"], name="unique_order_event_sequence"
            ),
        ),
    ]
