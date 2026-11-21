"""A permanent link per location (/t/<slug>/) that leads paired tablets to its vMix PC."""
import secrets

from django.db import migrations, models

import guestmedia.models


def fill(apps, schema_editor):
    Location = apps.get_model("guestmedia", "Location")
    for loc in Location.objects.all():
        loc.tablet_slug = secrets.token_urlsafe(9)
        loc.save(update_fields=["tablet_slug"])


class Migration(migrations.Migration):
    dependencies = [
        ("guestmedia", "0010_site_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="tablet_slug",
            field=models.CharField(max_length=24, null=True),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="location",
            name="tablet_slug",
            field=models.CharField(default=guestmedia.models.new_tablet_slug, max_length=24, unique=True),
        ),
    ]
