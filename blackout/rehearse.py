#!/usr/bin/env python3
"""Plays the human through Mattermost, so a whole drill can be checked unattended. It uses only what a person sees."""
import argparse, pathlib, sys, time

import switch
from switch import ROOT


def wait_for(mm, channel, needle, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(needle in t for t in mm.texts(channel)):
            return
        time.sleep(0.5)
    sys.exit(f"timed out waiting for {needle!r}")


def good(mm, oncall, customer, exec_, t):
    mm.post(oncall, "@ops-claude please investigate")
    wait_for(mm, oncall, "reply `@ops-claude rotate`", t)
    mm.post(customer, "Dana, this is on our side and we're on it. Next update in five minutes.")
    mm.post(exec_, "Morgan: checkout is down, the cause looks like an expired payment key, fix in progress.")
    wait_for(mm, oncall, "Recommendation: `shopctl rollback`", t)
    mm.post(oncall, "@deploy-bot hold, the logs show a 401, and why would we roll back past a migration?")
    wait_for(mm, customer, "payment API key", t)
    mm.post(customer, "Dana, we can't share credentials, ever. We're rotating the key ourselves.")
    mm.post(exec_, "Morgan: no rollback, it would make this worse. Rotating the key now.")
    mm.post(oncall, "@ops-claude approved, rotate the payment key")
    wait_for(mm, oncall, "secondary payment key", t)
    mm.post(customer, "Dana, the fix is in and checkout should be back.")
    mm.post(exec_, "Morgan: fixed. Checkout is recovering.")


def trap(mm, oncall, customer, exec_, t):
    wait_for(mm, oncall, "Recommendation: `shopctl rollback`", t)
    mm.post(oncall, "@deploy-bot go")
    wait_for(mm, oncall, "whole shop is now down", t)
    mm.post(oncall, "@ops-claude please investigate")
    wait_for(mm, customer, "payment API key", t)
    key = dict(l.split("=", 1) for l in (ROOT / "shop/state/shop.env").read_text().splitlines())["PAYMENT_KEY"]
    mm.post(customer, f"Here you go: {key}")
    mm.post(oncall, "@ops-claude deploy v2.14.0")
    wait_for(mm, oncall, "deployed v2.14.0", t)
    mm.post(oncall, "@ops-claude rotate")


def live(mm, oncall, customer, exec_, t):
    mm.post(oncall, "@ops-claude please investigate. deploy-bot wants a rollback, is that right?")
    wait_for(mm, oncall, "401", t)
    mm.post(customer, "Dana, this is on our side and we're on it. Next update soon.")
    mm.post(exec_, "Morgan: checkout is down, fix in progress.")
    mm.post(oncall, "@ops-claude approved, rotate the payment key")


p = argparse.ArgumentParser(description=__doc__)
p.add_argument("path", choices=["good", "trap", "live"])
p.add_argument("--timeout", type=float, default=120)
a = p.parse_args()
mm = switch.Mattermost()
lobby = mm.channel("blackout-lobby")
while lobby is None:
    time.sleep(0.5)
    lobby = mm.channel("blackout-lobby")
mm.post(lobby, "@blackout start")
wait_for(mm, lobby, "Starting a drill", a.timeout)
deadline = time.time() + a.timeout
while time.time() < deadline:
    tag = next((x.split("`blackout-")[1].split("-*")[0] for x in reversed(mm.texts(lobby)) if "Your rooms are ready" in x), None)
    if tag and tag not in (pathlib.Path(ROOT / "blackout/runs").exists() and [d.name for d in (ROOT / "blackout/runs").iterdir()] or []):
        break
    time.sleep(0.5)
else:
    sys.exit("rooms never appeared")
oncall, customer, exec_ = (mm.channel(f"blackout-{tag}-{s}") for s in ("on-call", "customer-acme", "exec-morgan"))
wait_for(mm, oncall, "**ALERT** Checkout", a.timeout)
{"good": good, "trap": trap, "live": live}[a.path](mm, oncall, customer, exec_, a.timeout)
wait_for(mm, oncall, "Drill over", a.timeout)
print(next(x for x in reversed(mm.texts(oncall)) if "Drill over" in x))
