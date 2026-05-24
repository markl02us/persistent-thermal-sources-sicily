"""Auto-categorize PHOENIX persistent thermal sources via OpenStreetMap Overpass.

For each source in sources.json, query Overpass within radius_km to find:
  - power plants (esp. solar, wind, geothermal)
  - refineries / chimneys / industrial sites
  - volcanic features
  - waste / landfill / cement / mining

Adds the OSM tags + a best-guess subcategory back into the record. Free, no
API key, no commercial dependency. Respects Overpass's polite-use limits.

Usage:
  python3 osm_enrich.py
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/mark/phoenix_false_positives")
OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "PHOENIX-FP-catalog/1.0 (mark@phoenix-wildfire.org)"

# Tag dictionary worth looking for. Each key is a tag pair, value is the
# inferred PHOENIX subcategory.
INTEREST_TAGS = {
    ("power", "plant"):                 "power_plant",
    ("power", "generator"):             "power_generator",
    ("plant:source", "solar"):          "solar_farm",
    ("generator:source", "solar"):      "solar_farm",
    ("plant:source", "wind"):           "wind_farm",
    ("plant:source", "geothermal"):     "geothermal_plant",
    ("plant:source", "gas"):            "gas_plant",
    ("plant:source", "coal"):           "coal_plant",
    ("plant:source", "oil"):            "oil_plant",
    ("man_made", "chimney"):            "industrial_chimney",
    ("man_made", "flare"):              "gas_flare",
    ("man_made", "petroleum_well"):     "petroleum_well",
    ("man_made", "works"):              "industrial_works",
    ("industrial", "refinery"):         "refinery",
    ("industrial", "petrochemical"):    "petrochemical",
    ("industrial", "depot"):            "industrial_depot",
    ("landuse", "industrial"):          "industrial_landuse",
    ("landuse", "quarry"):              "quarry",
    ("landuse", "landfill"):            "landfill",
    ("landuse", "farmyard"):            "farmyard_warehouse",
    ("man_made", "wastewater_plant"):   "wastewater_plant",
    ("natural", "volcano"):             "volcano",
    ("geological", "volcanic_caldera_rim"): "volcanic_caldera",
}


def query_overpass(lat: float, lon: float, radius_m: int, timeout_s: int = 25) -> dict:
    """Run a single Overpass query around a coordinate."""
    q = f"""
    [out:json][timeout:{timeout_s}];
    (
      node(around:{radius_m},{lat},{lon});
      way(around:{radius_m},{lat},{lon});
      relation(around:{radius_m},{lat},{lon});
    );
    out tags center;
    """
    data = urllib.parse.urlencode({"data": q}).encode()
    req = urllib.request.Request(OVERPASS, data=data,
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s + 5) as resp:
        return json.loads(resp.read().decode())


def summarize_features(payload: dict) -> dict:
    """Extract interesting OSM features matching INTEREST_TAGS."""
    matches = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        for (k, v), category in INTEREST_TAGS.items():
            if tags.get(k) == v:
                matches.append({
                    "osm_id": f"{el.get('type','?')[0]}{el.get('id')}",
                    "category": category,
                    "name": tags.get("name") or tags.get("operator") or tags.get("ref"),
                    "tags": {k: tags.get(k) for k in (
                        "name", "operator", "ref", "plant:source",
                        "generator:source", "industrial", "landuse",
                        "man_made", "power", "natural", "geological",
                    ) if k in tags},
                })
                break
    return {
        "n_matches": len(matches),
        "matches": matches,
        "queried_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    src_path = ROOT / "sources.json"
    data = json.loads(src_path.read_text())
    sources = data["sources"]
    for i, rec in enumerate(sources):
        lat, lon, r_km = rec["lat"], rec["lon"], rec["radius_km"]
        radius_m = int(r_km * 1000)
        print(f"[{i+1}/{len(sources)}] {rec['id']}  ({lat},{lon})  r={r_km}km")
        try:
            payload = query_overpass(lat, lon, radius_m)
            summary = summarize_features(payload)
            rec["osm_tags"] = summary
            top = summary["matches"][:5]
            print(f"   -> {summary['n_matches']} matches; top: "
                  + ", ".join(f"{m['category']}({m.get('name') or '-'})" for m in top))
        except Exception as exc:
            print(f"   !! Overpass error: {exc}")
            rec["osm_tags"] = {"error": str(exc),
                               "queried_at": datetime.now(timezone.utc).isoformat()}
        # Polite-use throttle: at most one query per 5s
        time.sleep(5)

    data["metadata"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    data["metadata"]["osm_enriched"] = True
    src_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"\nUpdated {src_path}")


if __name__ == "__main__":
    main()
