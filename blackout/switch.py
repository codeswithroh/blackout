"""Switch and Mattermost clients for the game master."""
import asyncio, json, pathlib, uuid
import httpx, websockets

import sw

ROOT = pathlib.Path(__file__).resolve().parent.parent

MM = "http://localhost:8065/api/v4"
CAST = {
    "blackout": ("Runs the drill", "Blackout"),
    "ops-claude": ("The team's Claude Code agent", "ops-claude"),
    "deploy-bot": ("Fictional deploy assistant in a drill", "deploy-bot (simulated)"),
    "dana-acme": ("Fictional customer in a drill", "Dana at Acme (simulated)"),
    "morgan-ceo": ("Fictional CEO in a drill", "Morgan (CEO, simulated)"),
}
AGENT_NAMES = set(CAST) | {display for _, display in CAST.values()}


def is_human(sender):
    return sender not in AGENT_NAMES and not sender.startswith("bridge-")


def ensure_cast():
    missing = [n for n in CAST if n not in sw.agents()]
    gw = sw.gateway()
    for bridge in gw.get("/collaborations").json():  # a CEO who joins with "Tag me anytime!" breaks the fiction
        gw.patch(f"/collaborations/{bridge['bridge_id']}", json={"agent_greetings_enabled": False}).raise_for_status()
    if missing:
        key = gw.post("/api-keys", json={"label": "blackout-cast"}).json()["key"]
        for n in missing:
            sw.register_owned(gw, key, n, *CAST[n])
    return {n: sw.Agent(n) for n in CAST}


def say(agent, room, text):
    r = httpx.post(f"{sw.BASE}/agents/{agent.id}/message", headers=agent.h, json={"room_id": room, "content": text}, timeout=15)
    r.raise_for_status()


def timeline(agent, room, limit=500):
    """A room's messages oldest first, from Switch's own record."""
    threads = agent.op("read_context", room_id=room, limit=limit)["threads"]
    rows = [e for t in threads for e in [t["root"], *t["replies"]] if e["kind"] == "message"]
    return sorted(rows, key=lambda e: e["timestamp"])


class Conn:
    """One agent's event stream. Reconnect after every room change: a live connection misses new rooms."""

    def __init__(self, agent, on_message):
        self.agent, self.on_message, self.id = agent, on_message, str(uuid.uuid4())
        self.cursor, self.gen, self.ws, self.task = 0, None, None, None

    async def open(self):
        url = f"ws://localhost:8000/agents/{self.agent.id}/connection/ws?connection_id={self.id}&scope=all&filter=all&start_from={self.cursor or 'head'}"
        if self.gen is not None:
            url += f"&expected_generation={self.gen}"
        self.ws = await websockets.connect(url, additional_headers=self.agent.h)
        self.task = asyncio.create_task(self.loop())

    async def loop(self):
        try:
            async for raw in self.ws:
                f = json.loads(raw)
                if f.get("event") == "ping" or f.get("type") == "ping":
                    await self.ws.send(json.dumps({"type": "pong", "cursor": self.cursor}))
                    continue
                if "id" in f:
                    self.cursor = f["id"]
                d = f.get("data") or f
                if f.get("event") == "connection_state" or d.get("type") == "connection_state":
                    self.gen = d.get("generation", self.gen)
                if f.get("event") == "message":
                    p = d["payload"]
                    if p.get("sender_name") != self.agent.name:
                        asyncio.create_task(self.on_message(d["room_id"], p.get("sender_name", ""), p.get("body", ""), bool(p.get("addressed"))))
        except websockets.ConnectionClosed:
            pass

    async def close(self):
        await self.ws.close()
        await self.task

    async def reconnect(self):
        await self.close()
        await self.open()


class Mattermost:
    """The rehearsal driver's stand-in for a person at a keyboard."""

    def __init__(self):
        r = httpx.post(f"{MM}/users/login", json={"login_id": sw.ENV["MATTERMOST_USER"], "password": sw.ENV["MATTERMOST_USER_PASSWORD"]})
        r.raise_for_status()
        self.h = {"Authorization": f"Bearer {r.headers['Token']}"}
        self.team = httpx.get(f"{MM}/users/me/teams", headers=self.h).json()[0]["id"]

    def channel(self, fragment):
        hits = [c for c in httpx.get(f"{MM}/users/me/teams/{self.team}/channels", headers=self.h).json() if fragment in c["display_name"]]
        return max(hits, key=lambda c: c["create_at"])["id"] if hits else None

    def post(self, channel_id, text):
        httpx.post(f"{MM}/posts", headers=self.h, json={"channel_id": channel_id, "message": text}).raise_for_status()

    def texts(self, channel_id):
        d = httpx.get(f"{MM}/channels/{channel_id}/posts", headers=self.h).json()
        return [d["posts"][i]["message"] for i in reversed(d["order"])]


if __name__ == "__main__":  # setup.sh polls this until Switch and Mattermost are both ready
    ensure_cast()
    Mattermost()
