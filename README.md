# PHOENIX

**Sicily wildfire and pre-wildfire detection system.** Multi-sensor fusion of satellite, atmospheric, SAR, ground, and citizen data feeds — graded at the incident level with honest verification tiers — and a public dashboard at [adr-wildfire.com](https://adr-wildfire.com/).

> **Access restricted.** This repository holds the operational PHOENIX engineering and code. Access is limited to the two-person development team. The public-facing artifacts — live API, dashboard, false-positive catalog, and CC-BY 4.0 data — are at [adr-wildfire.com](https://adr-wildfire.com/) and at the [public companion repo](https://github.com/markl02us/persistent-thermal-sources-sicily).

---

## What PHOENIX is

A grassroots civic-tech wildfire-detection project covering the Sicilian land area, anchored in Alessandria della Rocca (Agrigento). PHOENIX runs continuously, fuses signals across more than 21 independent data feeds, and publishes the results openly under CC-BY 4.0 with a strict transparency-and-retraction policy.

### Why it exists

- Inland Sicilian agricultural communities face annual fire risk that threatens both livelihoods and lives. Commercial detection providers focus elsewhere; FIRMS-URT is US/CAN-only; institutional pipelines reach the ground too slowly.
- A fatal residential fire in Alessandria della Rocca in the past year (with attendant hazard from materials inside the home) motivates a parallel residential-LoRa fire-sensor track.
- Open satellite data, free APIs, and cheap edge-AI compute now make community-scale fire detection feasible without commercial or institutional gatekeeping.

### What PHOENIX is NOT

- Not a commercial product.
- Not a real-time per-pixel detector for sub-minute alerting (10-minute geostationary cadence is the floor for thermal).
- Not yet broadcasting directly to residents — gated explicitly on achieving < 5% verified-FP rate against burn-scar-confirmed ground truth.
- Not making claims about sub-pixel flame physics from microwave SAR — SAR is a scar confirmer, not a flame detector.

---

## Architecture at a glance

```
                +----- Overhead --------+   +----- Citizen --------+
                | MTG-FCI, MTG-AF-L2,   |   | Vigili del Fuoco,    |
                | MTG-LI, SEVIRI,       |   | ANSA / Italian RSS,  |
                | FIRMS VIIRS/MODIS,    |   | Reddit + Mastodon,   |
                | SLSTR, S-2, S-1 RTC,  |   | ARPA Sicilia,        |
                | NISAR, TROPOMI HCHO,  |   | OroraTech (OSINT),   |
                | OLCI smoke (planned), |   | Smoke YOLO webcams   |
                | Landsat 8/9 (planned) |   +---------+------------+
                +-----------+-----------+             |
                            |                         |
                            v                         v
                 +-----------------------------------------------+
                 | Fusion + grading layer                        |
                 |   - Joint Dozier (FCI+SLSTR+S-2)              |
                 |   - Hawkes ignition prior (nightly)           |
                 |   - wind_diff motion thermal                  |
                 |   - subpixel_v1 multi-sensor                  |
                 |   - YOLO smoke verifier                       |
                 |   - S-2 dNBR burn-scar verifier               |
                 |   - event_grades T0-T3 + race-validity + T+72h|
                 +---------------+---------------+---------------+
                                 |                |
                                 v                v
                 +-----------------------------------------------+
                 | SQLite ground_truth.sqlite (WAL)              |
                 |   external_fires, internal_fires,             |
                 |   corroboration_signals, event_grades,        |
                 |   confirmed_missed_fires, frp_quarantine,     |
                 |   fire_events + fire_event_evidence (P1.1)    |
                 +---------------+-------------------------------+
                                 |
                                 v
                 +-----------------------------------------------+
                 | Flask API + OpenAPI 3.1                       |
                 |   /api/feed_accuracy, /api/source_health,     |
                 |   /api/burn_verification, /api/wins.*,        |
                 |   /api/false_positive_zones.geojson, ...      |
                 |   PWA dashboard at adr-wildfire.com           |
                 +-----------------------------------------------+
```

Future ground tier (in development): PTZ + wide-cam nodes on Pi 5 + Hailo-8 26 TOPS with AREDN hub-and-spoke radio; home LoRa fire sensors; pole-mounted pre-ignition VOC sensors (research phase).

---

## What's in this repo

```
adr_wildfire_solution.py             Flask app + API routes + scoring + scoreboard
gunicorn_conf.py                     Production WSGI config + post_fork daemon spawn
config.yaml                          AOI bboxes, thresholds, secrets references

src/
├── data_sources/                    One module per ingestion daemon
│   ├── ororatech_public.py
│   ├── sentinel1_sar_change.py
│   ├── nisar_change.py
│   ├── slstr_frp.py
│   ├── ansa_rss.py
│   ├── arpa_air.py
│   ├── italian_news_rss.py
│   ├── social_feeds.py
│   ├── tropomi.py
│   ├── worldcover.py
│   ├── modis_viirs_sar.py
│   ├── weather_cams.py
│   ├── cems_effis_rda.py
│   ├── active_fire_l2.py
│   ├── lightning_li.py
│   └── hawkes_ignition.py
├── verifiers/                       Verification + multi-sensor fusion
│   ├── sentinel2_burnscar.py        S-2 dNBR burn-scar arbiter
│   ├── joint_dozier.py              FCI+SLSTR+S-2 fusion
│   └── smoke_yolo_daemon.py         YOLOv8 smoke webcam verifier
├── cleaners/                        Post-ingestion cleanup
│   └── detection_dedupe.py          wind_diff pixel-fragmentation merge
├── api/                             Flask middleware
│   ├── honest_precision_middleware.py
│   ├── symmetric_source_health_middleware.py
│   └── headline_lead_middleware.py
├── postmortem/                      Confirmed-miss registry + watcher
│   └── confirmed_misses.py
├── events/                          Reserved for P1.2 event-level wiring
├── land_mask.py                     Sicily land + coastline + Etna + industrial masks
└── ground_truth.py                  Ground-truth schema + helpers

scripts/
├── grade_events.py                  Event-level T0-T3 grading + race-validity + T+72h reconcile
├── deploy.sh                        Atomic production deploy
├── rollback.sh                      Rollback from timestamped backup
├── wire_middleware.py               Idempotent app-level middleware wiring
└── wire_dedupe_daemon.py            Idempotent gunicorn-level daemon wiring

migrations/
├── 002_frp_sanity_gate.sql          FRP > 10 GW clamp + quarantine table
└── 003_fire_events_schema.sql       fire_events + fire_event_evidence + audit + counts

docs/
├── methodology.md                  How the FP catalog was built
├── data-schema.md                  Schema of data/sources.json
├── win-classification.md           How PHOENIX counts wins (v2 permissive, 2026-05-26)
├── PHOENIX_EXSUM_EN.md             External executive summary (English)
├── PHOENIX_EXSUM_IT.md             External executive summary (Italian)
├── PHOENIX_INGV_Pack_v1.2_Updates_EN.md
├── PHOENIX_INGV_Pack_v1.2_Updates_IT.md
└── (additional published methodology notes)
```

---

## Live system

| Resource | URL |
|---|---|
| Public dashboard (PWA) | https://adr-wildfire.com/ |
| OpenAPI 3.1 spec | https://adr-wildfire.com/api/openapi.json |
| Per-source accuracy | https://adr-wildfire.com/api/feed_accuracy |
| Per-source health | https://adr-wildfire.com/api/source_health |
| Burn-scar verification | https://adr-wildfire.com/api/burn_verification |
| Win list (CSV / RSS / iCal) | /api/wins.csv, /wins.rss, /wins.ics |
| False-positive catalog | https://adr-wildfire.com/falsi-positivi |
| Methodology (Italian) | https://adr-wildfire.com/come-funziona |
| Confirmed-wins page | https://adr-wildfire.com/wins.html |
| Per-feed accuracy page | https://adr-wildfire.com/accuracy.html |

Companion public assets:
| Resource | URL |
|---|---|
| False-positive catalog repo (public, CC-BY 4.0) | https://github.com/markl02us/persistent-thermal-sources-sicily |
| Catalog Zenodo DOI | 10.5281/zenodo.20369891 |

---

## Operating principles (HARD rules — see `/CONTRIBUTING.md`)

1. **Truth discipline before sources.** No new sensor or feed integration ships until burn verification works against real ground truth, FRP sanity gates are in place, dedupe is live, and the published precision/unknown-rate metrics are honest. Enhancements to already-shipped sources are fine; new sensors are not.
2. **Events, not detections.** Score and report at the incident level. Pixel-level scoring is a developer convenience; the public scoreboard is always incident-level with T0-T3 tier + race-validity flag + T+72h reconciliation outcome.
3. **Two-clock honesty.** Headline lead time is always `lead_min_vs_sensed` (against comparator sensor-acquisition time). `lead_min_vs_reported` (against comparator feed-delivery time) is preserved as a context column but never headlines; it is structurally inflated against FIRMS-Sicily because FIRMS-URT is US/CAN-only.
4. **No farmer broadcast until verified-FP rate < 5%.** PWA push + RSS/CAP feed without PII is acceptable; direct broadcast to residents via SMS / WhatsApp / Telegram is paused until the system is measurably trustworthy against burn-scar-confirmed ground truth.
5. **Public change log + public retraction.** Every algorithm / threshold / mask change is published on the public change log with rationale and a +7-day outcome assessment. When we discover we shipped wrong data, we publish a retraction within 24 hours and preserve the corrupted data in a quarantine table for forensics.
6. **Confirmed misses are gold.** Every locally-confirmed fire that PHOENIX failed to detect is registered in `confirmed_missed_fires`, auto-backtested against every algorithm, and becomes a permanent regression test. The inaugural entry is the ADR 2026-05-24 fire at (37.562278°N, 13.440250°E).

---

## Repository access policy

This repo is **private**. Access is currently limited to:

- Repository owner (technical lead)
- Gaetano Zambito (Sicilian representative, INGV-facing point of contact) — see CODEOWNERS

External contributions and external mirrors are not accepted. The project's external surfaces are:
- The public dashboard at adr-wildfire.com (CC-BY 4.0 data)
- The public companion repo `persistent-thermal-sources-sicily` (false-positive catalog only, CC-BY 4.0 + MIT)
- The public OpenAPI at /api/openapi.json
- The public Zenodo deposit (DOI 10.5281/zenodo.20369891)

Any technical question from an outside party — academic researcher, journalist, INGV, vigili del fuoco — should be routed through `adrwildfi@gmail.com` (project inbox) or to Gaetano Zambito directly. The full architecture, daemons, and operational details are described in the **INGV partnership pack** (`docs/PHOENIX_INGV_Pack_EN.md` and `docs/PHOENIX_INGV_Pack_IT.md`) which is the canonical external-facing description of the system.

---

## License

- **Code:** MIT
- **Data, configuration, and false-positive catalog:** CC-BY 4.0
- **Sensor model weights (where redistributable):** respective upstream licenses preserved
- **Embedded third-party data products** (Copernicus, FIRMS, EUMETSAT, NASA Earthdata, Microsoft Planetary Computer assets): governed by their respective upstream licenses. We comply with attribution requirements for each.

See `LICENSE-CODE` and `LICENSE-DATA`.

---

## Contact

| Role | Contact |
|---|---|
| Sicilian representative + INGV-facing PoC | Gaetano Zambito — folderdj@gmail.com — +39 366 545 0598 |
| Project inbox (please cc) | adrwildfi@gmail.com |
| Live system | https://adr-wildfire.com/ |
| Public methodology | https://adr-wildfire.com/come-funziona |

---

*This README is a private-repo internal description. The canonical external description of PHOENIX is the INGV partnership pack in `docs/`.*
