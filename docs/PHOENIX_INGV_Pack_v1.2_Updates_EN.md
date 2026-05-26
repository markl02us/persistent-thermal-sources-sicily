# PHOENIX–INGV Partnership Pack v1.2 — Changes from v1.1

**Prepared:** 2026-05-26
**Supersedes:** v1.1 of 2026-05-25
**Point of contact:** Gaetano Zambito — folderdj@gmail.com — +39 366 545 0598
**Live system:** https://adr-wildfire.com/
**Open-source code:** https://github.com/markl02us/persistent-thermal-sources-sicily

---

## TL;DR

Between v1.1 (25 May, evening) and v1.2 (26 May), seven categories of work shipped that
materially change what the pack claims. Highest-impact changes:

1. **Two previously-disclosed bugs are now RESOLVED in production**
   (Sentinel-2 burn-scar verifier; subpixel_v1_alpha FRP overflow).
2. **The grader is now v2.1** with race-strict / race-marginal / first-vs-VVF distinction,
   biome-aware dNBR thresholds, multi-stage reconcile (T+72h → T+14d → T+45d), comparator-panel
   JSON, below-floor flag, and 14 new `event_grades` columns.
3. **Reproducibility stack shipped end-to-end**: daily public snapshots, standalone reproducer
   (`scripts/regrade.py`), permutation null-distribution bootstrap (`/api/null_bootstrap`),
   Wilson 95% CI surfaced everywhere.
4. **Public page rebuild**: 7-cell system-profile strip, per-sub-detector precision chips,
   refuted-events open by default, bilingual EN/IT toggle, ARIA labels, Wong-2011 colour-blind palette.
5. **The "3 race-valid wins" headline is honestly RETIRED**: under the strict bar we have 0 wins
   in 30 days (bootstrap p = 1.00 vs random); under the looser bar, 2 PHOENIX-first events in the
   last 7 days, both shown with explicit methodology asterisks.

INGV can audit any number on `/wins.html` end-to-end from raw FIRMS / EUMETSAT / VVF pulls
without contacting us. That was not true on 25 May; it is true on 26 May.

---

## Detailed changes

### Bugs resolved

| Item | v1.1 status | v1.2 status |
|---|---|---|
| Sentinel-2 dNBR burn-scar verifier | HTTP 400 on every call (82 null returns) | **Fixed end-to-end.** Three stacked bugs: STAC datetime format, MPC SAS signing, B8/B12 shape mismatch. GitHub commit `eadb2ed`. Smoke-tested on April 26 ADR detection: pre_NBR=0.3072, post_NBR=0.3423, dNBR=−0.0351, verified_burn=False (correct). First confirmed burn (via SAR fallback): det_id 16802. |
| subpixel_v1_alpha FRP overflow | Outliers up to 3.9 PW (physically impossible) | **Fixed.** Max FRP now 9.09 MW, mean 2.73 MW, n=5,524, zero outliers above 10 GW. |

### Grader v2.1 — new schema and methodology

- **`race_strict`**: PHX lead > 0 AND lead < 50% of comparator's revisit AND comparator_class='satellite_sensor' AND ≥ 1 capable comparator AND not below_floor.
- **`race_valid` (loose)**: PHX lead within 100% of revisit. When 50% < ratio ≤ 100%, surfaced on `/wins.html` as "race-marginal*" with footnote.
- **`comparator_class`** ∈ {`satellite_sensor`, `human_dispatch`, `social`}. VVF and news are `human_dispatch` — they corroborate truth (T2) but they don't race satellites. PHOENIX-first vs VVF surfaced as "First vs VVF*" (different visual + footnote).
- **`comparator_panel`** (JSON): per-event list of every capable comparator with lead, revisit, below_floor flag.
- **`worst_capable_lead_min`**: race_strict uses the WORST lead among capable comparators, not the best. Closes the "comparator-of-convenience" cherry-pick attack.
- **`below_comparator_floor`**: 1 if event FRP is below the physical floor of every capable comparator. Such events are excluded from the FP denominator — a comparator literally couldn't have caught them.
- **`biome_class` + `dnbr_threshold_biome`**: ESA WorldCover-derived. Forest = 0.27 (Key & Benson 2006), shrubland/macchia = 0.18 (Fernández-Manso 2016, De Santis & Chuvieco 2009, Mallinis 2018), grassland/crop = 0.12. Replaces the v1 universal 0.27 which is forest-calibrated and wrong for Mediterranean garrigue.
- **`wui_built_pct` + `wui_class`** ∈ {U Urban, I WUI Interface, W Wildland, N Other}: operational context for dispatcher use.
- **`phoenix_had_coverage`**: for external-led events (PHOENIX missed), 1 if PHOENIX had a detector running during the comparator's acquisition. Distinguishes algorithm-miss from feed-miss.
- **`refute_strength`** ∈ {strong, weak, unverifiable}: strong = cloud-free dNBR < biome threshold; weak = dNBR ambiguous; unverifiable = no S-2 scene available. Only `strong` counts against precision.
- **Multi-stage reconcile**: `t72h_outcome` (initial), `t14d_outcome` (extended search), `t45d_outcome` (final scar disposition). Each stage can upgrade or downgrade prior outcomes. Cloud-occluded events flagged `no_signal_unverifiable`, NOT refuted.

