from django.apps import AppConfig


class GuestMediaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "guestmedia"
    verbose_name = "Vitrine guest media"

    def ready(self):
        from . import signals  # noqa: F401
