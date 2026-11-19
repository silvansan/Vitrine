"""WebSocket the agent keeps open, so it hears about changes within seconds."""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import Agent, hash_token
from .notify import AGENTS_GROUP


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
        await self.accept()
        await self.send_json({"type": "hello", "manifest_version": agent.manifest_version})

    async def disconnect(self, code):
        if hasattr(self, "agent_id"):
            await self.channel_layer.group_discard(AGENTS_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def manifest_changed(self, event):
        version = await current_version(self.agent_id)
        await self.send_json({"type": "manifest", "manifest_version": version})
