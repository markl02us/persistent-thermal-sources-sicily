#!/usr/bin/env python3
"""PHOENIX event grading reproducer.

Reads raw internal_fires.csv + external_fires.csv (publicly downloadable from
adr-wildfire.com archives or this repo's `data/snapshots/`), loads them into a
temp SQLite, runs the EXACT same grading logic as scripts/grade_events.py,
and writes the resulting event_grades.csv.

If you also have a reference event_grades.csv to compare against (e.g. one
downloaded from adr-wildfire.com on the same day), the script will diff your
re-graded output against it and report any mismatches.

Usage:
  python3 regrade.py --internal internal_fires.csv --external external_fires.csv --out my_grades.csv
  python3 regrade.py --internal ... --external ... --reference grades_from_site.csv

How to get the inputs:
  curl -sS https://adr-wildfire.com/api/event_grades.csv > grades_from_site.csv
  # raw internal/external dumps: open issue at github.com/markl02us/persistent-thermal-sources-sicily

This script + grade_events.py are MIT-licensed. The data is CC-BY 4.0.
"""
import argparse, csv, io, json, os, sqlite3, subprocess, sys, tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# We re-use the grader by importing it. This file must live next to grade_events.py.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade_events  # uses scripts/grade_events.py from the same directory

CSV_INTERNAL_COLS = ["id","aoi_id","lat","lng","ts","source","confidence","frp_mw",
                     "temperature_c","raw_json","ingested_at","narration_json"]
CSV_EXTERNAL_COLS = ["id","source","aoi_id","lat","lng","ts","frp_w","raw_json","ingested_at"]
CSV_CORR_COLS     = ["id","source","ts","product_id","lat","lng","raw_json"]


NUMERIC_COLS = {"lat", "lng", "confidence", "frp_mw", "frp_w", "temperature_c"}

def _cast(col, val):
    if val is None or val == "":
        return None
    if col in NUMERIC_COLS:
        try: return float(val)
        except (TypeError, ValueError): return None
    if col == "id":
        try: return int(val)
        except (TypeError, ValueError): return None
    return val

def load_csv_to_db(con, csv_path, table, cols):
    if not csv_path or not Path(csv_path).exists():
        print(f"warning: {csv_path} not found, skipping {table}")
        return 0
    with open(csv_path, encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows = list(r)
    n = 0
    placeholders = ",".join(["?"] * len(cols))
    for row in rows:
        vals = tuple(_cast(c, row.get(c)) for c in cols)
        try:
            con.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})", vals)
            n += 1
        except sqlite3.IntegrityError:
            continue
    con.commit()
    return n


SCHEMA_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS internal_fires (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    aoi_id TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
    ts TEXT NOT NULL, source TEXT NOT NULL,
    confidence REAL, frp_mw REAL, temperature_c REAL,
    raw_json TEXT, ingested_at TEXT NOT NULL, narration_json TEXT
);
CREATE TABLE IF NOT EXISTS external_fires (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, aoi_id TEXT NOT NULL,
    lat REAL NOT NULL, lng REAL NOT NULL, ts TEXT NOT NULL,
    frp_w REAL, raw_json TEXT, ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corroboration_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, ts TEXT, product_id TEXT,
    lat REAL, lng REAL, raw_json TEXT
);
"""


def dump_grades_csv(con, out_path):
    rows = list(con.execute("SELECT * FROM event_grades ORDER BY first_ts"))
    if not rows:
        Path(out_path).write_text("(no events)\n")
        return 0
    cols = [d[0] for d in con.execute("SELECT * FROM event_grades LIMIT 1").description]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
    return len(rows)


def diff_grades(generated_csv, reference_csv):
    def load(p):
        with open(p, encoding="utf-8") as f:
            return {r["event_key"]: r for r in csv.DictReader(f) if r.get("event_key")}
    g = load(generated_csv); r = load(reference_csv)
    only_in_g = set(g) - set(r)
    only_in_r = set(r) - set(g)
    in_both = set(g) & set(r)
    mismatches = []
    compare_cols = ["verification_tier", "is_phoenix_led", "race_strict",
                    "lead_min_vs_sensed", "comparator_source", "t72h_outcome"]
    for k in in_both:
        for c in compare_cols:
            gv = g[k].get(c, ""); rv = r[k].get(c, "")
            if gv != rv:
                mismatches.append((k, c, gv, rv))
                break
    print(f"shared events: {len(in_both)}")
    print(f"only in your regrade: {len(only_in_g)}")
    print(f"only in reference:    {len(only_in_r)}")
    print(f"mismatches on shared keys: {len(mismatches)}")
    for k, c, gv, rv in mismatches[:20]:
        print(f"  {k} : {c} : your={gv!r} ref={rv!r}")
    if mismatches:
        print("If you see mismatches, the grading logic in grade_events.py may have")
        print("changed since the reference was generated. Try the matching commit.")
    return len(mismatches)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--internal", required=True, help="internal_fires.csv path")
    ap.add_argument("--external", required=True, help="external_fires.csv path")
    ap.add_argument("--corr", default=None, help="corroboration_signals.csv path (optional)")
    ap.add_argument("--out", default="event_grades_reproduced.csv",
                    help="output CSV path")
    ap.add_argument("--reference", default=None,
                    help="reference event_grades.csv to diff against")
    args = ap.parse_args()

    print(f"= PHOENIX regrade reproducer = grader version {grade_events.GRADER_VERSION}")
    tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name
    con = sqlite3.connect(tmp_db)
    con.executescript(SCHEMA_BOOTSTRAP)
    con.executescript(grade_events.SCHEMA)
    for m in grade_events.MIGRATIONS:
        try: con.execute(m)
        except sqlite3.OperationalError: pass

    n_int = load_csv_to_db(con, args.internal, "internal_fires", CSV_INTERNAL_COLS)
    n_ext = load_csv_to_db(con, args.external, "external_fires", CSV_EXTERNAL_COLS)
    n_cor = load_csv_to_db(con, args.corr, "corroboration_signals", CSV_CORR_COLS) if args.corr else 0
    print(f"loaded: internal={n_int} external={n_ext} corr={n_cor}")

    # Monkeypatch grade_events.DB_PATH so its functions use our temp DB
    grade_events.DB_PATH = Path(tmp_db)

    print("loading detections + clustering + grading...")
    internals, externals, corr = grade_events.load_all(con, since=None)
    phx_idx = grade_events.phx_coverage_index(internals)
    events = grade_events.cluster_events(internals, externals)
    con.execute("BEGIN")
    for ev in events:
        g = grade_events.grade_event(ev, corr, phx_idx)
        grade_events.upsert(con, g)
    con.execute("COMMIT")

    print("running multi-stage reconcile...")
    grade_events.reconcile_all_stages(con)

    n_rows = dump_grades_csv(con, args.out)
    print(f"wrote {n_rows} grades -> {args.out}")

    if args.reference:
        print(f"\n= diff against {args.reference} =")
        diff_grades(args.out, args.reference)

    os.unlink(tmp_db)


if __name__ == "__main__":
    main()
