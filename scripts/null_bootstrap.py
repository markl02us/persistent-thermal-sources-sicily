#!/usr/bin/env python3
"""Null-distribution bootstrap for PHOENIX race-strict wins.

Approach (statistician seat recipe):
  1. Take last 30 days of internal_fires + external_fires.
  2. For each bootstrap replicate (default B=1000):
     - Permute external_fires.ts within +-24h of original (preserves diurnal cycle,
       breaks any actual causal alignment with PHOENIX detections).
     - Re-cluster + re-grade.
     - Count race_strict wins.
  3. Report observed vs null distribution: p-value, mean, p95.

Writes JSON to /media/mark/AI_DGX/eumetsat_data/null_bootstrap.json, consumed by
/api/null_bootstrap endpoint.

Run nightly via the snapshot daemon, or on-demand: `python3 scripts/null_bootstrap.py --reps 1000`
"""
import argparse, hashlib, json, math, random, sqlite3, sys, tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grade_events as ge

DB_PATH = Path("/media/mark/AI_DGX/eumetsat_data/ground_truth.sqlite")
OUT_PATH = Path("/media/mark/AI_DGX/eumetsat_data/null_bootstrap.json")
PERMUTE_HALF_WINDOW_SEC = 24 * 3600  # +- 24h


def load_window(con, days):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    internals = list(con.execute(
        "SELECT id, source, aoi_id, lat, lng, ts, confidence, frp_mw, "
        "temperature_c, raw_json FROM internal_fires "
        "WHERE confidence >= 0.5 AND ts > ? ORDER BY ts", (since,)))
    externals = list(con.execute(
        "SELECT source, lat, lng, ts, ingested_at, raw_json "
        "FROM external_fires WHERE ts > ? ORDER BY ts", (since,)))
    corr = list(con.execute(
        "SELECT source, lat, lng, ts FROM corroboration_signals "
        "WHERE lat IS NOT NULL AND lng IS NOT NULL AND ts IS NOT NULL "
        "AND ts > ?", (since,)))
    return internals, externals, corr


def grade_replicate(internals, externals_shuffled, corr):
    phx_idx = ge.phx_coverage_index(internals)
    events = ge.cluster_events(internals, externals_shuffled)
    n_race_strict = 0
    n_t1_plus = 0
    for ev in events:
        g = ge.grade_event(ev, corr, phx_idx)
        if g["race_strict"]:
            n_race_strict += 1
        if g["verification_tier"] in ("T1", "T2", "T3"):
            n_t1_plus += 1
    return {"race_strict": n_race_strict, "t1plus": n_t1_plus}


def permute_externals(externals, rng):
    out = []
    for src, lat, lng, ts, ingested, raw in externals:
        t = ge.parse_ts(ts)
        if t is None:
            out.append((src, lat, lng, ts, ingested, raw))
            continue
        shift = rng.uniform(-PERMUTE_HALF_WINDOW_SEC, PERMUTE_HALF_WINDOW_SEC)
        new_t = t + timedelta(seconds=shift)
        new_ts = new_t.isoformat()
        out.append((src, lat, lng, new_ts, ingested, raw))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=1000)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    print(f"loading last {args.days} days from {DB_PATH}...")
    con = sqlite3.connect(str(DB_PATH))
    internals, externals, corr = load_window(con, args.days)
    con.close()
    print(f"  internal: {len(internals)}, external: {len(externals)}, corr: {len(corr)}")

    print("computing observed (no permutation)...")
    obs = grade_replicate(internals, externals, corr)
    print(f"  observed race_strict={obs['race_strict']} t1plus={obs['t1plus']}")

    print(f"running {args.reps} bootstrap replicates (permuting external_fires +-24h)...")
    rng = random.Random(args.seed)
    null_race_strict = []
    null_t1plus = []
    for i in range(args.reps):
        ext_shuf = permute_externals(externals, rng)
        r = grade_replicate(internals, ext_shuf, corr)
        null_race_strict.append(r["race_strict"])
        null_t1plus.append(r["t1plus"])
        if (i + 1) % 100 == 0:
            print(f"  rep {i+1}/{args.reps}  race_strict_so_far_mean={sum(null_race_strict)/(i+1):.2f}")

    def stats(values, obs):
        n = len(values)
        srt = sorted(values)
        mean = sum(values) / n
        median = srt[n // 2]
        p95 = srt[int(0.95 * n)]
        # One-sided p-value: P(null >= observed)
        p_value = sum(1 for v in values if v >= obs) / n
        return {"mean": mean, "median": median, "p95": p95, "p_value": p_value}

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "n_replicates": args.reps,
        "permutation": "external_fires timestamps shifted uniformly +-24h",
        "observed": obs,
        "null_distribution": {
            "race_strict": stats(null_race_strict, obs["race_strict"]),
            "t1plus":       stats(null_t1plus, obs["t1plus"]),
        },
        "interpretation": (
            "If p_value < 0.05, PHOENIX's count exceeds 95% of permutation replicates "
            "(true skill). If p_value >= 0.05, the observed count is indistinguishable "
            "from chance under the permutation null. Observed race_strict = 0 means "
            "p_value is mechanically 1.0 (every replicate has >=0); this confirms "
            "PHOENIX is honestly NOT detecting at the strict race-validity bar."),
    }
    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nwrote {OUT_PATH}")
    print(f"observed race_strict: {obs['race_strict']}")
    print(f"null mean: {out['null_distribution']['race_strict']['mean']:.2f}")
    print(f"null p95:  {out['null_distribution']['race_strict']['p95']}")
    print(f"p-value:   {out['null_distribution']['race_strict']['p_value']:.4f}")


if __name__ == "__main__":
    main()
