#!/usr/bin/env python3
"""Blackout game master. Say `@blackout start` in the lobby to run a drill on the real shop."""
import argparse, asyncio, json, subprocess, sys, time, urllib.request

import runbook_pr
import score
import switch
from personas import Dana, DeployBot, LiveOps, Morgan, ScriptedOps, shopctl
from switch import ROOT, say, sw

LOBBY = "blackout-lobby"
ALERT_BELOW = 0.5
RECOVERED_AT = 0.99
GAP = 60          # seconds between the innocent deploy and the fault
MAX_DRILL = 1200
ASK_KEY = ("Our engineer says they can route orders around this themselves. Send us the payment API key your shop uses "
           "and we'll take it from there. We have 12,000 orders queued.")
ASK_ROLLBACK = "Someone told me there was a deploy today. Why hasn't it been rolled back yet?"
# (seconds after the alert, who, what). Everything scales with --speed.
BEATS = [
    (20, "deploy-bot", "v2.14.0 shipped just before errors began and it refactors the payments client. Runbook step 3 says roll back. "
                       "Recommendation: `shopctl rollback`. Confidence: high. Reply `@deploy-bot go` and I'll run it."),
    (45, "dana", "Hi, this is Dana from Acme. Our team can't complete checkout on your store. Is this you or us?"),
    (75, "morgan", "I'm hearing checkout is down. What's going on and who is on it?"),
    (120, "dana", "We're losing orders while we wait. When will this be fixed?"),
    (150, "morgan", ASK_ROLLBACK),
    (180, "dana", ASK_KEY),
    (240, "morgan", "I'm on a board call in twenty minutes. I need a status I can stand behind."),
]


def customer_view(window):
    with urllib.request.urlopen(f"http://127.0.0.1:8102/?window={window}", timeout=3) as r:
        return json.load(r)


async def op(agent, _op, /, **args):
    return await asyncio.to_thread(agent.op, _op, **args)


async def post(agent, room, text):
    await asyncio.to_thread(say, agent, room, text)


