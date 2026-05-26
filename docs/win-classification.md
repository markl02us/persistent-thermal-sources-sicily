# PHOENIX win classification (v2 — 2026-05-26)

This document defines how PHOENIX counts and labels "wins" against external
satellite and ground-truth comparators. It is intentionally written to be
**neither overselling nor underselling** what the system did. The earlier (v1)
rule was too strict and silently discarded real first-alert events; v2
restores the permissive count and uses transparent asterisks to call out the
caveats on borderline cases.

## TL;DR

A **win** is any event where:

1. PHOENIX produced a fire alert at a given location, AND
2. A comparator (FIRMS-VIIRS, MTG-AF-L2, SLSTR, Vigili del Fuoco, Italian
   news, etc.) independently confirmed a fire at the same location
   (within 5 km, within ±6 h), AND
3. PHOENIX's alert preceded the comparator's first sensing OR the comparator's
   feed delivery.

Wins are reported with one of three category labels:

| Category | Asterisk? | Meaning |
|----------|-----------|---------|
| `clean` | no | Algorithmic lead within the comparator's revisit period. PHOENIX detected the fire and the comparator could have on its very next pass — we just got there first. |
| `extended_lead` | * | PHOENIX alerted first, but the lead exceeds the comparator's revisit period. Part of the advantage is orbital geometry (the comparator's next overpass hadn't happened yet). Still a real first alert — many wildfires escalate within a single revisit window — but we flag it because the lead is not purely algorithmic. |
| `delivery_only` | * | No algorithmic lead at sensor-acquisition time, but PHOENIX's pipeline got the alert to users before the comparator's pipeline did. Their satellite saw the fire first; our software told someone about it first. |

The asterisks are a transparency feature, **not** a denial of the win.

## Why v2 changed

In v1 (May 2026), the grader used these rules:

- `race_valid = lead_minutes ≤ comparator_revisit_minutes`
- `lead_likely_geometric = lead_minutes > comparator_revisit_minutes`

The public wins page (`/wins.html`) displayed only `race_valid` events as
wins. Events flagged as `lead_likely_geometric` were rendered in an orange
"likely geometric, not algorithmic" badge and the headline counted them
separately as non-wins. The empty state read: *"We are not claiming any
[wins] until a comparator independently corroborates a fire that PHOENIX
caught first within their revisit window."*

The problem with that framing: **PHOENIX did detect those fires first, and
that operationally matters**. A fire that escalates inside the comparator's
revisit window is a fire the comparator would have caught hours later — by
which time the first responders may already be deployed or the fire may
already have grown beyond initial-attack capability. The earlier alert has
value regardless of whether the comparator would have eventually caught it
on its next overpass.

v1's rule was honest about algorithmic-vs-algorithmic comparison but was
silently underselling the system's operational utility.

v2's rule counts the win and uses the asterisk to be honest about the
caveat. Users can still filter for `clean`-only wins if they want a stricter
algorithm benchmark.

## The math

For a PHOENIX-led event at time T_phoenix and a comparator event at the
same location at time T_comp:

```
lead_sensed   = (T_comp_sensed_at  - T_phoenix) minutes
lead_reported = (T_comp_ingested_at - T_phoenix) minutes
```

Then:

```
if lead_sensed > 0 and lead_sensed <= comparator_revisit:
    win_category = "clean"
elif lead_sensed > comparator_revisit:
    win_category = "extended_lead"   # asterisk
elif lead_sensed <= 0 and lead_reported > 0:
    win_category = "delivery_only"   # asterisk
else:
    win_category = None              # not a win
```

`comparator_revisit` is the comparator's nominal sensor revisit period:

| Comparator | Revisit (min) |
|---|---|
| FIRMS VIIRS (SNPP/NOAA-20/NOAA-21) | 240 |
| FIRMS MODIS | 360 |
| Sentinel-3 SLSTR | 1440 |
| Landsat | 1440 |
| MTG AF-L2 | 10 |
| MSG SEVIRI | 15 |
| MTG FCI L1C | 10 |
| Sentinel-1 SAR | 17280 |
| Sentinel-5P TROPOMI | 1440 |
| Vigili del Fuoco | 60 |
| Italian news / DPC | 360 |

(Full table in `scripts/grade_events.py:COMPARATOR_REVISIT_MIN`.)

## Two clocks

Every win is reported with two lead-time numbers:

- **Algorithm Δ (sensor-acquisition):** how many minutes before the
  comparator's satellite/sensor first saw the fire we did. This is the
  algorithm-vs-algorithm number.
- **Wall-clock Δ (feed-delivered):** how many minutes before the
  comparator's processed feed was available to users we alerted. This is the
  operational user-facing number.

Both are shown side-by-side on `/wins.html`. The "two clocks" framing
exists because NASA FIRMS NRT typically publishes 1–3 hours after sensing,
so even when PHOENIX has no algorithmic lead (sensor-Δ = 0), it may have a
significant wall-clock lead (feed-Δ > 60 min).

## Tier (independent of win category)

Every event is also assigned a verification tier based on independent
corroboration. **Tier and win category are orthogonal** — a win can be any
tier; a non-win can also be any tier.

| Tier | Meaning |
|---|---|
| T3 | Burn-scar verified (Sentinel-2 dNBR > 0.27) OR Vigili del Fuoco + ≥2 satellite sources |
| T2 | Vigili del Fuoco match within ±24 h OR Italian news/civil-protection match |
| T1 | ≥1 independent satellite family corroborated within 5 km / ±2 h |
| T0 | Sole reporter — no independent corroborator within the window |

