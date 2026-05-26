# PHOENIX — Sicily Wildfire Detection System
## Partnership Briefing for INGV (Istituto Nazionale di Geofisica e Vulcanologia)

**Document version:** 1.2 (supersedes 1.1 of 2026-05-25)
**Prepared:** 2026-05-26 (post-audit revision)
**Point of contact:** Gaetano Zambito — folderdj@gmail.com — +39 366 545 0598
**Project inbox:** adrwildfi@gmail.com
**Prepared by:** ADR PHOENIX team (Alessandria della Rocca, Sicily)
**License:** CC-BY 4.0 (data) / MIT (code) — open for academic re-use and citation
**Live system:** https://adr-wildfire.com/
**Open-source code:** https://github.com/markl02us/persistent-thermal-sources-sicily
**DOI for false-positive catalog:** 10.5281/zenodo.20369891

---

# EXECUTIVE SUMMARY

## Bottom Line Up Front

PHOENIX is a multi-sensor wildfire and pre-wildfire detection system covering Sicily, run by a two-person grassroots volunteer team out of Alessandria della Rocca (Agrigento province). We are requesting a working collaboration with INGV — not funding, not exclusivity, not a commercial partnership — built around **four specific data exchanges** that neither of us can produce alone and that materially improve the quality of wildfire warning for Sicilian farmers and rural residents.

**What we want from INGV:**

1. **Etna thermal baseline data** — INGV's continuous thermal anomaly catalog from Etna's summit and flank vents. Today PHOENIX simply masks a 15 km radius around the Etna summit as an exclusion zone because we cannot distinguish a real wildfire on Etna's flanks (which do occur in pine and broom vegetation) from background volcanic thermal noise. INGV's existing surveillance distinguishes those signals routinely. A live or near-live feed (even a daily JSON dump of "active vent locations + intensity classes") would unlock real fire detection on Etna for the first time.

2. **Seismic-station-co-located fire-weather context** — INGV's seismic network includes many stations in remote pyroclastic / fire-prone terrain. Local meteorological micro-conditions at those stations (where instrumented) would help our ignition-prior model do something we cannot do from gridded weather alone.

3. **Tephra and ash-plume forecasts** — INGV's volcanic ash dispersion forecasts directly affect our smoke-detection logic. A volcanic ash plume looks like wildfire smoke to a YOLO model trained on wildfire smoke. Pre-positioning a "do not classify as fire smoke if ash forecast covers this area" prior would eliminate a significant false-positive mode.

4. **Historical fire-volcano interaction atlas** — INGV's institutional record of fires triggered by lava flows, pyroclastic events, and ash-deposit-flammability changes is unique. We have no equivalent. A read-only dump (even of 1980-2020) anchors our seasonal priors and our event-grading rules.

**What we offer INGV in return:**

- Free, open, real-time access to the entire PHOENIX data stream (REST/STAC/RSS/CSV/GeoJSON) at adr-wildfire.com — already CC-BY 4.0 licensed.
- Co-authored peer-reviewed publication when joint methods reach maturity. INGV-Sicily and INGV-Catania researchers welcome to lead or co-lead.
- Free use of our DGX-class compute for joint analyses (currently 18+ live polling daemons, NISAR L-band SAR pipeline with NASA Earthdata authentication, MTG-FCI / MTG-LI ingestion, joint Dozier multi-satellite fusion, YOLO smoke verification, Hawkes ignition forecasting).
- Citable false-positive catalog (Zenodo DOI 10.5281/zenodo.20369891) of persistent thermal sources in Sicily — already useful to anyone working in Sicilian remote sensing.
- **Full reproducibility (shipped 2026-05-26)**: daily public snapshots at `/data/snapshots/YYYY-MM-DD/` (raw inputs + published grades + SHA-256 sums), a standalone `scripts/regrade.py` reproducer (verified zero-mismatch against published grades on 2,172 events), a null-distribution bootstrap at `/api/null_bootstrap` (we publish our own falsification — current p-value = 1.00 vs random), and Wilson 95% CI on every precision claim. INGV can audit any number on `/wins.html` end-to-end from raw FIRMS / EUMETSAT / VVF pulls without contacting us.

**Who we are, honestly:**

