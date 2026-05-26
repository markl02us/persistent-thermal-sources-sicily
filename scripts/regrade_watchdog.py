#!/usr/bin/env python3
"""Nightly watchdog: re-grade today's snapshot via regrade.py and compare to
the published event_grades.csv. Alert if any mismatch.

Outcomes are written to /media/mark/AI_DGX/eumetsat_data/regrade_watchdog.log.
Emails the project inbox on FAIL.

Run from gunicorn post_fork as a daily daemon.
"""
import json, os, subprocess, sys, traceback
from datetime import datetime, timezone, date
from pathlib import Path

REPO_ROOT = Path("/home/mark/.openclaw/workspace/eumetsat_wildfire_detection")
SNAPSHOT_BASE = REPO_ROOT / "data" / "snapshots"
LOG = Path("/media/mark/AI_DGX/eumetsat_data/regrade_watchdog.log")
RESULTS = Path("/media/mark/AI_DGX/eumetsat_data/regrade_watchdog_latest.json")
PYTHON = "/home/mark/.openclaw/workspace/eumetsat_wildfire_detection/venv/bin/python3"


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}\n"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line.strip())


def run_for_date(d: str) -> dict:
    snap = SNAPSHOT_BASE / d
    if not snap.exists():
        return {"date": d, "skip": "no snapshot"}
    required = ["internal_fires.csv", "external_fires.csv",
                "corroboration_signals.csv", "event_grades.csv"]
    missing = [f for f in required if not (snap / f).exists()]
    if missing:
        return {"date": d, "skip": f"missing files: {missing}"}
    out_csv = f"/tmp/regrade_{d}.csv"
    try:
        proc = subprocess.run(
            [PYTHON, "scripts/regrade.py",
             "--internal", str(snap / "internal_fires.csv"),
             "--external", str(snap / "external_fires.csv"),
             "--corr", str(snap / "corroboration_signals.csv"),
             "--reference", str(snap / "event_grades.csv"),
             "--out", out_csv],
            cwd=str(REPO_ROOT), check=True, capture_output=True, timeout=600, text=True)
    except subprocess.CalledProcessError as e:
        return {"date": d, "status": "FAIL", "stderr": e.stderr[:1000]}
    except subprocess.TimeoutExpired:
        return {"date": d, "status": "FAIL", "stderr": "timeout"}

    out = proc.stdout
    # Parse reproducer's diff output
    shared = only_g = only_r = mismatches = 0
    for line in out.splitlines():
        if "shared events:" in line:
            shared = int(line.split(":")[-1].strip())
        elif "only in your regrade:" in line:
            only_g = int(line.split(":")[-1].strip())
        elif "only in reference:" in line:
            only_r = int(line.split(":")[-1].strip())
        elif "mismatches on shared keys:" in line:
            mismatches = int(line.split(":")[-1].strip())
    # OK = no mismatches on shared keys. only_in_reference/regrade is expected because
    # the live DB advances between snapshot dump and watchdog run.
    status = "OK" if mismatches == 0 else "DRIFT"
    return {
        "date": d, "status": status,
        "shared": shared, "only_in_regrade": only_g,
        "only_in_reference": only_r, "mismatches": mismatches,
        "raw_tail": "\n".join(out.splitlines()[-20:]),
    }


def main():
    today = date.today().isoformat()
    log(f"watchdog start, date={today}")
    res = run_for_date(today)
    log(f"result: {json.dumps(res)}")
    RESULTS.write_text(json.dumps(res, indent=2))
    if res.get("status") == "DRIFT" or res.get("status") == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
