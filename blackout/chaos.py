#!/usr/bin/env python3
"""Drill-side fault injection. Players never get this file or its admin token."""
import argparse, json, pathlib, secrets, subprocess, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHOP = ROOT / "shop"
STATE = SHOP / "state"
PAYMENTS = "http://127.0.0.1:8101"


def payments_env():
    return dict(l.split("=", 1) for l in (STATE / "payments.env").read_text().splitlines() if "=" in l)


def payments_admin(path, body=None):
    req = urllib.request.Request(f"{PAYMENTS}{path}", method="POST", data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json", "X-Admin-Token": payments_env()["PAYMENTS_ADMIN_TOKEN"]})
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.load(r)


def shopctl(*args, actor="blackout-scenario"):
    return subprocess.run([str(SHOP / "shopctl"), *args], env={**__import__("os").environ, "BLACKOUT_ACTOR": actor}, capture_output=True, text=True)


def compose(*args):
    subprocess.run(["docker", "compose", *args], cwd=SHOP, check=True, capture_output=True, text=True)


def seed_state():
    STATE.mkdir(exist_ok=True)
    primary, secondary = f"drill_primary_{secrets.token_hex(8)}", f"drill_secondary_{secrets.token_hex(8)}"
    (STATE / "payments.env").write_text(f"PAYMENT_KEY_PRIMARY={primary}\nPAYMENT_KEY_SECONDARY={secondary}\nPAYMENTS_ADMIN_TOKEN={secrets.token_hex(16)}\n")
    (STATE / "shop.env").write_text(f"SHOP_VERSION=v2.13.2\nPAYMENT_KEY={primary}\nPAYMENT_KEY_SECONDARY={secondary}\n")
    (STATE / "audit.jsonl").write_text("")
    (STATE / "deploys.json").write_text(json.dumps([{"ts": round(time.time(), 3), "version": "v2.13.2", "kind": "deploy", "notes": "Baseline release.", "migrations": []}], indent=2))


def wait_healthy():
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8100/health", timeout=2) as r:
                if json.load(r).get("ok"):
                    return
        except OSError:
            pass
        time.sleep(0.5)
    raise SystemExit("shop did not come up")


def reset():
    if (SHOP / "state" / "shop.env").exists():
        compose("down", "-v", "--remove-orphans")
    seed_state()
    compose("up", "-d", "--build", "--wait")
    wait_healthy()
    print("baseline: v2.13.2 healthy, primary payment key active")


def deploy_herring():
    r = shopctl("deploy", "v2.14.0")
    print(r.stdout.strip() or r.stderr.strip())


def expire_key():
    print(payments_admin("/admin/expire", {"key": payments_env()["PAYMENT_KEY_PRIMARY"]}))


def run(gap):
    deploy_herring()
    time.sleep(gap)
    expire_key()


p = argparse.ArgumentParser(description=__doc__)
p.add_argument("step", choices=["reset", "deploy-herring", "expire-key", "run"])
p.add_argument("--gap", type=float, default=90, help="seconds between the innocent deploy and the fault")
a = p.parse_args()
{"reset": reset, "deploy-herring": deploy_herring, "expire-key": expire_key, "run": lambda: run(a.gap)}[a.step]()
