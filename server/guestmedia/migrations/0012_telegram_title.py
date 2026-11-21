from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("guestmedia", "0011_tablet_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="notify_telegram_title",
            field=models.CharField(blank=True, help_text="Name of the linked chat.", max_length=120),
        ),
        migrations.AlterField(
            model_name="organization",
            name="notify_telegram_chat",
            field=models.CharField(blank=True, help_text="Telegram chat or group id (linked from /manage/organization/).", max_length=64),
        ),
    ]