### Reproducibility stack

| Component | URL | What it does |
|---|---|---|
| Daily public snapshots | `https://adr-wildfire.com/data/snapshots/YYYY-MM-DD/` | Raw `internal_fires.csv` + `external_fires.csv` + `corroboration_signals.csv` + `event_grades.csv` + `SHA256SUMS` + `README.md` |
| Standalone reproducer | `scripts/regrade.py` (on GitHub) | Takes the CSV inputs and regenerates `event_grades.csv` using the same code path as production. Verified zero-mismatch on 2,172 events. |
| Null-distribution bootstrap | `https://adr-wildfire.com/api/null_bootstrap` | 200-replicate permutation test on race_strict count. Current result: observed=0, null mean=12.7, p-value=1.00. **We publish our own falsification.** |
| Wilson 95% CI | Surfaced on `/wins.html` precision band + per-sub-detector chips | n < 30 → intervals only, no point estimates |
| Provenance per row | 🛰️ FIRMS map + 🌍 Copernicus Browser links | Click-through-to-source on every event row |

### Honest win-count restatement

- **Race-strict wins (30 days):** 0. Bootstrap p-value 1.00 vs null. PHOENIX is currently NOT statistically distinguishable from chance at the strict bar.
- **PHOENIX-first events (7 days, looser race-valid):** 2.
  - 2026-05-25 `wind_diff` +9.4 min vs Vigili del Fuoco at (36.99°N, 14.37°E). T2 ("First vs VVF*").
  - 2026-05-24 `wind_diff` +9.1 min vs EUMETSAT MTG-AF-L2. T1 ("Race-marginal*", lead = 91% of revisit).
- **Co-detected (≥ T1, PHOENIX co-detected with comparator):** 16.
- **Caught by others, PHOENIX missed:** 195.
- **Sole-reporter, awaiting T+72h reconcile:** ~1,096.
- **Refuted at T+72h:** 623.
- **Resolved-set precision:** 1.74% (Wilson 95% CI: 0.97%–3.08%, n=634).

These are all honest as-of-2026-05-26 numbers. v1.1 of the pack reported "3 race-valid wins" — that figure used a definition that has since been retired in favour of the harder bar above.

### Daemon count

v1.1 said "21 daemons running". v1.2 runs:

- 21 polling daemons (FIRMS, EUMETSAT, Sentinel-1, Sentinel-2 verifier, SAR change, NISAR, TROPOMI, OroraTech, ANSA, Italian news, Reddit, Mastodon, joint Dozier, Hawkes, etc.)
- 3 reproducibility daemons (5-min event-grader, daily snapshot, nightly null-bootstrap)
- + multi-stage reconciler running at 6h intervals

Total active background daemons: 25+.

### New API endpoints

- `/api/event_grades?days=N[&tier=Tx][&led=phoenix|external]` — full graded event list
- `/api/event_grades.csv` — same as CSV
- `/api/null_bootstrap` — permutation null distribution
- `/data/snapshots/` — index of available daily snapshots
- `/data/snapshots/<date>/` — listing for a date
- `/data/snapshots/<date>/<file>` — raw CSV / README / SHA256SUMS

### `event_grades` schema (v2.1 — full DDL)

The columns added in v2.1 over the v1 schema printed in Annex D of the v1.1 pack:

