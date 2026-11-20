"""Organizations, locations and roaming people (1/3: new tables and columns).

Existing data moves into one organization "Main": each agent gets a location,
each service (event) goes to the first location, and guests with the same
name become one person. Their other links keep working as aliases.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import guestmedia.models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("guestmedia", "0002_audio"),
    ]

    operations = [
        migrations.CreateModel(
            name="Organization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("share_all", models.BooleanField(default=False, help_text="Share every approved, non-private file. Otherwise only files marked 'shared'.")),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("shares_with", models.ManyToManyField(blank=True, help_text="Organizations that may use our shared files.", related_name="shared_from", to="guestmedia.organization")),
                ("staff", models.ManyToManyField(blank=True, help_text="Staff accounts that manage this organization. Superusers see every organization.", related_name="vitrine_orgs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Location",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("agent", models.OneToOneField(blank=True, help_text="The vMix PC at this location.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="location", to="guestmedia.agent")),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="locations", to="guestmedia.organization")),
            ],
            options={"ordering": ["organization__name", "name"]},
        ),
        migrations.CreateModel(
            name="Person",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("token", models.CharField(default=guestmedia.models.new_guest_token, max_length=64, unique=True)),
                ("can_upload", models.BooleanField(default=True)),
                ("can_browse", models.BooleanField(default=True, help_text="May pick files from the shared library.")),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="people", to="guestmedia.organization")),
            ],
            options={"ordering": ["name"], "verbose_name_plural": "people"},
        ),
        migrations.CreateModel(
            name="PersonAlias",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(max_length=64, unique=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="aliases", to="guestmedia.person")),
            ],
        ),
        migrations.AddField(
            model_name="service",
            name="location",
            field=models.ForeignKey(blank=True, help_text="The agent of this location shows the event and downloads its files ahead of time.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="events", to="guestmedia.location"),
        ),
        migrations.AddField(
            model_name="guest",
            name="person",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="guests", to="guestmedia.person"),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="organization",
            field=models.ForeignKey(blank=True, help_text="Whose library the file is in.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assets", to="guestmedia.organization"),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="shared",
            field=models.BooleanField(default=False, help_text="Other organizations we share with may use it (always, if the organization shares everything)."),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="uploader",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="guestmedia.person"),
        ),
    ]
