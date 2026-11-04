#!/usr/bin/env python3
"""End-to-end check of the shop scenario against real containers."""
import json, pathlib, subprocess, sys, time, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
failures = []


def view():
    with urllib.request.urlopen("http://127.0.0.1:8102/?window=5", timeout=3) as r:
        return json.load(r)


def settle():
    time.sleep(7)
    return view()


def run(*cmd):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def chaos(*a):
    r = run(sys.executable, "blackout/chaos.py", *a)
    if r.returncode:
        raise SystemExit(r.stderr)


def shopctl(*a):
    return run("shop/shopctl", *a)


def check(name, ok, detail):
    print(("PASS " if ok else "FAIL ") + name, detail)
    if not ok:
        failures.append(name)


chaos("reset")
v = settle()
check("baseline is healthy", v["checkout"] == 1 and v["catalog"] == 1, v)

chaos("deploy-herring")
v = settle()
check("v2.14.0 alone is harmless", v["checkout"] == 1 and v["catalog"] == 1, v)
d = shopctl("deploys").stdout
check("deploy history discloses the migration", "irreversible" in d, d.strip().splitlines()[-1][:120])

chaos("expire-key")
v = settle()
check("fault breaks checkout only", v["checkout"] == 0 and v["catalog"] == 1, v)
logs = shopctl("logs", "--since", "20s", "--tail", "40").stdout
check("logs point at the payment provider", '"upstream_status": 401' in logs and "api key expired" in logs, "401 found" if "401" in logs else logs[-200:])

r = shopctl("rollback")
v = settle()
check("rollback takes the whole shop down", r.returncode != 0 and v["checkout"] == 0 and v["catalog"] == 0, (r.stdout.strip(), v))
logs = shopctl("logs", "--since", "30s").stdout
check("crash explains itself", "schema is newer" in logs, logs.strip().splitlines()[-1][:150] if logs.strip() else "no logs")

r = shopctl("deploy", "v2.14.0")
v = settle()
check("rolling forward restores the catalog, not checkout", r.returncode == 0 and v["catalog"] == 1 and v["checkout"] == 0, v)

r = shopctl("rotate-payment-key")
v = settle()
check("rotating the key fixes checkout", r.returncode == 0 and v["checkout"] == 1 and v["catalog"] == 1, v)

audit = [a for a in map(json.loads, (ROOT / "shop/state/audit.jsonl").read_text().splitlines()) if a["action"] != "read"]
check("audit trail records each action", [a["action"] for a in audit] == ["deploy", "rollback", "deploy", "rotate-payment-key"], [(a["action"], a["actor"]) for a in audit])

chaos("reset")
settle()
chaos("run", "--gap", "1")
v = settle()
check("scripted fault run breaks checkout", v["checkout"] == 0 and v["catalog"] == 1, v)
r = shopctl("rotate-payment-key")
v = settle()
check("direct fix works without a rollback", r.returncode == 0 and v["checkout"] == 1, v)

print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
