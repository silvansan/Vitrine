from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Guest, MediaAsset, MediaSet, Marker, ScreenGrant, Service, SetItem
from .notify import notify_agents

WATCHED = (Service, Guest, MediaAsset, MediaSet, SetItem, Marker, ScreenGrant)


@receiver(post_save)
@receiver(post_delete)
def content_changed(sender, **kwargs):
    if sender in WATCHED:
        update_fields = kwargs.get("update_fields")
        # Bookkeeping writes don't change what the agent shows.
        if update_fields and set(update_fields) <= {"last_used"}:
            return
        notify_agents()
