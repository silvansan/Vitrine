"""Organizations (3/3): drop the old guest columns, link uploads to people."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guestmedia", "0004_organizations_data"),
    ]

    operations = [
        migrations.RemoveField(model_name="mediaasset", name="uploaded_by"),
        migrations.RenameField(model_name="mediaasset", old_name="uploader", new_name="uploaded_by"),
        migrations.AlterField(
            model_name="mediaasset",
            name="uploaded_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="uploads", to="guestmedia.person"),
        ),
        migrations.RemoveField(model_name="guest", name="name"),
        migrations.RemoveField(model_name="guest", name="token"),
        migrations.RemoveField(model_name="guest", name="can_upload"),
        migrations.RemoveField(model_name="guest", name="can_browse"),
        migrations.RemoveField(model_name="guest", name="expires_at"),
        migrations.AlterField(
            model_name="guest",
            name="person",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="guests", to="guestmedia.person"),
        ),
        migrations.AlterModelOptions(name="guest", options={"ordering": ["person__name"]}),
        migrations.AddConstraint(
            model_name="guest",
            constraint=models.UniqueConstraint(fields=("service", "person"), name="person_once_per_event"),
        ),
        migrations.AlterModelOptions(name="service", options={"ordering": ["-date", "-id"], "verbose_name": "event"}),
        migrations.AlterField(
            model_name="service",
            name="is_active",
            field=models.BooleanField(default=True, help_text="Active events from yesterday to the next days are sent to their location's agent."),
        ),
        migrations.AlterField(
            model_name="service",
            name="auto_approve",
            field=models.BooleanField(default=False, help_text="Skip the approval queue for uploads from this event's people."),
        ),
    ]
