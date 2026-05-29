# CHANGELOG

Tracks changes to the Persistent Thermal Sources Catalog — Sicily.

Versioning follows [Semantic Versioning](https://semver.org/): patch for
non-data corrections (authorship, metadata), minor for additive data changes
(new entries, new categories), major for breaking schema changes.

---

## [1.2.2] — 2026-05-29 — Burn-scar driven relabel patch (training-positives only)

### Changed

- Applied 8 burn-scar-driven relabel suggestions to the v1.2.1 manual-review session. No mask catalog changes — these are training-positive bucket corrections (7 unsure-to-ag_burn/real_fire promotions, 1 ag_burn-to-unsure regression for a cell still showing persistent thermal activity in the last 48h).
- The objective evidence (Sentinel-2 dNBR scar at the USGS-standard moderate-burn threshold of 0.27 or higher; still-active-in-48h thermal signal for the regression case) disagreed with the v1.2.1 reviewer call for these 8 cells. Auto-applied because (a) dNBR is unambiguous geophysics, and (b) 7 of 8 are pure bucket moves between training-positive files (no FP-mask catalog impact).

### Relabel detail (8 cells)

| idx | from | to | dNBR max | severity | basis |
|---|---|---|---|---|---|
| 0 | unsure | ag_burn | 0.35 | moderate | localized scar NE quadrant ~244m, signal cooled past 48h |
| 2 | unsure | ag_burn | 0.36 | moderate | localized scar SW quadrant ~170m, signal cooled past 48h |
| 18 | unsure | ag_burn | 0.36 | moderate | localized scar SE quadrant ~257m, signal cooled past 48h |
| 40 | ag_burn | unsure | 0.16 | low | no post-window scar, signal STILL ACTIVE in 48h (persistent not transient) |
| 79 | unsure | real_fire | 0.48 | high | localized scar SW quadrant ~185m, signal cooled past 48h |
| 83 | unsure | real_fire | 0.40 | moderate | localized scar SE quadrant ~183m, signal cooled past 48h |
| 84 | unsure | real_fire | 0.48 | high | localized scar SW quadrant ~247m, signal cooled past 48h |
| 100 | unsure | ag_burn | 0.27 | moderate | localized scar NE quadrant ~74m, signal cooled past 48h |

### New session totals after v1.2.2

- classified (mask): 17 (unchanged)
- real_fire: 20 + 3 = 23
- ag_burn: 65 + 4 - 1 = 68
- unsure: 12 - 7 + 1 = 6
- total: 114

### Updated files

- `docs/manual_review_progress_2026_05_29.json` — appended 8 new action records (audit trail preserved; `compute_counts` is last-action-wins per idx, so totals flip correctly).
- `docs/manual_review_real_fire_2026_05_29.json` — added 3 entries (idx 79, 83, 84) with `applied_from = "burn_scar_suggestion"` and `basis` text.
- `docs/manual_review_agricultural_burns_2026_05_29.json` — added 4 entries (idx 0, 2, 18, 100), removed 1 (idx 40).
- `docs/manual_review_unsure_2026_05_29.json` — added 1 entry (idx 40, flagged `removed_from_ag_burn_pending_review`), removed 7 (idx 0, 2, 18, 79, 83, 84, 100).

### Honesty notes

- No mask catalog changes. `data/sources.json`, `data/sources.csv`, `data/sources.geojson` are byte-identical to v1.2.1. Total mask entries remain 335 (318 baseline + 17 from v1.2.1).
- The idx 40 regression preserves audit trail: its original ag_burn evidence is retained as the `basis` field on the new unsure record, and `flag = "removed_from_ag_burn_pending_review"` marks why it was dropped from the training-positive set.
- The dNBR 0.27 threshold is the USGS-standard moderate-burn cutoff (Key & Benson 2006); applying it as the auto-relabel gate aligns with established burn-severity classification.

---

## [1.2.1] — 2026-05-29 — Manual review of 114 ambiguous cells

### Added

- **17 new persistent thermal source entries** from human review of the v1.2.0 manual-review queue (114 cells that did not pass auto-vision thresholds). Reviewer classification breakdown:
  - 17 water_body
- `docs/manual_review_real_fire_2026_05_29.json` — 20 cell(s) flagged as candidate real-fire signal (NOT added to mask catalog; routed to training-positives).
- `docs/manual_review_agricultural_burns_2026_05_29.json` — 65 cell(s) classified as agricultural burns (NOT added to mask catalog; routed to training-positives — same thermal characteristics as wildfire, valuable small-fire detector training signal).
- `docs/manual_review_unsure_2026_05_29.json` — 12 cell(s) left unclassified pending further evidence.
- `docs/manual_review_progress_2026_05_29.json` — full action log for the review session.

### Schema additions

- `annotation`: `"manual"` for human-reviewed entries.
- `annotation_source`: `"manual_review_2026_05_29"`.
- `annotation_confidence`: `"human_high"`.
- `annotation_author`: `"Alessandria Della Rocca Applications"`.

### Honesty notes

- Cells classified as `real_fire` or `ag_burn` were NOT added to the mask catalog and remain visible to downstream PHOENIX detection. They are logged separately as positive training signal for the small-fire detector.
- Cells marked `unsure` remain outside the catalog pending a second review pass or additional sensor evidence.

---

## [1.2.0] — 2026-05-29 — Full 7-day harvest + vision pass (+284 entries)

### Added

- **284 new persistent thermal source entries** from the full PHOENIX
  `internal_fires` 7-day window (not just the top-50 of v1.1.0). Breakdown:
  - 218 glasshouse complexes (continued dominance of the Vittoria-Comiso-Pachino
    polytunnel belt; vision picked up many cells OSM missed due to incomplete
    landuse tagging in rural OSM)
  - 24 quarries
  - 22 urban heat-island cells
  - 10 solar farms
  - 10 industrial sites (light industrial, mixed)
- **55 OSM-promoted** (unambiguous landuse polygon within 600 m).
- **229 vision-promoted** via Claude Sonnet 4.5 satellite-imagery
  classification on Esri World Imagery tiles. Threshold: confidence ≥ 0.85
  AND predicted class ∈ persistent-FP set. Vision cost: $0.85 USD for the
  full batch (86 batched calls × 4 tiles each).
- `docs/WEEKLY_FP_REPORT_2026_05_29_v1_2_0.md` — full v1.2.0 methodology +
  category breakdown + confidence distribution.
- `docs/MANUAL_REVIEW_BATCH_2026_05_29.md` — 114-cell manual review queue
  with per-cell vision result + Google Maps satellite link.
- `data/fp_candidates_2026_05_29.json` regenerated with all 398 novel
  candidates (was 50 in v1.1.0).
- `data/fp_review_batch_2026_05_29.json` updated to 114 vision-flagged cells
  (was 34 OSM-unmatched cells in v1.1.0).
- `data/sources.csv` and `data/sources.geojson` regenerated.

### Schema additions (per-entry, on new auto-promoted records)

- `annotation_source`: `"osm"` or `"vision"` — disambiguates auto-promotion
  origin. Existing v1.1.0 entries kept their schema (some had no
  `annotation_source`; v1.2.0 entries always include it).
- `annotation_confidence`: vision confidence (0.85–1.00) or 0.85/0.90 for
  OSM (synthetic).
- `evidence`: 1-sentence rationale from the vision call or OSM tag string.
- `phoenix_stats`: per-cell hit count + distinct days + avg confidence + avg
  FRP + detection-source list from the originating PHOENIX window.

### Honesty notes

- The vision threshold of 0.85 is conservative but not infallible. Any
  catalog mask that subsequently intersects a FIRMS-confirmed real-fire
  ground truth should be retired or split — that policy is unchanged.
- Vision was batched 4 tiles per call for cost discipline. Output is
  structured JSON-array with per-tile `index` field; no misalignments
  observed but the manual-review queue is the safety net.
- 114 cells did not pass vision auto-promote thresholds. These remain
  outside the public catalog pending human review (Mark + Gaetano).

---

## [1.1.0] — 2026-05-29 — First weekly FP harvest (+16 entries)

### Added

- **16 new persistent thermal source entries** auto-promoted from the
  PHOENIX `internal_fires` 7-day window (2026-05-22..2026-05-29) after
  cross-referencing against OSM landuse polygons via Overpass. Breakdown:
  - 10 glasshouse complexes in the Vittoria-Comiso-Pachino belt
  - 3 photovoltaic solar farms in Caltagirone-Catania province
  - 2 urban heat-island patches
  - 1 landfill
- `docs/WEEKLY_FP_REPORT_2026_05_29.md` — full methodology + auto-promotion
  table + manual-review batch description.
- `data/fp_candidates_2026_05_29.json` — raw classified candidate list (50)
  with OSM categories per cell.
- `data/fp_review_batch_2026_05_29.json` — 34 unmatched cells flagged for
  individual human review (could be agricultural burns, undocumented small
  fires, or OSM coverage gaps).
- `data/sources.geojson` regenerated to include the new entries.

### Why minor not patch

This is the first additive data release after the v1.0.1 governance bump.
Per the semver policy in this CHANGELOG, additive data changes bump the
minor; major would only fire on breaking schema changes. New entries
maintain the existing schema exactly.

### Honesty note

The 16 auto-promoted entries are flagged `"annotation": "auto"` in the
JSON. Downstream consumers who want a hand-curated-only subset can filter
on `annotation == "human"` (the original 18 entries are human-confirmed).
The auto-promotions are intended to be high-confidence (clear OSM match)
but consumers should treat them as machine-classified until manual review
is performed.

---

## [1.0.1] — 2026-05-29 — First operational version

### Changed

- **Authorship attribution updated to "Alessandria Della Rocca Applications"**
  in `CITATION.cff`, `.zenodo.json`, `LICENSE.code`, and `docs/methodology.md`.
  The earlier v1.0.0 attribution to "Ludwikowski, M." remains preserved on
  the v1.0.0 Zenodo deposit for citation continuity; v1.0.1 and forward use
  the organizational attribution.
- Gaetano Zambito named as Sicilian representative co-author in CITATION.cff
  and .zenodo.json (he is the project's INGV-facing point of contact).

### Why this version exists

This is the **first operational version** of the catalog — v1.0.0 was the
initial public deposit. v1.0.1 marks the moment we transitioned from
individual-authored research artifact to organizationally-maintained
operational data product, with a public commitment to continuous monitoring,
versioned updates, and a transparent change log.

The data itself (`data/sources.json`, `data/sources.geojson`, etc.) is
unchanged in v1.0.1 — only metadata + governance.

### Added

- `docs/REPO_MIGRATION_PLAN.md` — internal plan for migrating the repo from
  `markl02us/persistent-thermal-sources-sicily` (the original publisher's
  personal namespace) to an Alessandria Della Rocca Applications organization,
  preserving the Zenodo DOI lineage. No timeline; gated on org-creation
  decision.

### Versioning policy going forward

- **Patch (1.0.x)** for metadata/governance updates with no change to data
  entries. Catches typos, fixes attributions, updates CITATION metadata.
- **Minor (1.x.0)** for additive data changes — new persistent-thermal-source
  entries, new category classifications, expanded confidence scores. Existing
  entries' meaning never changes inside a minor release.
- **Major (x.0.0)** for breaking schema changes — renaming fields, removing
  categories, removing entries. Forces re-validation of downstream consumers.

The intent of regular minor releases is to demonstrate to external researchers
and operational consumers that this catalog is actively monitored and
continually improved — not a one-off deposit.

---

## [1.0.0] — 2026-05-24 — Initial Zenodo deposit

- Inaugural public release of the Persistent Thermal Sources Catalog for
  Sicily.
- 18+ entries across volcanic, industrial, glasshouse, quarry, and solar-farm
  categories.
- CC-BY 4.0 (data) + MIT (code).
- Zenodo DOI `10.5281/zenodo.20369891`.