PHOENIX is operated by a small volunteer team, all with full-time day jobs. The Sicilian representative and point of contact for INGV is **Gaetano Zambito** — based in Milan during the week, returning to Alessandria della Rocca for a few days each month, currently completing his university degree. Project correspondence is welcomed at his direct email (folderdj@gmail.com), at his Italian mobile (+39 366 545 0598), and at the project group inbox (adrwildfi@gmail.com). The technical engineering side of the project — satellite-data ingestion, AI/ML infrastructure, and DGX-class compute operations — is led by a separate ADR-affiliated technical contributor; that role is intentionally not named publicly here. We are not a startup, we have no commercial intent, no fundraising, no exclusivity demands. Costs (compute, internet, ground-sensor hardware) are borne by ADR-affiliated team members personally as a contribution to the community.

**Why now:**

In the past year, a fatal residential fire in Alessandria della Rocca took the life of one resident and risked further harm from hazardous materials inside the house. Reference: https://www.youtube.com/watch?v=kgDIhfthQJM. Alessandria della Rocca is a small, almost-entirely agricultural community in inland Sicily where annual wildfire risk threatens both lives and livelihoods. Our motivation is preventing repeat tragedies — not academic publication, not commercial detection-as-a-service, not building a brand. We are publishing data publicly and openly so that everyone in the region benefits, including INGV's researchers if useful to them.

**What we deliver:**

- A live PHOENIX system already running 24/7 with 18+ active satellite-data and citizen-data daemons, covering Sicily-wide and an Alessandria della Rocca + Agrigento AOI specifically.
- A roadmap covering ground sensors (PTZ camera + wide camera + LoRa node hubs on Pi 5 + Hailo-8 26 TOPS hardware), home fire sensors over LoRa for residential early warning (motivated by the fatal fire above), and an experimental pole-mounted pre-ignition VOC sensor.
- A 12 / 24 / 36-month delivery schedule that we will hit even with the two-person constraint, because we have already delivered the core system.
- A public change log on adr-wildfire.com for every adjustment to algorithms, thresholds, or masks — with the rationale published alongside the change. Public retractions when we discover we shipped data that turned out to be wrong.

**Why this proposal — not a contract or RFP:**

We are operating as a grassroots civic-tech project. INGV is a serious scientific institution. We approach you with the conviction that even at our limited scale we have **already built and made public** a substantial fire-detection capability for Sicily that is honest about its limits and that any Sicily-focused researcher might find useful. We hope you find it interesting enough to share four specific kinds of data with us. If yes: tell us what you need from our side. If no: the system stays live and useful for everyone regardless.

---



<p align="center">
  <img src="../assets/maps/sicily_aoi_overview.png" alt="PHOENIX Sicily Operating Area — AOIs (sicily_full, agrigento), key cities, volcanic centers (Etna 15 km exclusion mask), and the 2026-05-24 ADR confirmed-miss fire." style="max-width:100%;"/>
</p>
<p align="center"><em>Figure: PHOENIX Sicily Operating Area — AOIs (sicily_full, agrigento), key cities, volcanic centers (Etna 15 km exclusion mask), and the 2026-05-24 ADR confirmed-miss fire.</em></p>

## At-a-Glance Summary Tables

### Currently Live (validated as of 2026-05-25)

| Component | Status | Notes |
|---|---|---|
| Production web service | LIVE | https://adr-wildfire.com/, HTTP 200 in 0.4 s, gunicorn on DGX, Tailscale |
| Satellite-data daemons | 21 polling + 3 reproducibility (grader, snapshot, null-bootstrap) | FIRMS (4 platforms), MTG-FCI, MTG-AF-L2, MTG-LI, SLSTR FRP, **Sentinel-2 burn-scar verifier (fixed 2026-05-26)**, Sentinel-1 SAR change detection, NISAR L-band SAR, TROPOMI HCHO, OroraTech public OSINT, worldcover, modis_viirs_sar, weather cams, CEMS EFFIS RDA, ANSA news, Italian news RSS, Reddit + Mastodon, smoke YOLO verifier, joint Dozier (FCI+SLSTR+S-2 fusion), Hawkes ignition forecaster, **event-grader v2.1**, **daily reproducibility snapshot**, **nightly null-distribution bootstrap** |
| Verification layer | **LIVE 2026-05-26** | Sentinel-2 dNBR burn-scar verifier operational end-to-end (first confirmed burn via SAR fallback already published: det_id 16802, 37.689°N 12.743°E, 2026-05-25 fci_l1c). Three stacked bugs fixed: STAC datetime format, MPC SAS signing, B8/B12 shape mismatch. |
| Tier-based event grading | LIVE (v2.1) | T0/T1/T2/T3 + race-strict (lead < 50% of revisit) + race-marginal (lead within revisit but ≥ 50%) + "first vs VVF/news*" + multi-stage reconcile (T+72h → T+14d → T+45d) + biome-aware dNBR thresholds (0.12 grass / 0.18 macchia / 0.27 forest via ESA WorldCover) + WUI class (U/I/W/N) + below-comparator-floor flag + comparator panel JSON. 2,287 events graded as of 2026-05-26. |
| Ground-truth registry | INITIATING | First confirmed-miss case logged: ADR 2026-05-24 fire at (37.562278°N, 13.440250°E) |
| Persistent FP catalog | LIVE | 18 zones (Etna summit, Gela refinery, Augusta-Priolo-Melilli industrial, Termini, Milazzo, Catania, Stromboli, glasshouse complexes, mining sites, solar farms). Citable as Zenodo 10.5281/zenodo.20369891 |
| Public API | LIVE | OpenAPI 3.1 spec at /api/openapi.json. JSON / CSV / GeoJSON / RSS / iCal endpoints. CC-BY 4.0. New 2026-05-26: `/api/event_grades`, `/api/event_grades.csv`, `/api/null_bootstrap`, `/data/snapshots/YYYY-MM-DD/`. |
| GitHub repo | LIVE | https://github.com/markl02us/persistent-thermal-sources-sicily — v1.0.0 tagged |
| DGX compute | LIVE | 4-stream live processing, NISAR detector mode with NASA Earthdata authentication active |