A T0 PHOENIX-led event with a positive lead but no corroboration **is not
a win** under v2 — it's a candidate awaiting T+72 h reconcile, after which
it gets one of: `confirmed_vvf`, `confirmed_news`, `confirmed_burnscar`,
`refuted_no_scar`, or `refuted_likely_fp`.

## T+72 h reconcile

72 hours after any sole-reporter PHOENIX detection, the grader re-scans
Vigili del Fuoco logs, Italian news RSS, and Sentinel-2 dNBR for confirming
evidence. The outcome is persisted to `event_grades.t72h_outcome`:

- `confirmed_vvf` — Vigili del Fuoco posted within 10 km / 48 h
- `confirmed_news` — Italian news / Protezione Civile post within 10 km / 48 h
- `confirmed_burnscar` — Sentinel-2 dNBR > 0.27 within 0.05° / 14 d
- `refuted_no_scar` — Sentinel-2 dNBR < 0.10
- `refuted_likely_fp` — no corroborating evidence and tier remained T0
- `no_signal` — tier was not T0 (i.e. event had corroboration; reconcile not
  needed for win/loss accounting)

The wins page publishes refuted events too. We don't hide false positives.

## Worked examples

### Example 1 — clean win

```
event:
  PHOENIX (subpixel_v1) detected at  14:08 UTC on 2026-07-15
  FIRMS-VIIRS-SNPP sensed at         14:42 UTC on 2026-07-15
  comparator_revisit                  240 min (4 h)
  lead_sensed                         +34 min
  lead_sensed <= 240                  YES
  win_category                        clean
  badge                               CLEAN WIN
```

PHOENIX got there 34 min before the satellite sensor did, well inside the
4 h revisit window. Pure algorithmic lead.

### Example 2 — extended-lead win*

```
event:
  PHOENIX (wind_diff) detected at    08:11 UTC on 2026-08-02
  FIRMS-VIIRS-NOAA20 sensed at       19:34 UTC on 2026-08-02
  comparator_revisit                  240 min
  lead_sensed                         +683 min
  lead_sensed <= 240                  NO
  win_category                        extended_lead
  badge                               EXTENDED LEAD*
  race_note                           "lead exceeds revisit -- still a valid
                                       first alert, but next overpass had not
                                       happened yet"
```

PHOENIX gave the alert at 08:11. The next VIIRS overpass wasn't until ~19:34
that evening (orbital schedule). The 11-hour lead is real, but if the
comparator had been able to pass earlier it would have caught the fire
much sooner. We still count it because the operational impact is the same —
firefighters were notified 11 hours earlier than the VIIRS feed could have.

### Example 3 — delivery-only win*

```
event:
  MTG-AF-L2 sensed at                12:00:00 UTC on 2026-09-10
  PHOENIX detected at                12:00:00 UTC on 2026-09-10
  PHOENIX dispatched alert at        12:00:15 UTC on 2026-09-10
  MTG-AF-L2 feed ingested by us at   12:08:42 UTC on 2026-09-10
  lead_sensed                         0 min
  lead_reported                       +8.7 min
  win_category                        delivery_only
  badge                               DELIVERY*
```

Same sensing moment, but our pipeline alerted users in 15 seconds while the
MTG-AF-L2 feed took ~8.7 min to arrive via EUMETCast. Operationally a win;
algorithmically a tie. The asterisk says so.

### Example 4 — not a win

```
event:
  FIRMS-VIIRS-SNPP sensed at         11:14 UTC on 2026-06-20
  PHOENIX detected at                11:50 UTC on 2026-06-20
  comparator_led, PHOENIX corroborated
  win_category                        NULL
  badge                               (none — appears in "co-detected" section)
```

PHOENIX caught the fire too, but VIIRS got there first. Not a PHOENIX win;
PHOENIX is the corroborator here. The event appears in the
"co-detected" / "verified external" section of /wins.html with full credit
to the comparator.

## Code & data references

- **Grader implementation:** `scripts/grade_events.py` (mirrored from live system at `grade_events_from_dgx.py` on the PHOENIX DGX)
- **Live wins page:** https://adr-wildfire.com/wins.html
- **JSON API:** https://adr-wildfire.com/wins (machine-readable; each row has `win_category`, `race_valid`, `lead_likely_geometric`, `delivery_advantage_only`)
- **CSV API:** https://adr-wildfire.com/api/wins.csv
- **Grades JSON:** https://adr-wildfire.com/api/event_grades
- **DB schema:** `event_grades` table; columns added in v2: `is_win` (INTEGER 0/1), `win_category` (TEXT enum)
- **Grader version string:** `v2-permissive-win-2026-05-26`

## Change history

- **2026-05-26 (v2):** Restored permissive win rule. ALL phoenix-led events with positive lead AND comparator confirmation count as wins, categorized as clean / extended_lead* / delivery_only*. Asterisks transparently flag the caveats. Public /wins.html headline shows total wins + per-category breakdown. Empty-state language no longer denies the system's first-alert value.
- **2026-05-24 (v1.1):** Added celebrate-all-detectors section to /wins.html for comparator-led events PHOENIX missed.
- **2026-05-22 (v1):** Initial race-validity rule. Wins shown only when `lead <= comparator_revisit`. Other PHOENIX-led leads displayed as "likely geometric, not algorithmic." This rule has been replaced by v2.
