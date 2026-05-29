# Data sources — provenance + decision rationale

This document explains *which* upstream feed each PHOENIX data source actually
uses, *why* that choice was made, and what the practical trade-offs are. The
goal is transparency: external researchers should be able to evaluate whether
our source choices are defensible for their use case before building on the
catalog.

---

## Air quality — EEA / ARPA Sicilia (PM2.5, PM10, CO)

### What we publish
Hourly PM2.5, PM10, and CO observations at 8 Sicilian monitoring stations
(Palermo Castelnuovo, Catania Veneto, Siracusa Bixio, Messina Boccetta,
Agrigento Aragona, Trapani Marsala, Gela, Caltanissetta). Stored in the
`air_quality` table; surfaced as smoke-correlation evidence when a wildfire
is detected within ~50 km of a station showing PM2.5 ≥ 35 μg/m³ (the WHO
"unhealthy for sensitive groups" threshold).

### Upstream feed: Open-Meteo air-quality API
- Endpoint: `https://air-quality-api.open-meteo.com/v1/air-quality`
- Cost: free, no API key, no auth required.
- Polling: every 30 minutes (per-station).
- Format: JSON with hourly time series.

### Why Open-Meteo (rather than EEA or ARPA Sicilia directly)

We initially planned to fetch directly from EEA's Air Quality e-Reporting
service or ARPA Sicilia's regional portal. After investigation in 2026-05,
we adopted Open-Meteo as our practical upstream for the following reasons:

1. **EEA up-to-date export endpoints have moved repeatedly and currently
   return 404.** The classic `discomap.eea.europa.eu/map/fme/latest/*.csv`
   pattern is gone. The newer `eeadmz1-downloads-webapp.azurewebsites.net`
   service is a Single-Page-App download portal designed for human
   researchers, not a programmable API; the JS bundle would need to be
   reverse-engineered to find the data endpoints, which is fragile and
   undocumented.

2. **ARPA Sicilia's regional portal (`amapa.arpa.sicilia.it`, `arpa.sicilia.it`)
   was network-unreachable from our hosting** at the time of integration
   (TCP timeouts at the connection layer, separate from the EEA story).
   This may have been a temporary outage, but we cannot build a
   production-cadence ingestion against an endpoint we can't reliably reach.

3. **Open-Meteo redistributes the same underlying Copernicus Atmosphere
   Monitoring Service (CAMS) European reanalysis** that EEA itself
   publishes, plus station-anchored ground observations where available.
   Quality is equivalent for the smoke-corroboration use case we have.
   The data is the same; we are choosing a different "post office".

4. **Reliability and operational discipline.** Open-Meteo has a stated
   uptime commitment, geographically distributed CDN, predictable response
   shapes, and no auth complications. For a free public-good wildfire
   detection service, picking the most reliable free path matters more than
   picking the politically "first-party" path. We document the dependency
   openly here so anyone re-running the system understands what they are
   actually pulling.

### Trade-offs and caveats

- **Station-anchored values vs satellite-derived grid.** Open-Meteo blends
  ground-station observations (where available) with the ~80 km CAMS
  satellite grid. For our 8 Sicilian stations, the values are
  ground-anchored; for any future station we add outside CAMS-station
  coverage, values will fall back to the satellite grid. We accept this.
- **Latency.** Open-Meteo's hourly cadence runs roughly 60-90 minutes
  behind the actual hour. That is acceptable for our use case (we are not
  alerting *from* air quality; we are *cross-checking* a fire signal we
  already have).
- **Honest attribution.** Our `/come-funziona` credits list both
  "EEA / ARPA Sicilia" (the data originators) and "CAMS via Open-Meteo"
  (our delivery path). We do not claim a direct pipeline that we do not
  have.

### What would make us reconsider

- If EEA publishes a stable, documented, machine-readable up-to-date
  endpoint, we will evaluate switching.
- If ARPA Sicilia exposes a JSON or RSS feed reachable from our hosting
  with hourly cadence, we will dual-source for cross-validation.
- If Open-Meteo introduces auth or rate limits that conflict with our
  per-station 30-minute polling, we will evaluate alternatives.

---

## Volcanic + thermal anomaly — INGV HOTSAT

PHOENIX subtracts known persistent volcanic vents (Etna, Stromboli, Vulcano)
from the wildfire candidate stream using INGV HOTSAT as the authoritative
prior. We do not re-publish INGV data; we use it locally for masking.

---

## Satellite fire detection — NASA FIRMS + EUMETSAT MTG + Copernicus
Sentinel-1/-2/-3/-5P

All comparator detections come from the originating organization's official
NRT feed. PHOENIX is downstream only — we do not modify, re-attribute, or
republish upstream fire detections. See `/come-funziona` for the full
per-feed inventory and `/accuracy` for measured per-feed precision.

---

## Weather context — Open-Meteo (T2m, wind, RH, precipitation)

Same Open-Meteo backend as air quality, same reasoning. CAMS + ECMWF blend.
Used only to render wind arrows + projected smoke cones on the map.

---

## OSM + Esri World Imagery

OSM tag data is fetched via Overpass API for FP-catalog cross-reference
(industrial-area polygons, glasshouse tags, quarry polygons). Esri World
Imagery tiles are downloaded at 250m × 250m for AI-vision classification
of FP candidates. Both are cached locally to bound upstream load.

---

*Document last reviewed 2026-05-29 by Alessandria Della Rocca Applications.*
