# Weekly false-positive report — 2026-05-29

**Window analyzed:** 2026-05-22 → 2026-05-29 (7 days)
**Snapshot source:** PHOENIX `internal_fires` table via laptop shadow snapshot
**Methodology:** persistent thermal cells (≥3 hits on ≥2 distinct days within
a 0.01° lat/lon cell) cross-referenced against OSM landuse polygons within
600 m radius via Overpass bulk fetch.

---

## Summary

- **459 raw persistent cells** observed (≥3 hits, ≥2 distinct days)
- **57** already covered by existing FP-catalog entries (volcanic, refinery, etc.)
- **402** novel candidates — top 50 by hit-count classified via OSM
  - **16 auto-promoted** to public catalog (high-confidence OSM landuse match)
    - 10 greenhouse complexes (Vittoria-Comiso-Pachino "fascia trasformata")
    - 3 photovoltaic solar farms
    - 2 urban heat-island patches
    - 1 landfill
  - **34 manual-review candidates** with no OSM landuse match → see
    `data/fp_review_batch_2026_05_29.json`

The 16 auto-promotions bring the catalog from 18 to **34 entries**. They are
flagged `"annotation": "auto"` so downstream consumers can choose to apply
stricter human-review filters if they want.

---

## Why this batch matters for external researchers

If you are building a wildfire detector using free satellite feeds (FIRMS,
EUMETSAT MTG, S-3 SLSTR), our experience is that **the single biggest source
of false positives in Sicily is the greenhouse belt** between Vittoria, Comiso,
Acate, and Pachino. The polyethylene polytunnels covering ~25,000 ha of land
present:

1. A persistent MIR (3.8 μm) thermal signature, especially at sunset when the
   trapped daytime heat is released through the plastic envelope.
2. A pseudo-FRP that can easily reach 5-35 MW per FIRMS cell, well above the
   noise floor.
3. A non-moving footprint that triggers PHOENIX's `wind_diff` motion-thermal
   detector if the mask is not in place.

The 10 greenhouse cells in this batch are not "hidden" — many are tagged in
OSM as `landuse=greenhouse_horticulture`. We hope this catalog makes it
trivial for downstream consumers to apply a sensible exclusion mask without
re-doing the discovery work.

The 3 solar-farm cells are similar: PV array surface temperatures can lift
2-3 K above ambient air, and the panel albedo creates MIR reflections at low
solar angles that fool single-band heat detectors. The 3 in this batch are
all in Caltagirone-Catania province.

---

## Auto-promoted entries (this batch)

