"""How far a video's conversion is, shared through the cache (Redis) between the worker and the
web pages. Not in the database: it changes every second and only matters while it runs."""

import time

from django.core.cache import cache

TTL = 6 * 3600


def _key(asset_id):
    return f"vitrine:progress:{asset_id}"


class Reporter:
    """Called by the conversion with (fraction, stage); writes at most once a second."""

    def __init__(self, asset_id):
        self.asset_id = asset_id
        self.started = time.time()
        self.last = 0.0
        self.stage = ""
        cache.set(_key(asset_id), {"stage": "starting", "pct": 0, "eta": None}, TTL)

    def __call__(self, fraction, stage="converting"):
        now = time.time()
        if stage == self.stage and now - self.last < 1.0 and fraction < 1.0:
            return
        self.stage, self.last = stage, now
        elapsed = now - self.started
        eta = None
        if stage == "converting" and 0.02 <= fraction < 1.0 and elapsed > 3:
            eta = int(elapsed / fraction * (1 - fraction))
        cache.set(_key(self.asset_id), {"stage": stage, "pct": int(fraction * 100), "eta": eta}, TTL)

    def done(self):
        cache.delete(_key(self.asset_id))


def get_many(asset_ids):
    found = cache.get_many([_key(i) for i in asset_ids])
    return {i: found.get(_key(i)) for i in asset_ids}


def describe(info):
    """Text for a tile: 'Converting · 42% · about 1 min left'."""
    if not info or info.get("stage") == "starting":
        return "Starting…" if info else "Waiting to be converted…"
    if info.get("stage") == "finishing":
        return "Almost ready: making the thumbnail…"
    text = f"Converting · {info.get('pct', 0)}%"
    eta = info.get("eta")
    if eta is not None:
        text += " · " + (f"about {max(1, round(eta / 60))} min left" if eta >= 90 else f"about {max(5, round(eta / 5) * 5)} s left")
    return text
