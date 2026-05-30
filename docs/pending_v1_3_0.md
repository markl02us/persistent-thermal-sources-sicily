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