| ID                                  | Lat / Lon         | Category            | Subcategory             | OSM match               | Hits 7d | FRP avg | Conf |
|-------------------------------------|-------------------|---------------------|--------------------------|--------------------------|---------|---------|------|
| phoenix-36_9111-14_4283             | 36.9111, 14.4283  | glasshouse_complex  | plastic_polytunnel       | greenhouse,urban         | 9       | 5.4 MW  | 0.59 |
| phoenix-36_7915-14_6081             | 36.7915, 14.6081  | glasshouse_complex  | plastic_polytunnel       | greenhouse_horticulture  | 8       | 34.9 MW | 0.74 |
| phoenix-36_9711-14_3681             | 36.9711, 14.3681  | glasshouse_complex  | plastic_polytunnel       | greenhouse_horticulture  | 8       | 20.4 MW | 0.73 |
| phoenix-37_5094-14_9896             | 37.5094, 14.9896  | industrial          | landfill                 | landuse=landfill         | 8       | 4.5 MW  | 0.65 |
| phoenix-37_5506-14_9297             | 37.5506, 14.9297  | glasshouse_complex  | plastic_polytunnel       | greenhouse_horticulture  | 8       | 3.1 MW  | 0.58 |
| phoenix-36_7900-14_5900             | 36.7900, 14.5900  | glasshouse_complex  | plastic_polytunnel       | greenhouse,urban         | 7       | 5.4 MW  | 0.61 |
| phoenix-36_8900-14_4300             | 36.8900, 14.4300  | glasshouse_complex  | plastic_polytunnel       | greenhouse,urban         | 7       | 4.0 MW  | 0.58 |
| phoenix-36_9702-14_5103             | 36.9702, 14.5103  | urban               | urban_heat_island        | landuse=residential      | 7       | 1.4 MW  | 0.60 |
| phoenix-37_0696-14_7896             | 37.0696, 14.7896  | solar_farm          | photovoltaic_array       | generator:source=solar   | 7       | 1.5 MW  | 0.52 |
| phoenix-37_1300-14_1100             | 37.1300, 14.1100  | glasshouse_complex  | plastic_polytunnel       | greenhouse_horticulture  | 7       | 9.7 MW  | 0.62 |
| phoenix-37_1503-14_6693             | 37.1503, 14.6693  | solar_farm          | photovoltaic_array       | generator:source=solar   | 7       | 4.4 MW  | 0.58 |
| phoenix-37_4502-15_0493             | 37.4502, 15.0493  | solar_farm          | photovoltaic_array       | generator:source=solar   | 7       | 4.8 MW  | 0.61 |
| phoenix-36_7700-14_6100             | 36.7700, 14.6100  | urban               | urban_heat_island        | landuse=residential      | 6       | 18.3 MW | 0.63 |
| phoenix-36_7700-14_6300             | 36.7700, 14.6300  | glasshouse_complex  | plastic_polytunnel       | greenhouse,urban         | 6       | 11.6 MW | 0.63 |
| phoenix-36_7920-14_4875             | 36.7920, 14.4875  | glasshouse_complex  | plastic_polytunnel       | greenhouse,urban         | 6       | 20.4 MW | 0.72 |
| phoenix-36_8100-14_4900             | 36.8100, 14.4900  | glasshouse_complex  | plastic_polytunnel       | greenhouse_horticulture  | 6       | 11.0 MW | 0.63 |

---

## Manual-review batch (34 cells)

Stored at `data/fp_review_batch_2026_05_29.json`. These cells fired persistently
but did not match any OSM landuse polygon within 600 m. They could be:

- **Real agricultural burns** (Mediterranean stubble-burning is common between
  April-July; in Italy these are regulated but not eliminated).
- **Real undocumented small fires** that have not yet shown up in FIRMS / VIIRS.
- **Persistent thermals on land features not yet tagged in OSM** (small
  industrial sites, transformer yards, fruit-drying floors).

Until further analysis, these cells should NOT be auto-classified as FPs. They
will be reviewed individually with Esri World Imagery + nearby PHOENIX
detections, and either promoted to the catalog with appropriate subcategory
or retired as one-off detections.

---

## Caveats

- The 600-m radius for OSM matching is a heuristic — large industrial zones
  (Augusta-Priolo, Termini Imerese) extend well past that. Existing catalog
  entries handle those with `radius_km` ≥ 5.0; new entries default to 0.5 km.
- OSM coverage in rural Sicily is uneven; the 34 "unmatched" cells are
  partially an artifact of patchy OSM coverage, not necessarily evidence
  that no FP source exists there.
- This is a 7-day snapshot. Cells that are seasonally persistent (e.g.,
  active greenhouse periods) may dominate a given week; cells that are
  intermittent will need multi-week observation.

---

## What's next

- **2026-06-05** — repeat this analysis on the next week's data; converge
  toward a stable, monitored catalog.
- **Manual review of the 34 unmatched cells** — Mark + Gaetano walk through
  the Esri imagery + OSM tags individually.
- **Backfill 30-day analysis** — once the laptop pipeline catches up,
  expand the window from 7 to 30 days for the next batch.
- **Cross-validate against FIRMS confirmations** — if a "FP candidate" cell
  ever gets a VIIRS-confirmed fire, retire it from the catalog (or split
  the polygon).

---

*Generated 2026-05-29 by Alessandria Della Rocca Applications. Open data
under CC-BY 4.0. Comments + corrections welcome at `adrwildfi@gmail.com` or
via GitHub issues at `markl02us/persistent-thermal-sources-sicily`.*
