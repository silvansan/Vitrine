"""WebSocket the agent keeps open, so it hears about changes within seconds."""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache

from .models import Agent, hash_token
from .notify import AGENTS_GROUP
from .relay import agent_group, live_key, result_key


@database_sync_to_async
def find_agent(raw):
    if not raw:
        return None
    return Agent.objects.filter(token_hash=hash_token(raw)).first()


@database_sync_to_async
def current_version(agent_id):
    return Agent.objects.filter(pk=agent_id).values_list("manifest_version", flat=True).first()


class AgentConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        raw = ""
        for name, value in self.scope.get("headers", []):
            if name == b"authorization" and value.startswith(b"Bearer "):
                raw = value[7:].decode("utf-8", "ignore").strip()
        if not raw:
            query = parse_qs(self.scope.get("query_string", b"").decode())
            raw = (query.get("token") or [""])[0]
        agent = await find_agent(raw)
        if agent is None:
            await self.close(code=4401)
            return
        self.agent_id = agent.pk
        await self.channel_layer.group_add(AGENTS_GROUP, self.channel_name)
        await self.channel_layer.group_add(agent_group(agent.pk), self.channel_name)
        await self.accept()
        await self.send_json({"type": "hello", "manifest_version": agent.manifest_version})

    async def disconnect(self, code):
        if hasattr(self, "agent_id"):
            await self.channel_layer.group_discard(AGENTS_GROUP, self.channel_name)
            await self.channel_layer.group_discard(agent_group(self.agent_id), self.channel_name)
            await database_sync_to_async(cache.delete)(live_key(self.agent_id))

    async def receive_json(self, content, **kwargs):
        kind = content.get("type")
        if kind == "ping":
            await self.send_json({"type": "pong"})
        elif kind == "live":
            # What is on the projector, who has control: shown on guests' pages.
            await database_sync_to_async(cache.set)(live_key(self.agent_id), content, 60)
        elif kind == "command_result" and content.get("id"):
            await database_sync_to_async(cache.set)(result_key(content["id"]), content, 60)

    async def guest_command(self, event):
        """A command from a guest's phone, relayed to this agent."""
        await self.send_json(event["command"])

    async def manifest_changed(self, event):
        version = await current_version(self.agent_id)
        await self.send_json({"type": "manifest", "manifest_version": version})
