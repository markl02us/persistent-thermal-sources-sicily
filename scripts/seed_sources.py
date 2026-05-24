"""Generate the initial PHOENIX persistent-thermal-sources catalog.

Sources:
  - INGV-HOTSAT prior knowledge (Mt Etna, Stromboli, Vulcano)
  - Round-2 council research (Augusta-Priolo-Melilli, Gela, Milazzo, Termini, Catania)
  - Mark Ludwikowski direct (solar farms — to be mined + categorized)
  - PHOENIX internal_fires history (auto-mining of cells firing N+ times without VIIRS confirmation)

Outputs:
  /home/mark/phoenix_false_positives/sources.json   ← canonical machine-readable
  /home/mark/phoenix_false_positives/sources.geojson ← map-ready
  /home/mark/phoenix_false_positives/sources.csv     ← spreadsheet-friendly
  /home/mark/phoenix_false_positives/README.md       ← methodology + license

CC-BY 4.0 licensed for free public reuse.
"""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/mark/phoenix_false_positives")

# Initial seed — Sicily/Aeolian persistent thermal sources we already know about.
# Each entry will be enriched by the OSM auto-categorization pass (next script).
SEED_SOURCES = [
    # Volcanic
    {
        "id": "etna-summit",
        "name_it": "Mt. Etna — crateri sommitali",
        "name_en": "Mt. Etna summit craters",
        "category": "volcanic",
        "subcategory": "active_stratovolcano",
        "lat": 37.7510, "lon": 14.9934, "radius_km": 15.0,
        "country": "IT", "region": "Sicilia", "province": "Catania",
        "operators": ["INGV-Osservatorio Etneo"],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 1.00,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Vulcano attivo. Le anomalie termiche sono dovute all'attivita\u0300 effusiva e fumarolica. Monitorato in continuo da INGV-HOTSAT.",
        "notes_en": "Active volcano. Thermal anomalies driven by effusive and fumarolic activity. Continuously monitored by INGV-HOTSAT.",
        "evidence_urls": [
            "https://www.ingv.it/",
            "https://www.ct.ingv.it/",
        ],
        "google_maps": None,  # filled by enrichment
        "osm_tags": None,
        "sources_cited": ["INGV-HOTSAT", "PHOENIX-static"],
    },
    {
        "id": "stromboli",
        "name_it": "Vulcano Stromboli",
        "name_en": "Stromboli volcano",
        "category": "volcanic",
        "subcategory": "continuously_active_volcano",
        "lat": 38.789, "lon": 15.213, "radius_km": 5.0,
        "country": "IT", "region": "Sicilia", "province": "Messina",
        "operators": ["INGV"],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 1.00,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Vulcano in attivit\u00e0 continua. Esplosioni stromboliane permanenti.",
        "notes_en": "Continuously active volcano. Permanent Strombolian explosions.",
        "evidence_urls": ["https://www.ct.ingv.it/"],
        "google_maps": None,
        "osm_tags": None,
        "sources_cited": ["INGV-HOTSAT", "PHOENIX-static"],
    },
    {
        "id": "vulcano-fossa",
        "name_it": "Cratere La Fossa (Isola di Vulcano)",
        "name_en": "La Fossa crater (Vulcano island)",
        "category": "volcanic",
        "subcategory": "fumarolic_field",
        "lat": 38.404, "lon": 14.964, "radius_km": 3.0,
        "country": "IT", "region": "Sicilia", "province": "Messina",
        "operators": ["INGV"],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 0.95,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Campo fumarolico attivo. Anomalie termiche da emissioni di gas vulcanici.",
        "notes_en": "Active fumarolic field. Thermal anomalies from volcanic gas emissions.",
        "evidence_urls": ["https://www.ct.ingv.it/"],
        "google_maps": None,
        "osm_tags": None,
        "sources_cited": ["INGV", "PHOENIX-static"],
    },

    # Industrial — petrochemical / refining
    {
        "id": "augusta-priolo-melilli",
        "name_it": "Polo petrolchimico Augusta-Priolo-Melilli",
        "name_en": "Augusta-Priolo-Melilli petrochemical complex",
        "category": "industrial",
        "subcategory": "petrochemical_gas_flares",
        "lat": 37.20, "lon": 15.20, "radius_km": 5.0,
        "country": "IT", "region": "Sicilia", "province": "Siracusa",
        "operators": ["Sonatrach", "Versalis (Eni)", "ISAB Lukoil"],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 0.98,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Il pi\u00f9 grande complesso petrolchimico d'Europa. Le torce di emergenza producono segnale termico permanente in MIR.",
        "notes_en": "Europe's largest petrochemical complex. Emergency gas flares produce a permanent MIR thermal signature.",
        "evidence_urls": [
            "https://en.wikipedia.org/wiki/Augusta-Priolo_petrochemical_complex",
        ],
        "google_maps": None,
        "osm_tags": None,
        "sources_cited": ["PHOENIX-council-round2", "FIRMS-FP-evidence-2026-05-24"],
    },
    {
        "id": "gela-refinery",
        "name_it": "Raffineria di Gela",
        "name_en": "Gela refinery",
        "category": "industrial",
        "subcategory": "refinery",
        "lat": 37.06, "lon": 14.27, "radius_km": 3.0,
        "country": "IT", "region": "Sicilia", "province": "Caltanissetta",
        "operators": ["Eni Rewind", "Versalis"],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 0.97,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Raffineria convertita in bioraffineria. Mantiene attivit\u00e0 termiche residue.",
        "notes_en": "Refinery converted to biorefinery. Maintains residual thermal activity.",
        "evidence_urls": [],
        "google_maps": None,
        "osm_tags": None,
        "sources_cited": ["PHOENIX-council-round2"],
    },
    {
        "id": "milazzo-refinery",
        "name_it": "Raffineria di Milazzo",
        "name_en": "Milazzo refinery",
        "category": "industrial",
        "subcategory": "refinery_with_flare",
        "lat": 38.22, "lon": 15.24, "radius_km": 3.0,
        "country": "IT", "region": "Sicilia", "province": "Messina",
        "operators": ["RAM (Raffineria di Milazzo) — Eni + Q8"],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 0.97,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Raffineria attiva con torcia di sicurezza permanente.",
        "notes_en": "Active refinery with permanent safety flare.",
        "evidence_urls": [],
        "google_maps": None,
        "osm_tags": None,
        "sources_cited": ["PHOENIX-council-round2"],
    },
    {
        "id": "termini-imerese",
        "name_it": "Ex stabilimento Fiat + porto industriale di Termini Imerese",
        "name_en": "Former Fiat plant + Termini Imerese industrial port",
        "category": "industrial",
        "subcategory": "industrial_port",
        "lat": 37.99, "lon": 13.70, "radius_km": 3.0,
        "country": "IT", "region": "Sicilia", "province": "Palermo",
        "operators": [],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 0.90,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Area industriale dismessa con attivit\u00e0 portuali. Possibili attivit\u00e0 termiche residue.",
        "notes_en": "Decommissioned industrial area with active port. Possible residual thermal activity.",
        "evidence_urls": [],
        "google_maps": None,
        "osm_tags": None,
        "sources_cited": ["PHOENIX-council-round2"],
    },
    {
        "id": "catania-port",
        "name_it": "Porto e area industriale di Catania",
        "name_en": "Catania port and industrial area",
        "category": "industrial",
        "subcategory": "port_industrial",
        "lat": 37.50, "lon": 15.10, "radius_km": 2.0,
        "country": "IT", "region": "Sicilia", "province": "Catania",
        "operators": [],
        "phoenix_detections_30d": None,
        "viirs_confirmed_30d": None,
        "false_positive_confidence": 0.85,
        "first_observed": None,
        "last_observed": None,
        "notes_it": "Attivit\u00e0 portuali e raffinerie minori.",
        "notes_en": "Port activity and small refineries.",
        "evidence_urls": [],
        "google_maps": None,
        "osm_tags": None,
        "sources_cited": ["PHOENIX-council-round2"],
    },
]


