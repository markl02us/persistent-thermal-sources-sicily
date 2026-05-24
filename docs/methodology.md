# Methodology — How the catalog was built

## Step 1: Mining (`scripts/miner.py`)

Read the last 30 days of `internal_fires` (PHOENIX detections) and
`external_fires` (FIRMS, EUMETSAT, etc.). For each lat/lon cell (rounded
to 0.01°), count:
- Number of hits
- Number of distinct days
- Whether a Sentinel-2 burn-scar verification ever returned `verified_burn = True`

Flag candidates as: hits ≥ 6, days ≥ 3, no S-2-verified burn. These are
the "persistent thermal sources" — pixels that show heat repeatedly over
weeks without ever leaving a real fire scar.

## Step 2: Esri tile download

For each candidate, download a 250m × 250m Esri World Imagery tile centered
on the lat/lon. Cache as `evidence/<candidate_id>.png`.

## Step 3: AI-vision classification (`scripts/pipeline.py`)

Send each tile to Claude Sonnet 4.5 with a structured prompt:

> "What is in this satellite image? Categorize as one of: volcanic vent,
> industrial site (refinery/factory), glasshouse complex, solar farm,
> quarry, urban area, agricultural burn, fire scar, other. Provide
> confidence 0.0–1.0 and 1-sentence reasoning."

Parse the JSON response. If `confidence ≥ 0.85` and category is in the
auto-annotate list (industrial/glasshouse/solar/quarry/urban), promote
the candidate to a permanent FP zone. Otherwise queue for human review.

## Step 4: OSM enrichment (`scripts/osm_enrich.py`)

For each promoted candidate, query OpenStreetMap Overpass API for tags
within 500m of the lat/lon. Common matches:
- `landuse=industrial` + `industrial=refinery` → confirms refinery
- `landuse=greenhouse_horticulture` → confirms glasshouse
- `power=plant` + `plant:source=solar` → confirms solar farm
- `landuse=quarry` → confirms quarry

Add OSM tags + Wikidata link (if available) to the source record.

## Step 5: Final source-card

Each promoted source gets a JSON object in `data/sources.json`:

```json
{
  "id": "fp_pachino_tomato_2",
  "lat": 36.7152,
  "lon": 15.0989,
  "radius_km": 1.5,
  "category": "glasshouse_complex",
  "subcategory": "vegetable_greenhouse",
  "name_it": "Serre Pachino (Marzamemi)",
  "name_en": "Pachino glasshouse complex (Marzamemi)",
  "osm_tags": {"landuse": "greenhouse_horticulture"},
  "vision_result": {
    "model": "claude-sonnet-4.5",
    "category": "glasshouse complex",
    "confidence": 0.92,
    "reasoning": "Large grid of rectangular semi-transparent structures..."
  },
  "google_maps_url": "https://maps.google.com/?q=36.7152,15.0989",
  "first_seen": "2026-05-20T14:00:00Z",
  "last_verified": "2026-05-24T12:00:00Z"
}
```

## Step 6: Human review (low-confidence batch)

Candidates with `confidence < 0.85` accumulate into a daily review batch
that the maintainer (Mark L.) reviews individually with the Esri tile +
OSM tags + nearby PHOENIX detections.

Approved → added to sources.json. Rejected → added to `data/excluded.json`
with reason.

## Maintenance cadence

- Daily: mining + AI-vision passes (automated)
- Weekly: maintainer review of low-confidence batch
- On-demand: community contributions via GitHub issues

## Independent verifiers

A source is considered well-validated when ≥2 of these agree:
1. AI-vision classification confidence ≥ 0.85
2. OSM tag match
3. Wikidata entry exists for the named site
4. ≥30-day persistence in the thermal anomaly record
