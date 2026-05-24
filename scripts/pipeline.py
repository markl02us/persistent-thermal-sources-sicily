"""PHOENIX persistent-thermal-source pipeline — Stages 2 + 3 + 4.

Stage 2: Download a sub-meter Esri World Imagery tile for each source.
Stage 3: Send the tile to Claude Sonnet vision; ask "what is this?"
Stage 4: If vision confidence >= 0.85, auto-annotate the source JSON.
         If lower AND OSM has no unambiguous tag, queue for batched review.

Usage:
    python3 phoenix_fp_pipeline.py            # full run on all sources
    python3 phoenix_fp_pipeline.py --source augusta-priolo-melilli
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path("/home/mark/phoenix_false_positives")
TILES = ROOT / "tiles"
REVIEW_QUEUE = ROOT / "review_queue.jsonl"
SOURCES_JSON = ROOT / "sources.json"
SECRETS = ROOT / "secrets.json"
TILES.mkdir(parents=True, exist_ok=True)

# Esri World Imagery export endpoint (free, no API key, attribution required)
ESRI_EXPORT = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/export")
USER_AGENT = "PHOENIX-FP-catalog/1.0 (https://adr-wildfire.com/)"

# Vision classifier
ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"  # Sonnet 4.5 — vision-capable
VISION_CONFIDENCE_THRESHOLD = 0.85

# OSM tag categories that are unambiguous enough to skip vision entirely
OSM_UNAMBIGUOUS_TAGS = {
    "volcano", "volcanic_caldera",
    "solar_farm", "wind_farm", "geothermal_plant",
    "gas_flare", "petroleum_well",
}

logger = logging.getLogger("phoenix-fp")


# ── Stage 2 ──────────────────────────────────────────────────────────────────

def download_esri_tile(lat: float, lon: float, radius_km: float,
                       out_path: Path, size_px: int = 1024,
                       timeout_s: int = 30) -> Path:
    """Download a satellite tile centered on (lat,lon) covering ±radius_km.

    Uses the Esri World Imagery export endpoint — free for non-commercial /
    academic use with attribution. Returns the path to a JPEG.
    """
    # bbox in degrees — convert radius from km
    half_lat = radius_km / 111.0
    half_lon = radius_km / (111.0 * max(0.01, abs(__import__("math").cos(__import__("math").radians(lat)))))
    bbox = f"{lon - half_lon},{lat - half_lat},{lon + half_lon},{lat + half_lat}"
    params = {
        "bbox": bbox,
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{size_px},{size_px}",
        "format": "jpg",
        "f": "image",
    }
    url = ESRI_EXPORT + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = resp.read()
    out_path.write_bytes(data)
    return out_path


# ── Stage 3 ──────────────────────────────────────────────────────────────────

def _load_anthropic_key() -> str:
    with open(SECRETS) as f:
        return json.load(f)["ANTHROPIC_API_KEY"]


CLASSIFIER_PROMPT = """You are an expert satellite-imagery analyst. The image below is a sub-meter resolution overhead view of a location in Sicily, Italy, that consistently appears as a heat anomaly to overhead infrared sensors (SEVIRI, FCI, MODIS, VIIRS). Your task is to identify what is at this location so we can flag it as a known false-positive for wildfire detection.

Identify the PRIMARY man-made or natural feature visible. Choose from:

- solar_farm: photovoltaic panels in regular rectangular arrays
- wind_farm: wind turbines (white pylons with rotors)
- refinery: oil refinery (distillation columns, storage tanks, cooling towers)
- petrochemical_complex: integrated petrochemical site (multiple plants + flares)
- gas_flare_stack: visible flare stack burning (vertical chimney with flame)
- power_plant_gas: combined-cycle natural gas plant
- power_plant_coal: coal-fired plant (coal piles + tall chimneys)
- power_plant_geothermal: geothermal plant (pipes + cooling towers in rural area)
- quarry: open-pit mineral extraction
- landfill: waste landfill (clearly bare/disturbed ground with vehicles)
- cement_kiln: cement factory (long rotary kilns + storage)
- glasshouse_complex: large area of greenhouses (reflective rectangles)
- wastewater_treatment: circular settling tanks + rectangular pools
- port_industrial: industrial port (cranes, container yards, ships)
- volcano: volcanic crater or caldera
- lava_flow: recent or active lava
- agricultural_field: cropland, no industrial features
- urban: town or city
- water: sea, lake, river
- forest: tree cover
- other: visible feature not in this list — describe in notes

Also note any visible operator/company names or signage.

Respond ONLY with a single valid JSON object, no other text:

