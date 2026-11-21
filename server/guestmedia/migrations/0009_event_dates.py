"""Events have a period instead of a single date: from / until, either may be empty."""
import django.utils.timezone
from django.db import migrations, models


def one_day_events(apps, schema_editor):
    """Existing events lasted one day: end on their start date."""
    Service = apps.get_model("guestmedia", "Service")
    for event in Service.objects.all():
        event.ends_on = event.starts_on
        event.save(update_fields=["ends_on"])


class Migration(migrations.Migration):
    dependencies = [
        ("guestmedia", "0008_alerts_and_backups"),
    ]

    operations = [
        migrations.RenameField(model_name="service", old_name="date", new_name="starts_on"),
        migrations.AlterField(
            model_name="service",
            name="starts_on",
            field=models.DateField(blank=True, default=django.utils.timezone.localdate, help_text="Empty: already running.", null=True),
        ),
        migrations.AddField(
            model_name="service",
            name="ends_on",
            field=models.DateField(blank=True, help_text="Empty: no end. Same as the start for a one-day event.", null=True),
        ),
        migrations.AlterModelOptions(name="service", options={"ordering": ["-starts_on", "-id"], "verbose_name": "event"}),
        migrations.AlterField(
            model_name="service",
            name="is_active",
            field=models.BooleanField(default=True, help_text="Active events that are on, or start within the next days, are sent to their location's agent."),
        ),
        migrations.RunPython(one_day_events, migrations.RunPython.noop),
    ]
