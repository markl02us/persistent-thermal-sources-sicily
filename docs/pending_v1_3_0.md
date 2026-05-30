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