def enrich_google_links(rec):
    """Add a Google Maps satellite-view link (no API key required, free URL construction)."""
    lat, lon = rec["lat"], rec["lon"]
    rec["google_maps"] = {
        "satellite": f"https://www.google.com/maps/@{lat},{lon},14z/data=!3m1!1e3",
        "street_view": f"https://www.google.com/maps/@{lat},{lon},3a,75y,90t/data=!3m6!1e1",
        "earth": f"https://earth.google.com/web/@{lat},{lon},0a,1000d",
    }
    return rec


def to_geojson(records):
    return {
        "type": "FeatureCollection",
        "metadata": {
            "name": "PHOENIX persistent thermal sources — Sicily",
            "description": "Locations that consistently appear as fires in overhead satellite imagery but are NOT wildfires (volcanic, industrial, agricultural).",
            "license": "CC-BY 4.0",
            "source": "https://adr-wildfire.com/",
            "version": "1.0.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": r,
            }
            for r in records
        ],
    }


def to_csv_rows(records):
    cols = [
        "id", "name_en", "name_it", "category", "subcategory",
        "lat", "lon", "radius_km",
        "country", "region", "province",
        "false_positive_confidence",
    ]
    return [cols] + [[r.get(c, "") for c in cols] for r in records]


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    records = [enrich_google_links(dict(r)) for r in SEED_SOURCES]
    metadata = {
        "name": "PHOENIX persistent thermal sources — Sicily",
        "description": "Coordinates that consistently appear as 'fires' to overhead satellite sensors (SEVIRI, FCI, MODIS, VIIRS) but are NOT wildfires. Sources are volcanic, industrial (refineries, petrochemical, ports), or persistent natural anomalies. Published as a public good for civil-protection, researchers, and Sicilians who want to interpret FIRMS / EUMETSAT maps correctly.",
        "license": "CC-BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_repo": "https://github.com/phoenix-wildfire/persistent-thermal-sources-sicily",
        "version": "1.0.0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "n_sources": len(records),
        "methodology_summary": "Static seed from INGV-HOTSAT volcanic knowledge + Round-2 council research on Sicilian industrial sites. Auto-enrichment via OpenStreetMap Overpass. Mining of additional candidates from PHOENIX internal_fires history (cells firing >=6x in 12h windows without VIIRS confirmation).",
    }
    (ROOT / "sources.json").write_text(json.dumps({"metadata": metadata, "sources": records}, indent=2, ensure_ascii=False))
    (ROOT / "sources.geojson").write_text(json.dumps(to_geojson(records), indent=2, ensure_ascii=False))
    with open(ROOT / "sources.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in to_csv_rows(records):
            writer.writerow(row)
    print(f"Wrote {len(records)} sources to {ROOT}/")
    print("  - sources.json (canonical)")
    print("  - sources.geojson (map-ready)")
    print("  - sources.csv (spreadsheet)")


if __name__ == "__main__":
    main()
