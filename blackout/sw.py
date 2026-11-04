"""Thin Switch agent-protocol client."""
import json, os, pathlib
import httpx

BASE = "http://localhost:8000"
HERE = pathlib.Path(__file__).resolve().parent.parent / "infra"
STORE = HERE / ".agents.json"
ENV = dict(l.split("=", 1) for l in (HERE / ".env").read_text().splitlines() if "=" in l and not l.startswith("#"))


def agents():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


class Agent:
    def __init__(self, name, connection_id=None):
        rec = agents()[name]
        self.name, self.id, self.key, self.conn = name, rec["id"], rec["api_key"], connection_id
        self.h = {"Authorization": f"Bearer {self.key}"}

    def ops(self):
        r = httpx.get(f"{BASE}/agents/{self.id}/ops", headers=self.h)
        r.raise_for_status()
        return r.json()["operations"]

    def op(self, _op, /, **args):
        h = dict(self.h)
        if self.conn:
            h["X-Switch-Connection-Id"] = self.conn
        r = httpx.post(f"{BASE}/agents/{self.id}/ops/{_op}", headers=h, json=args, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"{self.name}.{_op} -> {r.status_code}: {r.text[:400]}")
        return r.json().get("result")


def gateway():
    """Log in as the Gateway admin; the agents' owner sets their addressing."""
    c = httpx.Client(base_url=f"{BASE}/gateway", timeout=30)
    r = c.post("/auth/login", json={"email": ENV["GATEWAY_ADMIN_EMAIL"], "password": ENV["GATEWAY_ADMIN_PASSWORD"]})
    r.raise_for_status()
    return c


def register_owned(gw, reg_key, name, description, display):
    known = agents()
    if name in known:
        return known[name]
    r = httpx.post(f"{BASE}/agents", headers={"Authorization": f"Bearer {reg_key}"}, json={
        "name": name, "description": description, "connector_type": "blackout", "display_name": display,
        "integration_profile": {"connection_model": "always_on", "message_exchange": True,
                                "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": []},
    })
    r.raise_for_status()
    known[name] = r.json()
    STORE.write_text(json.dumps(known, indent=2))
    os.chmod(STORE, 0o600)
    p = gw.put(f"/agents/{known[name]['id']}/addressing-policy", json={"policy": None})
    p.raise_for_status()
    return known[name]