`comparator_class TEXT, comparator_panel TEXT (JSON), capable_comparator_count INTEGER,
worst_capable_lead_min REAL, race_strict INTEGER, below_comparator_floor INTEGER,
biome_class TEXT, dnbr_threshold_biome REAL, phoenix_had_coverage INTEGER,
refute_strength TEXT, t14d_outcome TEXT, t14d_outcome_evidence TEXT,
t14d_reconciled_at TEXT, t45d_outcome TEXT, t45d_outcome_evidence TEXT,
t45d_reconciled_at TEXT, wui_built_pct REAL, wui_class TEXT`

The Sicily-wide negative median lead (−98.9 min) **is now scored only on cells where
`phoenix_had_coverage = 1`** — i.e., where PHOENIX had at least one capable detector with
a valid acquisition in the comparison window. Cells where PHOENIX could not have seen
the fire are excluded from the loss median.

### Public page (`/wins.html`) rebuild

- **System-profile strip** at the top showing all seven outcome categories side-by-side at equal visual weight.
- **Precision band** with Wilson 95% CI displayed: "Resolved-set precision: 1.74% [0.97%–3.08%, n=634]"
  and the null-distribution bootstrap line appended.
- **Per-PHOENIX-sub-detector chips** with confirmed / refuted / pending / unverifiable / below-floor counts and per-detector Wilson 95% CI. Same scoreboard treatment we apply to comparators applied to ourselves.
- **Authoritative-first section** moved to the top: union of every fire that VVF / FIRMS / EUMETSAT / SLSTR
  reported, with PHOENIX's contribution per row (co-detected / missed-algorithm-gap / missed-no-coverage).
- **Refuted section open by default**, no longer hidden behind a `<details>` drawer.
- **Per-row provenance**: 🛰️ FIRMS map and 🌍 Copernicus Browser links on every event.
- **Bilingual EN/IT toggle** (top right) with i18n dictionary covering nav, intro, tier legend, section headers.
- **ARIA labels** on tier badges, race-badges, section roles.
- **Wong-2011 colour-blind-safe palette**: T0 grey, T1 blue, T2 teal, T3 vermilion (was T3 yellow which was too close to T1 blue under deuteranopia).

### What remains the same / still honest

- Two-person volunteer team, no commercial intent, no funding ask. Unchanged.
- Personal motivation: 2025 fatal residential fire in Alessandria della Rocca. Unchanged.
- Four data exchanges requested from INGV: Etna thermal catalog (priority 1), seismic-station fire-weather context, ash-plume forecasts, historical fire–volcano interaction atlas. Unchanged.
- CC-BY 4.0 data + MIT code; no exclusivity; INGV attribution on every alert and publication using INGV data. Unchanged.
- The 15 km Etna exclusion mask is still in place; it is the unblocked-by-INGV-data limit we hope this collaboration will lift.

### What v1.2 still does NOT claim

- Race-strict skill above chance. (Bootstrap p = 1.00; we publish that.)
- A measured verified-FP rate below 5%. (Disclosed gating threshold for farmer broadcasts; not yet met.)
- A complete Sentinel-2 burn-scar archive for every detection. (Many recent fires have no post-fire S-2 pass yet; the daemon will roll up as passes land.)
- WUI proximity / road / hydrant operational fields. (Partial: WUI class from WorldCover for the ~64 cells covered; road and hydrant from OSM not yet shipped.)

---

## What INGV should look at on `/wins.html` and `/api/...` to verify any v1.2 claim

| Claim | How to verify |
|---|---|
| Race-strict = 0 | `curl https://adr-wildfire.com/api/null_bootstrap` — observed.race_strict |
| Daemon count | `gunicorn_conf.py` on GitHub commit `eadb2ed` (or later) |
| Biome dNBR thresholds | `scripts/grade_events.py` constant `BIOME_DNBR` |
| Verifier working | `curl https://adr-wildfire.com/api/burn_verification` — look for non-null dNBR or `verified_via_sar=True` rows |
| FRP overflow resolved | `SELECT MAX(frp_mw) FROM internal_fires WHERE source='subpixel_v1_alpha'` — should be < 10 |
| Reproducibility | Download `https://adr-wildfire.com/data/snapshots/2026-05-26/`, run `scripts/regrade.py`, diff against published `event_grades.csv` |

---

*This Updates document is part of the PHOENIX–INGV Partnership Pack v1.2. It complements the
EXSUM v1.2 and the full Technical Pack v1.2 (regenerating via GitHub Actions; download
`PHOENIX_INGV_Pack_EN.pdf` from the latest workflow artifact). For the canonical methodology
and reasoning behind every choice, see the Technical Pack v1.2.*
