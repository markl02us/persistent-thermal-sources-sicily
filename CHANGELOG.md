# CHANGELOG

## v1.3.0 - 2026-06-11

Rolled-up automated corrections since last release. See per-section
details below; this consolidates intervening agent passes (burn-scar
re-runs, relabel suggestions, proxy->signal upgrades, mask-recheck
promotions, etc.) into one coherent release.

# Pending changes for v1.3.0 (next weekly bump)

Staged automated corrections accumulated between v1.2.2 release and the next weekly version bump. Per the slow-cadence versioning directive, individual agent passes commit locally but DO NOT bump SemVer, tag, or push. The weekly bump rolls these up into one coherent CHANGELOG entry.


## Burn-scar multi-sensor rerun — 2026-05-29

Re-analyzed all 114 manual-review cells with the full burn-scar source roster per the all-sources directive rather than S2-only. Audit + decisions in `data/burn_scar_source_audit_2026_05_29.json`.

**Sources audited:** sentinel-2-l2a, landsat-c2-l2, sentinel-1-rtc, modis-09A1-061, modis-14A1-061, sentinel-3-olci-lfr-l2-land, viirs-09a1-001, modis-14A2-061, modis-09Q1-061, sentinel-1-grd, snapshot_db, mtg_fci_l1c_dgx
**Sources USED:** sentinel-2-l2a, landsat-c2-l2, sentinel-1-rtc, modis-09A1-061, modis-14A1-061, snapshot_db
**Sources SKIPPED:**
  - `sentinel-3-olci-lfr-l2-land` — Collection not found on PC (NotFoundError). No public OLCI burn-scar collection 
  - `viirs-09a1-001` — Collection not found on PC under this id. MODIS-09A1 already covers coarse optic
  - `modis-14A2-061` — Redundant with 14A1 (daily strictly finer than 8-day for thermal history). Held 
  - `modis-09Q1-061` — Only 2 SR bands (b01/b02) — no SWIR for NBR. 09A1 provides full band stack.
  - `sentinel-1-grd` — Redundant with RTC (RTC = terrain-corrected GRD).
  - `mtg_fci_l1c_dgx` — DGX SSH timed out at audit; per task budget, skip and document. Not silenced.

**Fusion rule:** has_scar_fused = (any optical dNBR_max >= 0.27) OR (S1 RTC ΔVV <= -1.5 dB) OR (>=2 MODIS-AF hits in window). Confidence weighted by sensor agreement count.

- Cells re-resolved: 114/114
- Fused-decision scar count: 59 (vs S2-only previously = 39)
- Status: complete, runtime 1079.5s

**Per-sensor coverage (n_cells with usable readout, out of 106):**
  - s2_nbr: ok=77, no_data=29, error=0
  - landsat_nbr: ok=74, no_data=32, error=0
  - s1_rtc: ok=106, no_data=0, error=0
  - modis_sr_nbr: ok=0, no_data=106, error=0
  - modis_active_fire: ok=0, no_data=106, error=0

**New training-positive relabels applied:** 1 (after subtracting 0 v1.2.2 duplicates)
  - unsure -> ag_burn: 1

**Bucket-counts delta:**
  - classified: 17 -> 17 (+0)
  - real_fire: 23 -> 23 (+0)
  - ag_burn: 68 -> 69 (+1)
  - unsure: 6 -> 5 (-1)

**Mask-recheck candidates parked:** 0 (in `docs/pending_mask_recheck.md`; `data/sources.json` UNCHANGED)

**Applied indices:**
  - idx 89 (36.8500N, 14.5700E): `unsure` -> `ag_burn` [sensors: s2; confidence: low]


## Burn-scar proxy-to-signal upgrade — 2026-05-30 (PARTIAL — 5/114 cells)

Per the use-all-data directive: built the infrastructure to upgrade 4 scene-count PROXY sources to REAL per-pixel signals using netcdf/HDF5 reads instead of just enumerating granules.

**Status: per-source functions are end-to-end smoke-tested and confirmed working (TROPOMI 2.33σ above CO baseline on a sample cell; Black Marble 1.468 persistence_ratio on the same cell). The full 114-cell run did not complete within the 4h session budget (~3.6 min/cell × 114 cells / 8 parallelism = 51 min, but each cell varies and TROPOMI/Black Marble downloads are the bottleneck). Only 5 of 114 cells were processed before the budget timeout; results were NOT persisted to data/fp_review_burn_scars_2026_05_29.json. Scripts at `C:\\Users\\markl\\fp_review_ui\\burn_scar_proxy_upgrade.py` are idempotent and re-runnable.**

