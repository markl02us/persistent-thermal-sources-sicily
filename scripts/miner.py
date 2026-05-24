"""PHOENIX persistent-thermal-source candidate miner.

Mines internal_fires history to find pixels that fire repeatedly but are
NEVER confirmed by FIRMS/VIIRS — strong signal they are persistent false-
positives (industrial, solar, agricultural, volcanic).

Output: a list of candidate cells written to candidates.jsonl. Each is then
fed through the Stage-2/3/4 pipeline (Esri tile → Claude vision → annotate).

Criteria for a candidate cell:
  - Same 0.05° cell (~5km) fired ≥ MIN_HITS times in the last LOOKBACK_DAYS
  - Across ≥ MIN_DISTINCT_DAYS distinct days
  - NEVER spatially-temporally matched a FIRMS-VIIRS confirmation within
    ±MATCH_KM and ±MATCH_HOURS
  - NOT inside an existing source mask in sources.json

This is a strict-enough filter that real wildfires don't qualify (they
either burn out or get FIRMS-confirmed within 24h). Persistent unconfirmed
hot pixels = industrial / volcanic / solar / agricultural.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/mark/phoenix_false_positives")
DB = "/media/mark/AI_DGX/eumetsat_data/ground_truth.sqlite"
SOURCES = ROOT / "sources.json"
CANDIDATES = ROOT / "candidates.jsonl"

LOOKBACK_DAYS = 30
MIN_HITS = 6                # cell must fire ≥6 times in 30 days
MIN_DISTINCT_DAYS = 3       # across ≥3 distinct days (not all in one cluster)
MATCH_KM = 5.0              # FIRMS within 5km counts as confirmation
MATCH_HOURS = 24            # within 24h either side


def hav(a1, b1, a2, b2):
    R = 6371.0
    ra, rb = math.radians(a1), math.radians(a2)
    da, dl = math.radians(a2 - a1), math.radians(b2 - b1)
    h = math.sin(da/2)**2 + math.cos(ra)*math.cos(rb)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(h))


def parse_ts(s: str) -> datetime:
    s = s.replace("+00:00", "")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def load_existing_source_masks() -> list:
    """Return [(lat, lon, radius_km, id)] for known sources to skip."""
    if not SOURCES.exists():
        return []
    data = json.loads(SOURCES.read_text())
    return [(s["lat"], s["lon"], s["radius_km"], s["id"]) for s in data.get("sources", [])]


def inside_existing_mask(lat: float, lon: float, masks: list) -> str:
    """Return source ID if (lat,lon) is inside an existing mask, else empty."""
    for slat, slon, r_km, sid in masks:
        if hav(slat, slon, lat, lon) <= r_km:
            return sid
    return ""


def mine_candidates(lookback_days: int = LOOKBACK_DAYS) -> list:
    """Return list of candidate-cell dicts."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%S")

    # Pull PHOENIX detections in the window
    phx = list(con.execute(
        "SELECT source, lat, lng, ts FROM internal_fires "
        "WHERE ts > ? "
        "AND source IN ('subpixel_v1_alpha','wind_diff','fci_l1c') "
        "-- (expired-rows INCLUDED for FP mining) ",
        (cutoff,)
    ))
    print(f"PHOENIX detections in {lookback_days}-day window: {len(phx)}")

    # Pull FIRMS confirmations
    firms = list(con.execute(
        "SELECT lat, lng, ts FROM external_fires "
        "WHERE ts > ? AND source LIKE 'firms_%' ",
        (cutoff,)
    ))
    firms_pts = [(r["lat"], r["lng"], parse_ts(r["ts"])) for r in firms]
    print(f"FIRMS hits in {lookback_days}-day window: {len(firms_pts)}")

    # Cluster PHOENIX hits into 0.05° cells (~5km)
    cells = defaultdict(list)
    for r in phx:
        lat_cell = round(round(r["lat"] / 0.05) * 0.05, 3)
        lon_cell = round(round(r["lng"] / 0.05) * 0.05, 3)
        cells[(lat_cell, lon_cell)].append({
            "lat": r["lat"], "lng": r["lng"], "ts": r["ts"], "source": r["source"],
        })

    # Filter to persistent cells (≥MIN_HITS, ≥MIN_DISTINCT_DAYS)
    persistent = []
    for (lat, lon), hits in cells.items():
        if len(hits) < MIN_HITS:
            continue
        distinct_days = len({h["ts"][:10] for h in hits})
        if distinct_days < MIN_DISTINCT_DAYS:
            continue
        persistent.append({
            "lat_cell": lat, "lon_cell": lon,
            "n_hits": len(hits), "n_distinct_days": distinct_days,
            "first_ts": min(h["ts"] for h in hits),
            "last_ts": max(h["ts"] for h in hits),
            "sources": sorted({h["source"] for h in hits}),
        })
    persistent.sort(key=lambda c: c["n_hits"], reverse=True)
    print(f"Persistent cells (≥{MIN_HITS} hits, ≥{MIN_DISTINCT_DAYS} days): {len(persistent)}")

    # Check FIRMS confirmation status for each persistent cell
    masks = load_existing_source_masks()
    candidates = []
    for p in persistent:
        # Inside existing mask? skip
        existing = inside_existing_mask(p["lat_cell"], p["lon_cell"], masks)
        if existing:
            p["status"] = "already_in_catalog"
            p["matches_existing_source"] = existing
            continue

        # Was it ever FIRMS-confirmed?
        first_dt = parse_ts(p["first_ts"])
        last_dt = parse_ts(p["last_ts"])
        confirmed = False
        for flat, flng, ft in firms_pts:
            if not (first_dt - timedelta(hours=MATCH_HOURS) <= ft <=
                    last_dt + timedelta(hours=MATCH_HOURS)):
                continue
            if hav(p["lat_cell"], p["lon_cell"], flat, flng) <= MATCH_KM:
                confirmed = True
                break

        if confirmed:
            p["status"] = "firms_confirmed_real_fire"
        else:
            p["status"] = "candidate_false_positive"
            candidates.append(p)

    print(f"NEW candidate false-positive cells: {len(candidates)}")
    con.close()
    return candidates


def write_candidates(candidates: list) -> None:
    with open(CANDIDATES, "w") as f:
        for c in candidates:
            f.write(json.dumps(c) + "\n")
    print(f"Wrote {len(candidates)} candidates → {CANDIDATES}")


def main():
    candidates = mine_candidates()
    print("\nTop 20 candidates by hit count:")
    for c in candidates[:20]:
        print(f"  ({c['lat_cell']:.3f}, {c['lon_cell']:.3f})  "
              f"hits={c['n_hits']:3d}  days={c['n_distinct_days']:2d}  "
              f"sources={c['sources']}")
    write_candidates(candidates)


if __name__ == "__main__":
    main()
