"""Turns a drill's runbook fixes into a pull request. A person decides whether to merge it."""
import shutil, subprocess, tempfile

from score import RUNS
from switch import ROOT


def run(*cmd, cwd=ROOT):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def open_pr(tag, result):
    branch = f"drill/{tag}-runbook"
    body = f"Drill {tag} scored {result['total']}/100.\n\n" + "\n".join(f"- {f}" for f in result["findings"]) + \
           "\n\nThe old runbook says to roll back after a bad deploy. This drill shows when that makes things worse. Read the diff and merge it if you agree."
    try:
        with tempfile.TemporaryDirectory() as d:
            run("git", "worktree", "add", "-q", "-b", branch, f"{d}/wt", "HEAD")
            shutil.copy(RUNS / tag / "runbook.proposed.md", f"{d}/wt/shop/runbooks/checkout.md")
            run("git", "add", "shop/runbooks/checkout.md", cwd=f"{d}/wt")
            run("git", "commit", "-qm", f"Update checkout runbook after drill {tag}", cwd=f"{d}/wt")
            run("git", "push", "-q", "-u", "origin", branch, cwd=f"{d}/wt")
            url = run("gh", "pr", "create", "--head", branch, "--title", f"Fix the checkout runbook (drill {tag})", "--body", body, cwd=f"{d}/wt")
            run("git", "worktree", "remove", "--force", f"{d}/wt")
        return f"Runbook fixes are up for review: {url}\nNothing changes until a person merges it."
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"runbook PR failed: {getattr(e, 'stderr', e)}", flush=True)
        return f"I couldn't open the runbook PR. The proposed runbook is in `blackout/runs/{tag}/runbook.proposed.md`."
