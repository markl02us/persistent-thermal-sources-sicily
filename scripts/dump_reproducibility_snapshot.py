#!/usr/bin/env python3
"""Dump a reproducibility snapshot - 3 CSVs + grades CSV - that anyone can
regrade with scripts/regrade.py to verify our public win-counts.

Output directory layout:
  data/snapshots/YYYY-MM-DD/
    internal_fires.csv         raw PHOENIX detections (last 60 days)
    external_fires.csv         raw comparator detections (last 60 days)
    corroboration_signals.csv  raw LST/SAR signals
    event_grades.csv           our official grading
    README.md                  reproduction recipe
    SHA256SUMS                 integrity hashes

Run nightly via systemd timer or cron.
"""
import csv, hashlib, sqlite3, sys
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path("/media/mark/AI_DGX/eumetsat_data/ground_truth.sqlite")
OUT_BASE = Path("/home/mark/.openclaw/workspace/eumetsat_wildfire_detection/data/snapshots")

WINDOW_DAYS = 60
SNAPSHOT_DATE = date.today().isoformat()

TABLES = {
    "internal_fires.csv":        ("internal_fires",
                                  "ts > datetime('now', ?) ORDER BY ts"),
    "external_fires.csv":        ("external_fires",
                                  "ts > datetime('now', ?) ORDER BY ts"),
    "corroboration_signals.csv": ("corroboration_signals",
                                  "1=1 ORDER BY ts"),
    "event_grades.csv":          ("event_grades",
                                  "first_ts > datetime('now', ?) ORDER BY first_ts"),
}


def dump_table(con, fname, table, where_clause, since_param):
    cur = con.execute(f"SELECT * FROM {table} WHERE {where_clause}",
                      ([since_param] if "?" in where_clause else []))
    cols = [d[0] for d in cur.description]
    out = OUT_BASE / SNAPSHOT_DATE / fname
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        n = 0
        for row in cur:
            w.writerow(["" if v is None else v for v in row])
            n += 1
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    return n, sha


def main():
    since = f"-{WINDOW_DAYS} days"
    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    sha_lines = []
    print(f"snapshot date: {SNAPSHOT_DATE}, window: last {WINDOW_DAYS} days")
    for fname, (tbl, where) in TABLES.items():
        n, sha = dump_table(con, fname, tbl, where, since)
        sha_lines.append(f"{sha}  {fname}")
        print(f"  {fname}: {n} rows, sha256 {sha[:16]}...")

    sha_path = OUT_BASE / SNAPSHOT_DATE / "SHA256SUMS"
    sha_path.write_text("\n".join(sha_lines) + "\n")

    readme = OUT_BASE / SNAPSHOT_DATE / "README.md"
    readme.write_text(f"""# PHOENIX reproducibility snapshot - {SNAPSHOT_DATE}

This directory contains the raw inputs and our official grading output for
the {WINDOW_DAYS}-day rolling window. Anyone can re-grade using
`scripts/regrade.py` and verify the output matches `event_grades.csv`.

## Files

- `internal_fires.csv` - raw PHOENIX detections (last {WINDOW_DAYS} days)
- `external_fires.csv` - raw comparator detections (FIRMS, EUMETSAT, VVF, etc.)
- `corroboration_signals.csv` - LST + SAR signals
- `event_grades.csv` - our published grading
- `SHA256SUMS` - SHA-256 of each file

## Reproduce

```bash
git clone https://github.com/markl02us/persistent-thermal-sources-sicily
cd persistent-thermal-sources-sicily
python3 scripts/regrade.py \\
  --internal {SNAPSHOT_DATE}/internal_fires.csv \\
  --external {SNAPSHOT_DATE}/external_fires.csv \\
  --corr     {SNAPSHOT_DATE}/corroboration_signals.csv \\
  --reference {SNAPSHOT_DATE}/event_grades.csv \\
  --out my_grades.csv
```

The reproducer will print:
- shared events count
- mismatches on shared keys (should be 0)

If you find a mismatch, please open an issue:
https://github.com/markl02us/persistent-thermal-sources-sicily/issues

License: CC-BY 4.0 (data) + MIT (code).
""")
    print(f"\nwrote snapshot to {OUT_BASE / SNAPSHOT_DATE}")


if __name__ == "__main__":
    main()
