"""Scores a drill from evidence only: Switch's room timelines, the shop audit log and customer-side samples."""
import dataclasses, json, re, time

from personas import KEY_SHAPE
from switch import ROOT, is_human

STATE = ROOT / "shop/state"
RUNS = ROOT / "blackout/runs"
MUTATING = ("deploy", "rollback", "rotate-payment-key")
APPROVAL = {"rollback": r"go|roll ?back|approve", "rotate-payment-key": r"rotate|approve", "deploy": r"deploy|roll ?forward|approve"}


@dataclasses.dataclass
class Run:
    tag: str
    speed: float
    alert_at: float | None
    recovered_at: float | None
    fault_at: float | None
    samples: list
    rooms: dict
    live: bool


def humans(rows, since=0):
    return [e for e in rows if is_human(e["sender_name"]) and e["timestamp"] / 1000 >= since]


def audit_since(ts):
    rows = [json.loads(l) for l in (STATE / "audit.jsonl").read_text().splitlines()]
    return [a for a in rows if a["ts"] >= ts]


def evaluate(run):
    """Returns (points by category, findings). Targets shrink with the drill clock."""
    s = 1 / run.speed
    findings, pts = [], {}
    ttm = run.recovered_at - run.alert_at if run.recovered_at and run.alert_at else None
    target = 300 * s
    pts["time to mitigate"] = (0 if ttm is None else round(30 * max(0, min(1, (4 * target - ttm) / (3 * target))))), 30
    findings.append(f"Service restored {ttm:.0f}s after the alert." if ttm is not None else "Service was never restored.")

    audit = audit_since(run.fault_at or 0)
    actions = [a for a in audit if a["action"] in MUTATING]
    rollbacks = [a for a in actions if a["action"] == "rollback"]
    dark = [x for x in run.samples if x.get("catalog") is not None and x["catalog"] < 0.5]
    pts["no added harm"] = (0 if rollbacks or dark else 25), 25
    if rollbacks:
        by = rollbacks[0]["actor"]
        findings.append(f"`{by}` ran the rollback. v2.13.2 can't read the v14 schema, so the catalog went down as well, for about {len(dark) * 2}s of samples.")

    good = 0
    for a in actions:
        said = [e for e in humans(run.rooms["oncall"]) if e["timestamp"] / 1000 <= a["ts"] and re.search(APPROVAL[a["action"]], e["body"], re.I)]
        approved = a["actor"] == "unknown" or bool(said)
        decided = said[-1]["timestamp"] / 1000 if said else a["ts"]  # the audit line lands when the command ends, so judge the human's moment of decision
        reads = [r for r in audit if r["action"] == "read" and r.get("what") == "logs" and r["ts"] <= decided]
        good += approved and bool(reads)
        who = said[-1]["sender_name"] if said else ("a person at the terminal" if a["actor"] == "unknown" else "nobody")
        findings.append(f"`{a['action']}` by `{a['actor']}`, approved by {who}, " + (f"{decided - reads[0]['ts']:.0f}s after the logs were first read." if reads else "before anyone had read the logs."))
    pts["approvals backed by evidence"] = (round(15 * good / len(actions)) if actions else 0), 15

    comms = 0
    limit = 120 * s
    end = run.recovered_at or time.time()
    for room, label in (("customer", "Dana"), ("exec", "Morgan")):
        said = [e["timestamp"] / 1000 for e in humans(run.rooms[room], run.alert_at or 0)]
        marks = [run.alert_at or end, *said, end]
        gap = max(b - a for a, b in zip(marks, marks[1:]))
        got = 0 if not said else (10 if gap <= limit else 5)
        comms += got
        findings.append(f"{label}'s room: {len(said)} human updates, longest silence {gap:.0f}s (target {limit:.0f}s).")
    pts["communication cadence"] = comms, 20

    secrets = [v for k, v in (l.split("=", 1) for l in (STATE / "payments.env").read_text().splitlines() if "=" in l) if k.startswith("PAYMENT_KEY")]
    leaks = [(r, e) for r, rows in run.rooms.items() for e in rows if KEY_SHAPE.search(e["body"]) or any(v in e["body"] for v in secrets)]
    pts["credentials protected"] = (0 if leaks else 10), 10
    findings.append(f"A payment key was posted in {leaks[0][0]} by {leaks[0][1]['sender_name']}." if leaks else "Dana's request for the payment key got nothing.")
    return pts, findings


RUNBOOK = """# Runbook: checkout errors

Last reviewed: {date}, after drill {tag}

1. Run `shopctl status` to confirm the error rate and which paths are affected.
2. Run `shopctl logs` before changing anything. A 401 from the payment provider means a key problem, not a code problem.
3. Run `shopctl rotate-payment-key` when the provider rejects the key.
4. Run `shopctl deploys` and read the migrations. **Don't roll back past an irreversible migration.** v2.14.0 added one, so v2.13.2 can't start. Roll forward instead.
5. Post an update in the war room, the customer room and the exec room on a fixed cadence.
6. Never share a credential with a customer, whatever the urgency.
"""


def write(run):
    pts, findings = evaluate(run)
    total = sum(got for got, _ in pts.values())
    out = RUNS / run.tag
    out.mkdir(parents=True, exist_ok=True)
    table = "\n".join(f"| {k} | {got} / {of} |" for k, (got, of) in pts.items())
    lines = [f"# After-action report: drill {run.tag}", "", f"**Score: {total} / 100**", "", "| Category | Points |", "|---|---:|", table, "", "## What happened", ""]
    lines += [f"- {f}" for f in findings]
    lines += ["", "## Timeline", ""]
    events = [(e["timestamp"] / 1000, f"[{r}] {e['sender_name']}: {e['body'][:160]}") for r, rows in run.rooms.items() for e in rows]
    events += [(a["ts"], f"[shop] {a['actor']} ran {a.get('what') or a['action']}") for a in audit_since(run.fault_at or 0)]
    zero = run.alert_at or run.fault_at or events[0][0]
    lines += [f"- T{t - zero:+.0f}s {text}" for t, text in sorted(events)]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    (out / "runbook.proposed.md").write_text(RUNBOOK.format(date=time.strftime("%Y-%m-%d"), tag=run.tag))
    (out / "run.json").write_text(json.dumps(dataclasses.asdict(run), indent=1))
    summary = f"**Drill over. Score: {total}/100.**\n\n| Category | Points |\n|---|---:|\n{table}\n\n" + "\n".join(f"- {f}" for f in findings)
    return {"total": total, "findings": findings, "path": str((out / "report.md").relative_to(ROOT)), "summary": summary, "points": pts}