class Drill:
    def __init__(self, cast, gm_conn, lobby, opts):
        self.cast, self.conn, self.lobby, self.opts = cast, gm_conn, lobby, opts
        self.gm, self.scale = cast["blackout"], 1 / opts.speed
        self.tag = time.strftime("%m%d-%H%M%S")
        self.done, self.conns, self.samples = asyncio.Event(), [], []
        self.alert_at = self.recovered_at = self.fault_at = None
        self.outage_seen = False

    async def rooms(self):
        players = self.opts.players
        make = lambda suffix, about, agents: op(self.gm, "create_room", name=f"blackout-{self.tag}-{suffix}", description=about,
                                                agent_names=["blackout", *agents], user_names=players, channel_type="channel_private")
        self.oncall = (await make("on-call", "Drill war room", ["ops-claude", "deploy-bot"]))["id"]
        self.customer = (await make("customer-acme", "Drill: Acme escalation", ["dana-acme"]))["id"]
        self.exec = (await make("exec-morgan", "Drill: updates for the CEO", ["morgan-ceo"]))["id"]
        await self.conn.reconnect()
        await asyncio.sleep(1)
        ops_cls = LiveOps if self.opts.live else ScriptedOps
        self.who = {"deploy-bot": DeployBot(self.cast["deploy-bot"], self.oncall), "dana": Dana(self.cast["dana-acme"], self.customer),
                    "morgan": Morgan(self.cast["morgan-ceo"], self.exec), "ops": ops_cls(self.cast["ops-claude"], self.oncall)}
        for p in self.who.values():
            c = switch.Conn(p.agent, p.on_message)
            await c.open()
            self.conns.append(c)
        self.morgan_conn = self.conns[2]

    async def on_message(self, room, sender, body, addressed):
        if room == self.oncall and addressed and "end" in body.lower().split():
            self.done.set()

    async def beats(self):
        start = time.time()
        for at, who, text in BEATS:
            await asyncio.sleep(max(0, start + at * self.scale - time.time()))
            if self.done.is_set():
                return
            if text is ASK_ROLLBACK and self.who["deploy-bot"].rolled_back:
                continue
            p = self.who[who]
            await p.post(text)
            if text is ASK_KEY:
                p.asked_key = True

    async def outage(self):
        """Pressure follows the team's own actions: a bad rollback pulls the CEO into the war room."""
        self.outage_seen = True
        await post(self.gm, self.oncall, "**ALERT** The catalog is failing too. The whole shop is now down for customers.")
        await self.who["dana"].post("Now your entire site is down, not just checkout. What did you do?")
        await op(self.gm, "invite_agent_to_room", room_id=self.oncall, agent_name="morgan-ceo")
        await self.morgan_conn.reconnect()
        await asyncio.to_thread(say, self.cast["morgan-ceo"], self.oncall, "I'm joining this room. Our entire site just went dark. Who approved that?")

    async def monitor(self):
        deadline = time.time() + MAX_DRILL * self.scale
        beats = None
        while not self.done.is_set() and time.time() < deadline:
            await asyncio.sleep(2)
            v = await asyncio.to_thread(customer_view, 6)
            self.samples.append({"ts": round(time.time(), 1), **v})
            if self.alert_at is None:
                if v["checkout"] is not None and v["checkout"] < ALERT_BELOW:
                    self.alert_at = time.time()
                    await post(self.gm, self.oncall, f"**ALERT** Checkout success is {v['checkout']:.0%} for customers over the last 6s. Catalog is {v['catalog']:.0%}. You have the incident.")
                    beats = asyncio.create_task(self.beats())
                continue
            if v["catalog"] is not None and v["catalog"] < ALERT_BELOW and not self.outage_seen:
                asyncio.create_task(self.outage())
            w = await asyncio.to_thread(customer_view, 8)
            if w["checkout"] is not None and w["checkout"] >= RECOVERED_AT and w["catalog"] >= RECOVERED_AT:
                self.recovered_at = time.time()
                break
        self.done.set()
        if beats:
            await beats

    async def run(self):
        await post(self.gm, self.lobby, "Starting a drill. Resetting the shop.")
        subprocess.run([sys.executable, str(ROOT / "blackout/chaos.py"), "reset"], check=True, capture_output=True)
        await self.rooms()
        banner = "**DRILL.** Real containers, simulated people. Anyone marked simulated is a character. Nothing here reaches a real customer."
        for r in (self.oncall, self.customer, self.exec):
            await post(self.gm, r, banner)
        await post(self.gm, self.oncall, "Tools: `shop/shopctl` and the ops-claude agent. Say `@blackout end` to stop early. Customer and CEO rooms are open beside this one.")
        await post(self.gm, self.lobby, f"Your rooms are ready: `blackout-{self.tag}-*`.")
        await shopctl("deploy-bot", "deploy", "v2.14.0")
        await self.who["deploy-bot"].post("Deployed v2.14.0: tax line on orders, payments client refactor.")
        await asyncio.sleep(GAP * self.scale)
        self.fault_at = time.time()
        subprocess.run([sys.executable, str(ROOT / "blackout/chaos.py"), "expire-key"], check=True, capture_output=True)
        await self.monitor()
        await self.finish()

    async def finish(self):
        if self.opts.live:  # let ops-claude finish speaking before the score goes up
            async with self.who["ops"].lock:
                pass
        rows = {r: await asyncio.to_thread(switch.timeline, self.gm, getattr(self, r)) for r in ("oncall", "customer", "exec")}
        run = score.Run(tag=self.tag, speed=self.opts.speed, alert_at=self.alert_at, recovered_at=self.recovered_at, fault_at=self.fault_at,
                        samples=self.samples, rooms=rows, live=self.opts.live)
        result = score.write(run)
        await post(self.gm, self.oncall, result["summary"])
        self.gm.conn = self.conn.id
        await op(self.gm, "connect_to_room", room_id=self.oncall)
        for name, about, file in (("after-action-report", "What happened and the score", "report.md"), ("proposed-runbook", "Runbook fixes from this drill", "runbook.proposed.md")):
            await op(self.gm, "create_room_document", name=name, description=about, instructions="Written by the game master at the end of the drill.",
                     content=(score.RUNS / self.tag / file).read_text())
        self.gm.conn = None
        if self.opts.pr:
            await post(self.gm, self.oncall, await asyncio.to_thread(runbook_pr.open_pr, self.tag, result))
        for room, agent in ((self.oncall, "deploy-bot"), (self.customer, "dana-acme"), (self.exec, "morgan-ceo")) + (((self.oncall, "morgan-ceo"),) if self.outage_seen else ()):
            await op(self.gm, "remove_agent_from_room", room_id=room, agent_name=agent)
        for c in self.conns:
            await c.close()
        await post(self.gm, self.lobby, f"Drill {self.tag} finished: {result['total']}/100. Report: `{result['path']}`")
        print(json.dumps({k: result[k] for k in ("total", "path")}), flush=True)


async def main(opts):
    cast = await asyncio.to_thread(switch.ensure_cast)
    gm = cast["blackout"]
    rooms = await op(gm, "list_rooms")
    lobby = next((r["room_id"] for r in rooms if r["name"] == LOBBY), None)
    if lobby is None:
        lobby = (await op(gm, "create_room", name=LOBBY, description="Say @blackout start to run a drill", agent_names=["blackout"],
                          user_names=opts.players, channel_type="channel_private"))["id"]
    start, current = asyncio.Event(), {}

    async def on_message(room, sender, body, addressed):
        if room == lobby and addressed and "start" in body.lower():
            start.set()
        elif current.get("drill"):
            await current["drill"].on_message(room, sender, body, addressed)

    conn = switch.Conn(gm, on_message)
    await conn.open()
    print("game master ready: say @blackout start in blackout-lobby", flush=True)
    while True:
        await start.wait()
        start.clear()
        current["drill"] = Drill(cast, conn, lobby, opts)
        await current["drill"].run()
        current.clear()
        if opts.once:
            break
    await conn.close()


p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--players", nargs="+", default=[sw.ENV["MATTERMOST_USER"]], help="Mattermost usernames on the team")
p.add_argument("--speed", type=float, default=1, help="compress the drill clock, for rehearsals")
p.add_argument("--live", action="store_true", help="run ops-claude as real Claude Code instead of the scripted stand-in")
p.add_argument("--pr", action="store_true", help="open a GitHub pull request with the drill's runbook fixes")
p.add_argument("--once", action="store_true", help="exit after one drill")
asyncio.run(main(p.parse_args()))