{"primary": "<one of the labels above>", "confidence": 0.0-1.0, "operator": "<name or empty>", "secondary_features": ["<other features visible>"], "notes": "<short description>"}"""


def classify_with_claude(image_path: Path, source: dict, api_key: str,
                         max_retries: int = 2) -> dict:
    """Send a satellite tile to Claude Sonnet and ask what's there."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    img_data = image_path.read_bytes()
    img_b64 = base64.standard_b64encode(img_data).decode()

    context_line = (f"Coordinates: {source['lat']:.4f}°N, {source['lon']:.4f}°E. "
                    f"Search radius: {source['radius_km']:.1f} km. "
                    f"Region: {source.get('region','?')}, {source.get('country','?')}.")

    for attempt in range(max_retries + 1):
        try:
            msg = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=512,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/jpeg", "data": img_b64,
                        }},
                        {"type": "text", "text": context_line + "\n\n" + CLASSIFIER_PROMPT},
                    ],
                }],
            )
            raw = "".join(b.text for b in msg.content if hasattr(b, "text")).strip()
            # Strip code-fence wrappers if present
            if raw.startswith("```"):
                raw = raw.strip("`").lstrip("json").strip()
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                # Try to extract the JSON object from anywhere in the response
                import re
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    result = json.loads(m.group(0))
                else:
                    raise
            result["model"] = ANTHROPIC_MODEL
            result["queried_at"] = datetime.now(timezone.utc).isoformat()
            result["raw_response_chars"] = len(raw)
            return result
        except Exception as exc:
            if attempt == max_retries:
                return {"error": str(exc), "model": ANTHROPIC_MODEL,
                        "queried_at": datetime.now(timezone.utc).isoformat()}
            time.sleep(2 ** attempt)


# ── Stage 4 — auto-annotate gate ─────────────────────────────────────────────

def osm_has_unambiguous_tag(source: dict) -> Optional[str]:
    """If OSM enrichment found one of our hard-fire-source tags, return it."""
    osm = source.get("osm_tags") or {}
    for m in osm.get("matches", []):
        if m.get("category") in OSM_UNAMBIGUOUS_TAGS:
            return m.get("category")
    return None


def annotate_source(source: dict, vision: dict) -> tuple:
    """Apply Stage-4 gate. Returns (decision, new_annotation_dict).

    decision is one of: 'auto-osm', 'auto-vision', 'queue-review'.
    """
    osm_match = osm_has_unambiguous_tag(source)
    if osm_match:
        return "auto-osm", {
            "auto_category": osm_match,
            "auto_source": "osm",
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
    vconf = vision.get("confidence", 0.0) or 0.0
    vprim = vision.get("primary")
    if "error" in vision:
        return "queue-review", {"reason": f"vision_error: {vision['error']}",
                                "decided_at": datetime.now(timezone.utc).isoformat()}
    if vconf >= VISION_CONFIDENCE_THRESHOLD and vprim:
        return "auto-vision", {
            "auto_category": vprim,
            "auto_confidence": vconf,
            "auto_operator": vision.get("operator"),
            "auto_secondary": vision.get("secondary_features"),
            "auto_notes": vision.get("notes"),
            "auto_source": "claude_sonnet",
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
    return "queue-review", {
        "reason": f"low_vision_conf={vconf}",
        "vision_primary": vprim,
        "vision_confidence": vconf,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }


def append_review_queue(source: dict, decision_info: dict, tile_path: Path) -> None:
    """Add a low-confidence candidate to the review queue (batched daily to Mark)."""
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source_id": source["id"],
        "lat": source["lat"], "lon": source["lon"],
        "radius_km": source["radius_km"],
        "google_maps_satellite": source.get("google_maps", {}).get("satellite"),
        "esri_tile_path": str(tile_path),
        "decision_info": decision_info,
    }
    with open(REVIEW_QUEUE, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ── Orchestrator ─────────────────────────────────────────────────────────────

def run_pipeline(source_id_filter: Optional[str] = None) -> None:
    data = json.loads(SOURCES_JSON.read_text())
    sources = data["sources"]
    api_key = _load_anthropic_key()

    n_auto_osm = n_auto_vision = n_queue = n_error = 0
    for s in sources:
        if source_id_filter and s["id"] != source_id_filter:
            continue
        logger.info("Processing %s @ (%.4f, %.4f) r=%.1fkm",
                    s["id"], s["lat"], s["lon"], s["radius_km"])

        tile_path = TILES / f"{s['id']}.jpg"
        if not tile_path.exists():
            try:
                download_esri_tile(s["lat"], s["lon"], s["radius_km"], tile_path)
                logger.info("  -> tile downloaded %.0f KB", tile_path.stat().st_size / 1024)
            except Exception as exc:
                logger.error("  !! tile download failed: %s", exc)
                n_error += 1
                continue

        vision = classify_with_claude(tile_path, s, api_key)
        if "error" in vision:
            logger.warning("  !! vision error: %s", vision["error"])
        else:
            logger.info("  vision: %s (conf=%.2f) op=%s",
                        vision.get("primary"), vision.get("confidence", 0),
                        vision.get("operator"))
        s["vision_result"] = vision

        decision, annotation = annotate_source(s, vision)
        s["annotation"] = annotation
        if decision == "auto-osm":
            n_auto_osm += 1
            logger.info("  -> auto-annotated via OSM: %s", annotation["auto_category"])
        elif decision == "auto-vision":
            n_auto_vision += 1
            logger.info("  -> auto-annotated via vision: %s", annotation["auto_category"])
        else:
            n_queue += 1
            append_review_queue(s, annotation, tile_path)
            logger.info("  -> QUEUED for review: %s",
                        annotation.get("reason", "low confidence"))

    data["metadata"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["metadata"]["pipeline_run_summary"] = {
        "auto_osm": n_auto_osm,
        "auto_vision": n_auto_vision,
        "queued_for_review": n_queue,
        "errors": n_error,
    }
    SOURCES_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nPipeline complete: {n_auto_osm} auto-osm, {n_auto_vision} auto-vision, "
          f"{n_queue} queued, {n_error} errors")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None,
                        help="Only process this source ID (for debugging)")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run_pipeline(args.source)


if __name__ == "__main__":
    main()
