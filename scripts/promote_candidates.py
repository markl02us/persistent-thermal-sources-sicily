"""Promote mined candidates through the Stage-2/3/4 pipeline.

Reads candidates.jsonl (output of miner.py), for each candidate:
  - Downloads Esri tile (~2km radius at the cell coord)
  - Runs Claude Sonnet vision classifier
  - If high-conf: auto-promotes to sources.json (new entry)
  - If low-conf: leaves in review queue

Usage:
    python3 promote_candidates.py            # all candidates
    python3 promote_candidates.py --limit 10 # first 10 only
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/mark/phoenix_false_positives/scripts")
from pipeline import (
    SOURCES_JSON, TILES, REVIEW_QUEUE, _load_anthropic_key,
    download_esri_tile, classify_with_claude, annotate_source,
    append_review_queue, VISION_CONFIDENCE_THRESHOLD,
)

CANDIDATES = Path("/home/mark/phoenix_false_positives/candidates.jsonl")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Only process first N candidates")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if not CANDIDATES.exists():
        sys.exit(f"No candidates file: {CANDIDATES}. Run miner.py first.")
    candidates = [json.loads(l) for l in CANDIDATES.read_text().splitlines() if l.strip()]
    if args.limit:
        candidates = candidates[:args.limit]
    print(f"Processing {len(candidates)} candidates")

    data = json.loads(SOURCES_JSON.read_text())
    api_key = _load_anthropic_key()
    n_auto, n_queue = 0, 0
    new_sources = []

    for c in candidates:
        lat, lon = c["lat_cell"], c["lon_cell"]
        cid = f"mined-{lat:.3f}-{lon:.3f}".replace(".", "p").replace("-", "n")
        # Build a stub source record so we can reuse the pipeline
        source = {
            "id": cid,
            "name_it": f"Sito candidato {lat:.3f}, {lon:.3f}",
            "name_en": f"Candidate site at {lat:.3f}, {lon:.3f}",
            "category": "unclassified",
            "subcategory": "candidate_persistent_anomaly",
            "lat": lat, "lon": lon, "radius_km": 2.0,
            "country": "IT", "region": "Sicilia",
            "phoenix_detections_30d": c["n_hits"],
            "viirs_confirmed_30d": 0,  # by definition of being a candidate
            "first_observed": c["first_ts"],
            "last_observed": c["last_ts"],
            "phoenix_sources": c["sources"],
            "google_maps": {
                "satellite": f"https://www.google.com/maps/@{lat},{lon},14z/data=!3m1!1e3",
                "street_view": f"https://www.google.com/maps/@{lat},{lon},3a,75y",
                "earth": f"https://earth.google.com/web/@{lat},{lon},0a,1000d",
            },
            "sources_cited": ["PHOENIX-miner"],
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }
        logging.info("Candidate %s: %d hits across %d days",
                     cid, c["n_hits"], c["n_distinct_days"])

        tile_path = TILES / f"{cid}.jpg"
        if not tile_path.exists():
            try:
                download_esri_tile(lat, lon, 2.0, tile_path)
                logging.info("  -> tile %.0f KB", tile_path.stat().st_size / 1024)
            except Exception as exc:
                logging.error("  !! tile fail: %s", exc)
                continue

        vision = classify_with_claude(tile_path, source, api_key)
        if "error" in vision:
            logging.warning("  !! vision error: %s", vision["error"])
        else:
            logging.info("  vision: %s (conf=%.2f) op=%s",
                         vision.get("primary"), vision.get("confidence", 0),
                         vision.get("operator"))
        source["vision_result"] = vision

        decision, annotation = annotate_source(source, vision)
        source["annotation"] = annotation
        if decision.startswith("auto"):
            source["category"] = vision.get("primary", "unclassified") if decision == "auto-vision" else annotation.get("auto_category")
            new_sources.append(source)
            n_auto += 1
            logging.info("  -> AUTO-PROMOTED: %s", source["category"])
        else:
            append_review_queue(source, annotation, tile_path)
            n_queue += 1
            logging.info("  -> queued for review")

    # Append auto-promoted candidates to sources.json
    if new_sources:
        data["sources"].extend(new_sources)
        data["metadata"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["metadata"]["n_sources"] = len(data["sources"])
        SOURCES_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"\n✓ Auto-promoted {n_auto} new sources to catalog")
    print(f"  Queued for review: {n_queue}")


if __name__ == "__main__":
    main()
