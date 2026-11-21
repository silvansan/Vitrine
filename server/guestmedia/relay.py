"""Guests' phones ⇄ the vMix PC, through the server.

The agent keeps a WebSocket to the server (consumers.AgentConsumer). It reports its
live state (what is on the projector, which guests have control, access requests),
which the server keeps in the cache. A guest's command goes to the agent's channel
group; the agent checks the control grant and replies with a command_result.
"""
import time
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache


def agent_group(agent_id):
    return f"agent-{agent_id}"


def live_key(agent_id):
    return f"vitrine:live:{agent_id}"


def result_key(command_id):
    return f"vitrine:cmd:{command_id}"


def live_state(agent):
    return cache.get(live_key(agent.pk)) if agent else None


def send_command(agent, payload, wait=4.0):
    """Relay a command to the agent; returns its result dict (or a timeout error)."""
    command_id = uuid.uuid4().hex
    layer = get_channel_layer()
    async_to_sync(layer.group_send)(
        agent_group(agent.pk), {"type": "guest.command", "command": {"type": "command", "id": command_id, **payload}}
    )
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        result = cache.get(result_key(command_id))
        if result is not None:
            return result
        time.sleep(0.1)
    return {"ok": False, "error": "The vMix PC didn't answer. Is it online?"}