### 12-month Roadmap (firm)

| Milestone | Target | Status |
|---|---|---|
| P0 truth-discipline fixes (burn verifier + FRP gates + event-level scoring + symmetric source health + headline lead-vs-sensed) | Deploy in next two-week window | Code bundle staged offline at this time, awaiting safe deploy window |
| Confirmed-miss watcher live | Same deploy | Code ready, watches Sentinel-2 STAC for post-fire scenes over registered misses |
| Sentinel-2 proactive scar discovery daemon | +60 days | Inverse of current verifier — flags scars on every clear S-2 pass over Sicily, not only where PHOENIX already detected |
| Sentinel-2 SWIR Band 12 active-fire detection | +90 days | Catches active fires during S-2 pass window |
| Landsat 8/9 ingest | +120 days | 8-day combined revisit + 100 m thermal bands |
| Sentinel-1 dual-pol VH+VV + 14-day rolling baseline | +150 days | Per peer-reviewed Sardinia / Sicily methodology (Imperatore 2017, Mastro 2022) |
| Capella + Umbra Open Data (X-band) opportunistic ingest | +180 days | Sub-meter resolution for case-study post-fire validation |
| First ADR ground sensor deployed on Amica Radio FM tower | +180 days | Hardware acquired, MOU in progress |
| Home fire-sensor over LoRa, first 5 units in Alessandria della Rocca residences | +270 days | Motivated by the fatal fire |
| Pole-mounted pre-ignition VOC sensor cost-viability decision | +365 days | Research phase |

### Recurring Cost (entirely borne by ADR developers, never billed to INGV or anyone else)

| Item | Annual cost (USD) | Notes |
|---|---|---|
| DGX-class compute power + electricity | Not separately billed — owner-operated | Spark-b0c1 host, Tailscale-attached |
| Internet bandwidth for satellite-data ingestion | Owner-borne | ~3-5 TB/year of Copernicus / MPC / EUMETSAT pulls |
| Domain registration (adr-wildfire.com) | ~$15 | |
| Cloudflare proxying / DNS / TLS | $0 — free tier | |
| NASA Earthdata Login | $0 — free self-serve | Active for NISAR L-band SAR ingestion |
| Copernicus Data Space (CDSE) | $0 — free | |
| EUMETSAT data access | $0 — free | |
| Microsoft Planetary Computer | $0 — free anonymous reads + SAS-signed blob access | |
| FIRMS API | $0 — free | |
| Per ADR ground sensor (capex) | ~$8,290/site | Pi 5 + Hailo-8 (26 TOPS) + Hikvision 32× PTZ + Reolink wide-angle + LiteBeam AREDN ch177 5835 MHz + RAK4631 LoRa + solar power kit; 3-4 sites planned total |
| Per home LoRa fire sensor (capex) | TBD (~$80-150/unit estimated) | Specifications not yet finalized |
| Pole-mounted pre-ignition VOC sensor | TBD (research phase, ~$200-400/unit if viable) | Currently evaluating MQ-series vs PID vs MOX sensor cost-per-detection-range |

**INGV is asked for zero financial contribution.** The recurring cost is owned by the ADR developers personally as a community contribution.

---

## Honest Limits — What PHOENIX Is NOT

Before we describe what PHOENIX is, here is what it is not:

1. **Not yet a primary alerting system.** Today PHOENIX is a research / observation platform with a public dashboard. It does not yet broadcast directly to farmers via SMS / WhatsApp / Telegram. That capability is on the roadmap (+270 days) but **explicitly gated on achieving a measured verified-false-positive rate below 5% over a burn-scar-confirmed baseline**. We will not become the system that cries wolf and is then ignored.

2. **Not yet beating every comparator on every AOI.** A May 2026 internal audit found PHOENIX has a positive median lead time of +107.6 minutes versus comparators in the Agrigento AOI but a *negative* median lead of −98.9 minutes in the broader Sicily-wide AOI. The grader has since been updated to v2.1 with a stricter race-validity definition. Under the **race-strict** bar (PHOENIX lead > 0 AND lead < 50% of the satellite comparator's revisit AND ≥ 1 capable comparator AND not below detection floor), we have **0 wins in the last 30 days**. A permutation null-distribution bootstrap (200 replicates, ±24 h timestamp shift) yields null mean = 12.7 / p-value = 1.00 — i.e. under the strictest bar we are currently NOT statistically distinguishable from chance, and we publish that openly at `https://adr-wildfire.com/api/null_bootstrap`. Under the looser race-valid bar (lead within 100% of revisit), we have **2 PHOENIX-first events in the last 7 days**: a +9.1-min lead vs EUMETSAT MTG-AF-L2 on 2026-05-24 (T1, race-marginal*) and a +9.4-min lead vs Vigili del Fuoco on 2026-05-25 (T2, "first vs VVF*" — human-dispatch comparator, not race-strict-eligible because it isn't a satellite cadence). Both shown with explicit asterisk footnotes on `/wins.html`. The work to close the Sicily-wide algorithmic gap is in the P0/P1 roadmap.

3. **Not currently doing real-time pixel-rate processing.** Most of our work runs on 10-minute (FCI / MTG-AF-L2), 15-minute (MTG-LI, ANSA news), 30-minute (SLSTR, ARPA air, smoke YOLO), hourly (CEMS EFFIS) or longer (Sentinel-2 6 hours, Sentinel-1 12 hours, NISAR 24 hours) polling cycles. We are not generating millisecond-level alerts. Wildfire detection at this scale doesn't need it; we acknowledge a real-time fire department system would.

4. **Not commercial, not academic-publishing-first, not a startup.** We do not plan to monetize PHOENIX. We do not have an institutional affiliation. We are individual contributors who have built and run this system.

5. *(v1.1 disclosure resolved — see #7 below for the Sentinel-2 verifier fix shipped 2026-05-26.)*

6. **Subpixel_v1_alpha FRP overflow — RESOLVED 2026-05-26.** A prior audit finding (v1.1) flagged radiative-power values up to 3.9 petawatts (physically impossible) due to a unit conversion or overflow bug. As of 2026-05-26, this source's FRP distribution is sane: max = 9.09 MW, mean = 2.73 MW, n = 5,524, zero outliers above 10 GW. The fix is now in production and the disclosure is updated.

7. **Sentinel-2 burn-scar verifier — RESOLVED 2026-05-26.** A v1.1 audit finding said the verifier returned HTTP 400 on every call due to a Microsoft Planetary Computer STAC query-format bug. Three independent bugs were stacking: (a) `detection_ts.isoformat() + "Z"` produced an RFC-3339-malformed double timezone for tz-aware datetimes; (b) COG band reads received HTTP 409 because MPC Sentinel-2 L2A blobs require SAS-signed URLs; (c) NBR broadcasting failed because B8 (10 m) and B12 (20 m) come back at different shapes. All three are fixed (GitHub commit `eadb2ed`). End-to-end smoke test on the April 26 ADR detection: pre_NBR = 0.3072, post_NBR = 0.3423, dNBR = −0.0351, verified_burn = False (correct — that detection was a known FP). First confirmed-burn-via-SAR-fallback already landed (det_id 16802, 2026-05-25 fci_l1c at 37.689°N, 12.743°E).

We disclose these things in the first six pages of this document because the alternative is INGV finding them six months from now and concluding we were not forthright. Transparency is part of how we define being trustworthy.

---

---

*This is the Executive Summary only. The full technical pack — covering current architecture (Section 2), the 2026-05-24 worked example (Section 3), the ground-sensor roadmap (Section 4), the four INGV data exchanges (Section 5), AI/ML capability (Section 6), cost / schedule / performance with full Gantt (Section 7), honest limitations and the staged P0 deploy bundle (Section 8), and the annexes including BOM, link budget, SQL schemas, and the MoU template (Section 9) — is in `PHOENIX_INGV_Pack_EN.pdf` and its Italian companion.*