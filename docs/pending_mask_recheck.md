# Pending FP-mask re-review candidates

This file lists cells currently masked in `data/sources.json` (action=`classify` in
the manual-review session) where downstream burn-scar evidence later suggests the
cell may actually be a transient burn, not a persistent FP. These are flagged for
human re-review only; `data/sources.json` is NEVER auto-edited from this file.

The trigger rule (multi-sensor burn-scar pass) is:
  - `has_scar_fused = True` AND
  - `still_active_48h = False` AND
  - `n_sensors_agree_scar >= 2`

A single-sensor scar (e.g. S2 only) over a water-body classification is NOT
flagged here because dNBR is well-known to misbehave over water (sun glint,
mixed-pixel reflectance from algae, suspended sediment, etc.).

## Multi-sensor burn-scar pass — 2026-05-29

- Cells classified into the mask: 17 (all `water_body`)
- Cells where multi-sensor fusion produced `has_scar_fused = True`: 17
- Cells where at least 2 independent sensors agreed on scar (gate threshold): 0

**No mask-recheck candidates parked this pass.** Every `classify=water_body` cell
showed a fused-scar signal driven by a SINGLE sensor (S2 dNBR), which is the
well-known water-body NBR artifact. Without a second independent sensor
confirming a scar, none of the 17 water-body classifications are flagged for
re-review.

Note: Sentinel-1 RTC backscatter delta was usable on all 17 water cells and
showed `|delta_vv_db| < 1.5 dB` in every case (no SAR scar) — consistent with
water surfaces, not burned vegetation. The mask is correct.

## Wave v1_3_0_unsure_resolution_full_roster (2026-05-30T01:40:42.591474+00:00)

| idx | lat | lon | proposed | basis |
|---:|---:|---:|---|---|
| 3 | 37.5700 | 14.7300 | greenhouse_complex | Sonnet vision (conf 0.92): The false-color composite shows bright cyan rectangular structures characteristic of plastic greenhouse coverings. The top-right corn |
| 40 | 36.9300 | 14.8100 | solar_farm | Sonnet vision (conf 0.92): The Sentinel-2 false-color image shows a distinct dark grey/black patch in the upper-right quadrant, characteristic of solar PV panel |
