# Weekly FP report — 2026-05-29 v1.2.0 delta

This is the v1.2.0 follow-up to
[`WEEKLY_FP_REPORT_2026_05_29.md`](WEEKLY_FP_REPORT_2026_05_29.md) (v1.1.0,
top-50 OSM-only batch). v1.2.0 processes the full 7-day novel-candidate set,
not just the top 50, and adds Claude Sonnet vision classification on cells
that OSM could not resolve.

---

## Summary

- **421 raw persistent cells** mined from PHOENIX `internal_fires` (window
  2026-05-22 → 2026-05-29, ≥3 hits, ≥2 distinct days, 0.01° spatial grid)
- **23** already covered by existing catalog masks
- **398 novel candidates** — full set, not capped at top 50
  - **55** auto-promoted via OSM landuse match within 600 m (unambiguous tag)
  - **229** auto-promoted via Claude Sonnet vision (conf ≥ 0.85, persistent FP
    class)
  - **114** queued for manual review (vision low-conf, ambiguous, or labelled
    `agricultural_field` / `forest` / `possible_fire` / `unknown`)

**Net effect on the public catalog:**

| Version | Catalog size |
|---------|--------------|
| v1.0.0  |  18 entries (static seed) |
| v1.1.0  |  34 entries (+16 from top-50 OSM batch) |
| v1.2.0  | 318 entries (+284 from full 7-day batch w/ OSM + vision) |

Vision spend: **$0.85 USD** (Sonnet 4.5, ~86 batched calls × 4 tiles each).

---

## Added entries by category (v1.2.0 batch only)

| Category             | Added |
|----------------------|-------|
| glasshouse_complex   |  218  |
| quarry               |   24  |
| urban                |   22  |
| solar_farm           |   10  |
| industrial           |   10  |
| **Total**            |  284  |

The glasshouse_complex dominance reflects the same finding as v1.1.0: the
Vittoria-Comiso-Pachino "fascia trasformata" greenhouse belt is the single
largest persistent-FP source in Sicilian satellite IR.

---

## OSM vs vision split

- **OSM auto-promotions (55):** unambiguous landuse polygon within 600 m,
  mapped via Overpass bulk fetch (10,160 Sicilian landuse features cached at
  `data/sicily_landuse_2026_05_29.json`).
- **Vision auto-promotions (229):** Esri World Imagery sub-meter tile passed
  to Claude Sonnet 4.5; promoted only if `confidence ≥ 0.85` AND the predicted
  class is in the persistent-FP set (glasshouse, solar, landfill, urban,
  industrial, refinery, petrochemical, power plant, cement, quarry, port,
  wastewater, gas flare, wind farm).

### Vision confidence distribution (229 auto-promoted)

| Bucket    | Count |
|-----------|-------|
| 0.95-1.00 |   93  |
| 0.90-0.95 |   77  |
| 0.85-0.90 |   59  |

The 0.85 floor is conservative; tightening to 0.90 would drop ~26% of the
vision-promotions but would also reduce already-low risk of contaminated
masks. For now we keep 0.85 and rely on the standing rule that any
real-fire ground truth that intersects a catalog mask retires/splits the
mask.

---

## Manual-review queue (114 cells)

Saved to [`data/fp_review_batch_2026_05_29.json`](../data/fp_review_batch_2026_05_29.json)
and indexed in [`MANUAL_REVIEW_BATCH_2026_05_29.md`](MANUAL_REVIEW_BATCH_2026_05_29.md).
Cells fall into three groups:

1. **Vision low-confidence (most common)** — Sonnet returned a label but with
   conf < 0.85 (typically tile contained mixed land cover, or the cell sat on
   a transition between e.g. greenhouse and farmland).
2. **Vision unknown** — the tile genuinely did not contain a recognizable
   feature class (often clouds in the Esri composite, or rural-mixed).
3. **Vision flagged `agricultural_field` / `forest` / `possible_fire`** — by
   policy these are NEVER auto-promoted; agricultural burns and real-fire
   candidates need human review before being added to the FP catalog.

The review batch is intentionally kept out of the public catalog. Until
further analysis, none of these 114 cells should be treated as known FPs.

---

## Caveats

- 600-m OSM match radius is a heuristic. Large polygons (greenhouse belts,
  industrial zones) are reliably captured; small distributed features
  (small solar arrays, isolated transformer yards) can miss.
- Esri World Imagery composite date varies by tile. A small fraction of
  tiles may show pre-2024 conditions; this is acceptable for steady-state
  features (greenhouses, quarries, power plants) but could miss recently
  built/decommissioned sites.
- The 0.85 vision-confidence threshold is calibrated by hand. Future runs
  should track promoted-mask precision against ground truth and adjust.
- Vision was run with 4 tiles per call (cost optimization) and a structured
  JSON-array response. We observed near-zero misalignment between tile
  index and returned object, but the manual-review batch is the safety net.

---

## Methodology recap

1. **Mine** — pull all PHOENIX `internal_fires` rows in 7-day window from
   sources `subpixel_v1_alpha`, `wind_diff`, `fci_l1c`. Cluster into 0.01°
   (~1 km) grid cells. Keep cells with ≥3 hits across ≥2 distinct days.
2. **Subtract** — drop any cell that falls inside an existing catalog
   mask (haversine ≤ entry's `radius_km`).
3. **OSM** — bulk-fetch Sicily-wide landuse polygons (Overpass), then
   point-in-circle match within 600 m. Unambiguous priority categories
   (greenhouse, solar, landfill, industrial, quarry, port, wastewater)
   → auto-promote. Pure residential-only → auto-promote as urban_heat.
4. **Vision** — for every remaining cell, fetch a 1024×1024 px Esri World
   Imagery tile centered on the cell (~1 km bbox) and ask Sonnet 4.5
   what's there. Batch 4 tiles per call for cost efficiency.
5. **Promote** — vision conf ≥ 0.85 AND class ∈ persistent-FP set → add
   to catalog with mask radius from vision suggestion (clipped to 0.3–1.0 km).
6. **Review queue** — everything else.

All scripts: see [`scripts/`](../scripts/), runtime helper at
`C:/Users/markl/AppData/Local/Temp/fp_v1_2_0_pipeline.py` (local; same
logic as committed scripts).

---

*Generated 2026-05-29 by Alessandria Della Rocca Applications. Open data
under CC-BY 4.0. Comments + corrections welcome at `adrwildfi@gmail.com`
or via GitHub issues at `markl02us/persistent-thermal-sources-sicily`.*