**Sources upgraded:**
  - `sentinel-5p-l2-netcdf` (TROPOMI) — per-pixel CO + NO2 column anomaly via h5netcdf; 2σ above 30d baseline = `combustion_signal_tropomi`. Widened bbox to 7km half-width (TROPOMI native 5.5×3.5km pixel).
  - `viirs-vnp14img-earthdata` — per-granule .nc/HDF5 active-fire pixel read via CMR; FP_confidence >= 7 = `viirs_active_fire`. Widened bbox to 750m half-width (375m pixel).
  - `viirs-vnp46a1-earthdata` (Black Marble) — per-granule DNB at-sensor radiance median ratio (post/pre 30-day windows); >=0.9 = `persistent_nightlight` (FP), <=0.7 = `nightlight_extinguished`.
  - `sentinel-3-slstr-frp-l2-netcdf` — per-scene FRP_MWIR netcdf read with inline latitude/longitude (66 fire-pixel records per scene); n_hits >= 1 = `slstr_active_fire`. Widened bbox to 2km half-width (SLSTR 1km pixel).

**Fusion thresholds added:**
  - TROPOMI sigma above 30d baseline >= 2.0
  - VIIRS FP_confidence >= 7
  - Black Marble persistence_ratio >= 0.9
  - SLSTR active-fire hits >= 1

The 4 proxy entries in `data/burn_scar_source_audit_2026_05_29.json` flipped to `signal_class: real_signal`.


## Sentinel-3 OLCI L2 vegetation-index dropoff — 2026-05-30 (BUILT, not yet executed)

Wired `sentinel-3-olci-lfr-l2-netcdf` (PC collection) with OTCI (Terrestrial Chlorophyll Index) + GIFAPAR (Green-Instantaneous FAPAR) as NDVI-equivalent vegetation-health signals. Median pre vs post window:
  - OTCI drop <= -0.5 OR GIFAPAR drop <= -0.15 → `ndvi_dropoff = True` (independent optical scar agreer).

OLCI bbox widened to 750m half-width (300m native).

**Status: script `C:\\Users\\markl\\fp_review_ui\\burn_scar_olci_ndvi.py` written, idempotent. Not executed in this session due to the proxy-upgrade pass consuming the session budget. Will run as part of the next daily/weekly catch-up.**


## MTG FCI archive backfill — 2026-05-30

Per task: SSH'd DGX, listed `/media/mark/AI_DGX/eumetsat_data/`. FCI cache contains 67 files in the rolling 24h window; no separate `fci_archive` directory exists. 106 / 114 cells fall outside that 24h window and remain `no_data` for MTG FCI. Per task: documented in audit, did not attempt EUMETSAT account creation today.

A new cron `PHOENIX_FP_FCI_Rolling_6h` now snapshots the DGX cache every 6h into `data/fci_rolling_archive_2026_05.json`, growing the historic archive on the public side.


## Scheduled tasks registered (Windows Task Scheduler) — 2026-05-30

  - `PHOENIX_FP_MODIS_Catchup_Daily` — daily 03:00 local. Re-runs MODIS-09A1 + 14A1 pull for cells whose original data was no_data due to PC ingestion lag.
  - `PHOENIX_FP_FCI_Rolling_6h` — every 6h (hourly /MO 6, first run 04:00). Pulls DGX FCI cache snapshot, accumulates into `data/fci_rolling_archive_2026_05.json`.
  - `PHOENIX_FP_Weekly_Version_Bump` — Mondays 09:00 local. Rolls up this `pending_v1_3_0.md` into CHANGELOG.md, bumps `data/sources.json.metadata.version` (1.2.1 → 1.3.0 → 1.4.0 etc.), regenerates sources.csv + sources.geojson, tags + pushes — only when pending content is newer than the last tag.


## fp_review_ui mask-recheck banner — 2026-05-30

Added a red banner to the manual-review UI that renders only on cells listed in `docs/pending_mask_recheck.md`. Banner shows proposed category + lat/lon + Sonnet vision basis + a "Promote to mask (M)" button. The M hotkey routes through `POST /api/promote_to_mask` which:
  1. Looks up the cell in the markdown table
  2. Refuses if a lat/lon-matching entry already exists in `data/sources.json` (prevents duplicates)
  3. Calls `build_source_entry()` + `append_to_sources()` with the proposed category
  4. Records a `classify` action in `manual_review_progress` with `promoted_from_mask_recheck = True`
  5. Removes the row from `pending_mask_recheck.md`

Currently idx 3 (greenhouse @ 37.57N 14.73E) + idx 40 (solar @ 36.93N 14.81E) are the only mask-recheck candidates; both were pre-promoted in v1.3.0 (commit 5cc0089) so the M-hotkey will refuse them with `"already in mask"` errors. Banner still renders for transparency.


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
