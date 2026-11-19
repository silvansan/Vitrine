"""Ed25519 key that signs tablet pairing codes.

The agent gets the public key in its manifest and checks pairing codes by
itself, so pairing keeps working when the venue's internet is down.
"""
import base64
import json
import time
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@lru_cache(maxsize=1)
def private_key():
    path = settings.VITRINE_KEY_PATH
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    path.chmod(0o600)
    return key


def public_key_b64():
    raw = private_key().public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def sign_pairing(grant):
    """Return the pairing code for a ScreenGrant: s1.<payload>.<signature>."""
    payload = {
        "typ": "pair",
        "agent": grant.agent_id,
        "role": grant.role,
        "name": grant.name,
        "nonce": grant.nonce,
        "exp": int(grant.expires_at.timestamp()),
        "iat": int(time.time()),
    }
    body = b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = private_key().sign(body.encode("ascii"))
    return f"s1.{body}.{b64url(signature)}"
