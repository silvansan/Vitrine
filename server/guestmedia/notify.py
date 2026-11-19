"""Tell connected agents that their manifest changed."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import F

logger = logging.getLogger(__name__)

AGENTS_GROUP = "vitrine-agents"


def _send():
    from .models import Agent

    Agent.objects.update(manifest_version=F("manifest_version") + 1)
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(AGENTS_GROUP, {"type": "manifest.changed"})
    except Exception:  # Redis briefly unavailable: agents also poll every minute.
        logger.warning("Could not notify agents", exc_info=True)


def notify_agents():
    """Call after any change an agent should know about. Runs once per transaction."""
    connection = transaction.get_connection()
    if connection.in_atomic_block:
        marker = "_vitrine_notify_pending"
        if getattr(connection, marker, False):
            return
        setattr(connection, marker, True)

        def run():
            setattr(connection, marker, False)
            _send()

        transaction.on_commit(run)
    else:
        _send()
