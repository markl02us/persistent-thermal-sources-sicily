#!/usr/bin/env python3
"""PHOENIX event grading v2 - council-revised methodology.

Changes vs v1:
  - comparator_class split (satellite_sensor | human_dispatch | social).
    Race-validity only applies to satellite_sensor comparators.
  - comparator_panel: for each event, rank ALL capable comparators with
    per-comparator lead/revisit/below-floor. worst_capable_lead drives
    race_strict. Kills comparator-of-convenience attack.
  - race_strict: lead > 0 AND lead < 0.5 * revisit_of_worst. The headline
    race-valid metric on /wins.html switches to race_strict.
  - below_comparator_floor: if event FRP below the physical detection
    floor of every capable comparator, the comparator literally couldn't
    have caught it. Excluded from FP denominator.
  - biome_class + dnbr_threshold_biome: per-cell ESA WorldCover lookup.
    Forest 0.27, shrub 0.18, crop/grass 0.12. RdNBR > 0.4 universal.
  - phoenix_had_coverage: for external-led events, did any PHX detector
    publish ANY row within +-30 min of the comparator's acquisition?
    Distinguishes algorithm-miss from data-feed-miss.
  - refute_strength: strong (cloud-free dNBR < biome_threshold) vs
    weak (dNBR ambiguous) vs unverifiable (no S2 scene).
  - Multi-stage reconcile: T+72h preliminary, T+14d, T+45d. Each stage
    can upgrade or downgrade the previous outcome.
  - Race-valid events that are refuted at T+72h are excluded from
    verified_wins (the bug surfaced by the red-team seat).

Run:
  python3 grade_events.py                # back-grade everything
  python3 grade_events.py --recent 2     # last 2 days only
  python3 grade_events.py --reconcile    # multi-stage reconcile pass
"""
import argparse, hashlib, json, math, sqlite3, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path("/media/mark/AI_DGX/eumetsat_data/ground_truth.sqlite")
WORLDCOVER_PATH = Path("/media/mark/AI_DGX/eumetsat_data/worldcover_sicily_cells.json")

# Comparator metadata (council audit revised)
COMPARATOR = {
    "firms_viirs_snpp":         {"class": "satellite_sensor", "revisit_min": 240, "floor_mw_night": 1.0,  "floor_mw_day": 5.0},
    "firms_viirs_noaa20":       {"class": "satellite_sensor", "revisit_min": 240, "floor_mw_night": 1.0,  "floor_mw_day": 5.0},
    "firms_viirs_noaa21":       {"class": "satellite_sensor", "revisit_min": 240, "floor_mw_night": 1.0,  "floor_mw_day": 5.0},
    "firms_viirs_noaa20_global":{"class": "satellite_sensor", "revisit_min": 240, "floor_mw_night": 1.0,  "floor_mw_day": 5.0},
    "firms_viirs_noaa20_nrt_archive": {"class": "satellite_sensor", "revisit_min": 240, "floor_mw_night": 1.0, "floor_mw_day": 5.0},
    "firms_viirs_noaa21_nrt_archive": {"class": "satellite_sensor", "revisit_min": 240, "floor_mw_night": 1.0, "floor_mw_day": 5.0},
    "firms_viirs_snpp_nrt_archive":   {"class": "satellite_sensor", "revisit_min": 240, "floor_mw_night": 1.0, "floor_mw_day": 5.0},
    "firms_modis_nrt":          {"class": "satellite_sensor", "revisit_min": 360, "floor_mw_night": 3.0,  "floor_mw_day": 6.0},
    "firms_modis":              {"class": "satellite_sensor", "revisit_min": 360, "floor_mw_night": 3.0,  "floor_mw_day": 6.0},
    "firms_landsat":            {"class": "satellite_sensor", "revisit_min": 1440,"floor_mw_night": 0.5,  "floor_mw_day": 1.0},
    "mtg_af_l2":                {"class": "satellite_sensor", "revisit_min": 10,  "floor_mw_night": 8.0,  "floor_mw_day": 12.0},
    "seviri":                   {"class": "satellite_sensor", "revisit_min": 15,  "floor_mw_night": 40.0, "floor_mw_day": 50.0},
    "fci_l1c":                  {"class": "satellite_sensor", "revisit_min": 10,  "floor_mw_night": 8.0,  "floor_mw_day": 12.0},
    "slstr_frp_s3a":            {"class": "satellite_sensor", "revisit_min": 1440,"floor_mw_night": 0.4,  "floor_mw_day": 0.8},
    "slstr_frp_s3b":            {"class": "satellite_sensor", "revisit_min": 1440,"floor_mw_night": 0.4,  "floor_mw_day": 0.8},
    "vigili_fuoco":             {"class": "human_dispatch",   "revisit_min": 60,  "floor_mw_night": 0.0,  "floor_mw_day": 0.0},
    "sentinel1_sar_change":     {"class": "satellite_sensor", "revisit_min": 17280,"floor_mw_night": 0.0, "floor_mw_day": 0.0},
    "tropomi_hcho_anomaly":     {"class": "satellite_sensor", "revisit_min": 1440,"floor_mw_night": 0.0,  "floor_mw_day": 0.0},
    "ansa_news":                {"class": "social",           "revisit_min": 360, "floor_mw_night": 0.0,  "floor_mw_day": 0.0},
    "italian_news_rss":         {"class": "social",           "revisit_min": 360, "floor_mw_night": 0.0,  "floor_mw_day": 0.0},
    "dpc":                      {"class": "human_dispatch",   "revisit_min": 60,  "floor_mw_night": 0.0,  "floor_mw_day": 0.0},
}

# Independent sensor families. Two detections from same family don't corroborate.
SENSOR_FAMILY = {
    "subpixel_v1_alpha": "phoenix_seviri", "wind_diff": "phoenix_seviri",
    "fci_l1c": "phoenix_fci", "adr": "phoenix_other", "seviri": "seviri",
    "firms_viirs_snpp": "viirs", "firms_viirs_noaa20": "viirs", "firms_viirs_noaa21": "viirs",
    "firms_viirs_noaa20_global":"viirs", "firms_viirs_noaa20_nrt_archive":"viirs",
    "firms_viirs_noaa21_nrt_archive":"viirs", "firms_viirs_snpp_nrt_archive":"viirs",
    "firms_modis_nrt": "modis", "firms_modis": "modis", "firms_landsat": "landsat",
    "mtg_af_l2": "mtg", "slstr_frp_s3a": "slstr", "slstr_frp_s3b": "slstr",
    "vigili_fuoco": "vvf", "sentinel1_sar_change":"sar", "tropomi_hcho_anomaly":"tropomi",
    "ansa_news": "news", "italian_news_rss": "news", "dpc": "civil_protection",
}

EVENT_CLUSTER_KM  = 5.0
EVENT_CLUSTER_MIN = 30
LEAD_CAP_MIN      = 120
T72H_WINDOW_HRS   = 72
T14D_WINDOW_HRS   = 14 * 24
T45D_WINDOW_HRS   = 45 * 24
GRADER_VERSION    = "v2"

# Biome -> dNBR threshold (Mediterranean-calibrated)
BIOME_DNBR = {
    "tree":  0.27,
    "shrub": 0.18,
    "crop":  0.12,
    "built": 0.12,
    "mixed": 0.18,
    "water": None,
}

EXCLUDED_TEST_SOURCES = {"firms_test", "firms_stub"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_grades (
    event_key                 TEXT PRIMARY KEY,
    cluster_lat               REAL NOT NULL,
    cluster_lng               REAL NOT NULL,
    first_ts                  TEXT NOT NULL,
    representative_source     TEXT NOT NULL,
    is_phoenix_led            INTEGER NOT NULL,
    verification_tier         TEXT NOT NULL,
    corroborator_families     TEXT NOT NULL,
    corroborator_count        INTEGER NOT NULL,
    corroborator_sources      TEXT NOT NULL,
    has_vigili_fuoco          INTEGER NOT NULL,
    has_news                  INTEGER NOT NULL,
    has_burn_scar             INTEGER NOT NULL,
    has_sar_change            INTEGER NOT NULL,
    has_lst_anomaly           INTEGER NOT NULL,
    race_valid                INTEGER,
    lead_likely_geometric     INTEGER,
    delivery_advantage_only   INTEGER,
    race_note                 TEXT,
    lead_min_vs_sensed        REAL,
    lead_min_vs_reported      REAL,
    comparator_source         TEXT,
    comparator_revisit_min    INTEGER,
    t72h_reconciled_at        TEXT,
    t72h_outcome              TEXT,
    t72h_outcome_evidence     TEXT,
    graded_at                 TEXT NOT NULL,
    grader_version            TEXT NOT NULL,
    comparator_class          TEXT,
    comparator_panel          TEXT,
    capable_comparator_count  INTEGER,
    worst_capable_lead_min    REAL,
    race_strict               INTEGER,
    below_comparator_floor    INTEGER,
    biome_class               TEXT,
    dnbr_threshold_biome      REAL,
    phoenix_had_coverage      INTEGER,
    refute_strength           TEXT,
    t14d_outcome              TEXT,
    t14d_outcome_evidence     TEXT,
    t14d_reconciled_at        TEXT,
    t45d_outcome              TEXT,
    t45d_outcome_evidence     TEXT,
    t45d_reconciled_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_grades_first_ts ON event_grades(first_ts);
CREATE INDEX IF NOT EXISTS idx_grades_tier     ON event_grades(verification_tier);
CREATE INDEX IF NOT EXISTS idx_grades_phoenix  ON event_grades(is_phoenix_led);
"""

POST_MIGRATION_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_grades_strict ON event_grades(race_strict)",
]

MIGRATIONS = [
    "ALTER TABLE event_grades ADD COLUMN comparator_class TEXT",
    "ALTER TABLE event_grades ADD COLUMN comparator_panel TEXT",
    "ALTER TABLE event_grades ADD COLUMN capable_comparator_count INTEGER",
    "ALTER TABLE event_grades ADD COLUMN worst_capable_lead_min REAL",
    "ALTER TABLE event_grades ADD COLUMN race_strict INTEGER",
    "ALTER TABLE event_grades ADD COLUMN below_comparator_floor INTEGER",
    "ALTER TABLE event_grades ADD COLUMN biome_class TEXT",
    "ALTER TABLE event_grades ADD COLUMN dnbr_threshold_biome REAL",
    "ALTER TABLE event_grades ADD COLUMN phoenix_had_coverage INTEGER",
    "ALTER TABLE event_grades ADD COLUMN refute_strength TEXT",
    "ALTER TABLE event_grades ADD COLUMN t14d_outcome TEXT",
    "ALTER TABLE event_grades ADD COLUMN t14d_outcome_evidence TEXT",
    "ALTER TABLE event_grades ADD COLUMN t14d_reconciled_at TEXT",
    "ALTER TABLE event_grades ADD COLUMN t45d_outcome TEXT",
    "ALTER TABLE event_grades ADD COLUMN t45d_outcome_evidence TEXT",
    "ALTER TABLE event_grades ADD COLUMN t45d_reconciled_at TEXT",
]


# ---------- helpers ----------
def parse_ts(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        t = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t

def km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1); dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

def event_key(lat, lng, first_t):
    cell = f"{round(lat, 2)},{round(lng, 2)},{first_t.strftime('%Y%m%d%H')}"
    return hashlib.sha1(cell.encode()).hexdigest()[:16]

def is_day(t):
    """Crude day flag: UTC hour 5-17 = day at Sicily latitude."""
    return 5 <= t.hour < 17


# ---------- worldcover biome lookup ----------
_WC_CACHE = None
def _worldcover():
    global _WC_CACHE
    if _WC_CACHE is None:
        try:
            _WC_CACHE = json.loads(WORLDCOVER_PATH.read_text())
        except Exception:
            _WC_CACHE = {}
    return _WC_CACHE

def biome_at(lat, lng):
    """Return (class, dnbr_threshold) for the dominant landcover at (lat,lng)."""
    wc = _worldcover()
    key = f"{lat:.2f}:{lng:.2f}"
    cell = wc.get(key)
    if not cell:
        return ("mixed", BIOME_DNBR["mixed"])
    # Dominant class
    classes = {k: v for k, v in cell.items() if k in BIOME_DNBR}
    if not classes:
        return ("mixed", BIOME_DNBR["mixed"])
    dom = max(classes, key=classes.get)
    if classes[dom] < 40:  # no clear majority
        return ("mixed", BIOME_DNBR["mixed"])
    return (dom, BIOME_DNBR.get(dom))


# ---------- data load ----------
def load_all(con, since=None):
    where_int = "WHERE confidence >= 0.5"
    where_ext = "WHERE 1=1"
    params_i = []; params_e = []
    if since is not None:
        where_int += " AND ts > ?"
        where_ext += " AND ts > ?"
        params_i = [since.isoformat()]
        params_e = [since.isoformat()]
    internals = list(con.execute(
        f"SELECT id, source, aoi_id, lat, lng, ts, confidence, frp_mw, "
        f"temperature_c, raw_json FROM internal_fires {where_int} ORDER BY ts",
        params_i))
    externals = list(con.execute(
        f"SELECT source, lat, lng, ts, ingested_at, raw_json "
        f"FROM external_fires {where_ext}",
        params_e))
    corr = list(con.execute(
        "SELECT source, lat, lng, ts FROM corroboration_signals "
        "WHERE lat IS NOT NULL AND lng IS NOT NULL AND ts IS NOT NULL"))
    return internals, externals, corr


# ---------- clustering ----------
def cluster_events(internals, externals):
    rows = []
    for det_id, src, aoi, lat, lng, ts, conf, frp, tmp, raw in internals:
        if src in EXCLUDED_TEST_SOURCES:
            continue
        t = parse_ts(ts)
        if t is None: continue
        rows.append(dict(kind="internal", src=src, lat=float(lat), lng=float(lng),
                         t=t, ts=ts, raw=raw, det_id=det_id, aoi=aoi,
                         frp=float(frp) if frp is not None else None))
    for src, lat, lng, ts, ingested, raw in externals:
        if src in EXCLUDED_TEST_SOURCES:
            continue
        t = parse_ts(ts)
        if t is None: continue
        rows.append(dict(kind="external", src=src, lat=float(lat), lng=float(lng),
                         t=t, ts=ts, raw=raw, ingested=ingested,
                         frp=None))
    rows.sort(key=lambda r: r["t"])

    events = []
    for r in rows:
        merged = False
        for ev in events:
            if abs((r["t"] - ev["first_t"]).total_seconds()) > EVENT_CLUSTER_MIN * 60:
                continue
            if km(r["lat"], r["lng"], ev["lat"], ev["lng"]) > EVENT_CLUSTER_KM:
                continue
            ev["members"].append(r)
            if r["t"] < ev["first_t"]:
                ev["first_t"] = r["t"]; ev["lat"] = r["lat"]; ev["lng"] = r["lng"]
                ev["representative"] = r
            merged = True
            break
        if not merged:
            events.append(dict(first_t=r["t"], lat=r["lat"], lng=r["lng"],
                               representative=r, members=[r]))
    return events


# ---------- phoenix coverage check ----------
def phx_coverage_index(internals):
    """Bucket PHX detections by 30-min UTC window for coverage lookup."""
    idx = {}
    for det_id, src, aoi, lat, lng, ts, *_ in internals:
        if src in EXCLUDED_TEST_SOURCES: continue
        t = parse_ts(ts)
        if t is None: continue
        bucket = t.replace(minute=(t.minute // 30) * 30, second=0, microsecond=0)
        idx.setdefault(bucket, 0)
        idx[bucket] += 1
    return idx

def had_phoenix_coverage(idx, t):
    """Was any PHX detector active within +-30 min of t?"""
    if not idx: return None  # unknown
    bucket = t.replace(minute=(t.minute // 30) * 30, second=0, microsecond=0)
    for delta in (-1, 0, 1):
        b = bucket + timedelta(minutes=30 * delta)
        if idx.get(b, 0) > 0:
            return 1
    return 0


# ---------- comparator panel ----------
def build_comparator_panel(ev, representative, frp):
    """For a PHOENIX-led event, list ALL comparator members with per-source
    lead_min and capability (below_floor, in_window). Returns (panel,
    worst_capable_lead, capable_count, below_floor_all)."""
    panel = []
    rep_t = representative["t"]
    rep_is_day = is_day(rep_t)
    for m in ev["members"]:
        if m["kind"] != "external":
            continue
        src = m["src"]
        meta = COMPARATOR.get(src, {"class": "unknown", "revisit_min": 240,
                                    "floor_mw_night": 0, "floor_mw_day": 0})
        lead = (m["t"] - rep_t).total_seconds() / 60.0
        floor = meta["floor_mw_day"] if rep_is_day else meta["floor_mw_night"]
        below_floor = (frp is not None and frp < floor) if floor > 0 else False
        capable = (
            meta["class"] == "satellite_sensor" and
            0 < lead <= LEAD_CAP_MIN and
            not below_floor
        )
        panel.append({
            "source": src,
            "class": meta["class"],
            "revisit_min": meta["revisit_min"],
            "floor_mw": floor,
            "lead_min": round(lead, 1),
            "below_floor": bool(below_floor),
            "capable": bool(capable),
        })
    capable_entries = [p for p in panel if p["capable"]]
    if capable_entries:
        worst = min(capable_entries, key=lambda p: p["lead_min"])
        worst_lead = worst["lead_min"]
        worst_revisit = worst["revisit_min"]
        worst_src = worst["source"]
    else:
        worst_lead = None; worst_revisit = None; worst_src = None
    # below-floor-all: event FRP below the floor of EVERY satellite comparator that saw the cluster
    sat_panel = [p for p in panel if p["class"] == "satellite_sensor"]
    below_all = (len(sat_panel) > 0 and all(p["below_floor"] for p in sat_panel))
    return panel, worst_lead, worst_revisit, worst_src, len(capable_entries), below_all


# ---------- grading ----------
def grade_event(ev, corr, phx_idx):
    rep = ev["representative"]
    members = ev["members"]
    phoenix_led = rep["kind"] == "internal"
    rep_t = rep["t"]
    frp = None
    for m in members:
        if m["kind"] == "internal" and m.get("frp") is not None:
            frp = m["frp"]; break

    rep_family = SENSOR_FAMILY.get(rep["src"], "unknown")
    family_to_sources = {}
    for m in members:
        fam = SENSOR_FAMILY.get(m["src"], "unknown")
        if fam == rep_family: continue
        family_to_sources.setdefault(fam, set()).add(m["src"])
    families = sorted(family_to_sources.keys())
    sources = sorted({s for srcs in family_to_sources.values() for s in srcs})

    has_vvf  = "vvf" in family_to_sources
    has_news = "news" in family_to_sources or "civil_protection" in family_to_sources
    has_sar  = "sar" in family_to_sources
    has_lst  = False
    has_burn = False

    # biome lookup
    biome, biome_threshold = biome_at(ev["lat"], ev["lng"])

    # burn-scar verification from raw_json
    burn_dnbr = None
    for m in members:
        if m["kind"] != "internal" or not m.get("raw"): continue
        try:
            rd = json.loads(m["raw"])
        except Exception:
            continue
        if not isinstance(rd, dict): continue
        bv = rd.get("burn_verification") or {}
        if isinstance(bv, dict):
            dnbr = bv.get("dnbr")
            if isinstance(dnbr, (int, float)):
                if burn_dnbr is None or dnbr > burn_dnbr:
                    burn_dnbr = dnbr
            if bv.get("verified_burn") is True and burn_dnbr is None:
                burn_dnbr = biome_threshold or 0.27  # treat as just-above-threshold
    if burn_dnbr is not None and biome_threshold is not None and burn_dnbr > biome_threshold:
        has_burn = True

    # corroboration_signals
    for c_src, c_lat, c_lng, c_ts in corr:
        ct = parse_ts(c_ts)
        if ct is None: continue
        if abs((ct - ev["first_t"]).total_seconds()) > 86400: continue
        if km(c_lat, c_lng, ev["lat"], ev["lng"]) > 5.0: continue
        if c_src == "mod11_lst": has_lst = True
        elif c_src == "sentinel1_sar": has_sar = True

    # T3 = burn-scar (biome-aware) OR VVF+2 sat families
    sat_families = {f for f in family_to_sources
                    if f in ("viirs","modis","slstr","mtg","seviri","landsat",
                             "sar","tropomi","phoenix_fci","phoenix_seviri")
                    and f != rep_family}
    sat_corroborator_count = len(sat_families)
    if has_burn or (has_vvf and sat_corroborator_count >= 2):
        tier = "T3"
    elif has_vvf or has_news:
        tier = "T2"
    elif sat_corroborator_count >= 1:
        tier = "T1"
    else:
        tier = "T0"

    # comparator panel
    comparator_panel = []
    worst_lead = worst_revisit = comparator_source_chosen = None
    capable_count = 0; below_floor_all = False
    race_valid_loose = race_strict_flag = lead_geom = delivery_only = None
    race_note = ""
    lead_sensed = lead_reported = None
    comparator_class = None
    comparator_revisit_min = None

    if phoenix_led:
        (comparator_panel, worst_lead, worst_revisit, comparator_source_chosen,
         capable_count, below_floor_all) = build_comparator_panel(ev, rep, frp)
        if worst_lead is not None:
            lead_sensed = worst_lead
            comparator_revisit_min = worst_revisit
            meta = COMPARATOR.get(comparator_source_chosen, {})
            comparator_class = meta.get("class")
            # compute lead_reported using worst comparator's ingested_at
            for m in members:
                if m["kind"] == "external" and m["src"] == comparator_source_chosen:
                    ingested_t = parse_ts(m.get("ingested")) or m["t"]
                    lead_reported = round((ingested_t - rep_t).total_seconds() / 60.0, 1)
                    break
            race_valid_loose = (lead_sensed > 0 and lead_sensed <= worst_revisit
                                and comparator_class == "satellite_sensor")
            race_strict_flag = (lead_sensed > 0
                                and lead_sensed <= 0.5 * worst_revisit
                                and comparator_class == "satellite_sensor"
                                and capable_count >= 1
                                and not below_floor_all)
            lead_geom = (lead_sensed > worst_revisit)
            delivery_only = (lead_sensed <= 0 and lead_reported is not None and lead_reported > 0)
            if race_strict_flag:
                race_note = (f"Strict-race valid: lead {lead_sensed}min < 50% of "
                             f"{comparator_source_chosen} revisit ({worst_revisit}min). "
                             f"{capable_count} capable comparator(s).")
            elif race_valid_loose:
                race_note = (f"Loose-race valid only: lead {lead_sensed}min within "
                             f"{comparator_source_chosen} revisit ({worst_revisit}min) "
                             f"but >50% threshold. Marginal.")
            elif lead_geom:
                race_note = (f"Geometric: lead {lead_sensed}min exceeds "
                             f"{comparator_source_chosen} revisit ({worst_revisit}min).")

    # phoenix_had_coverage (only meaningful for external-led events)
    phx_cov = None
    if not phoenix_led:
        phx_cov = had_phoenix_coverage(phx_idx, rep_t)

    ek = event_key(ev["lat"], ev["lng"], ev["first_t"])
    return dict(
        event_key=ek,
        cluster_lat=ev["lat"], cluster_lng=ev["lng"],
        first_ts=ev["first_t"].isoformat(),
        representative_source=rep["src"],
        is_phoenix_led=int(phoenix_led),
        verification_tier=tier,
        corroborator_families=",".join(families),
        corroborator_count=len(families),
        corroborator_sources=",".join(sources),
        has_vigili_fuoco=int(has_vvf),
        has_news=int(has_news),
        has_burn_scar=int(has_burn),
        has_sar_change=int(has_sar),
        has_lst_anomaly=int(has_lst),
        race_valid=(None if race_valid_loose is None else int(race_valid_loose)),
        lead_likely_geometric=(None if lead_geom is None else int(lead_geom)),
        delivery_advantage_only=(None if delivery_only is None else int(delivery_only)),
        race_note=race_note or None,
        lead_min_vs_sensed=lead_sensed,
        lead_min_vs_reported=lead_reported,
        comparator_source=comparator_source_chosen,
        comparator_revisit_min=comparator_revisit_min,
        comparator_class=comparator_class,
        comparator_panel=json.dumps(comparator_panel) if comparator_panel else None,
        capable_comparator_count=capable_count,
        worst_capable_lead_min=worst_lead,
        race_strict=(None if race_strict_flag is None else int(race_strict_flag)),
        below_comparator_floor=int(below_floor_all),
        biome_class=biome,
        dnbr_threshold_biome=biome_threshold,
        phoenix_had_coverage=phx_cov,
        graded_at=datetime.now(timezone.utc).isoformat(),
        grader_version=GRADER_VERSION,
    )


# ---------- multi-stage reconcile ----------
def _scan_corroborators(con, lat, lng, ft, window_hrs, radius_km=5.0):
    """Look for confirming evidence (VVF, news, burn-scar) within a time/space window."""
    horizon_lo = (ft - timedelta(hours=window_hrs)).isoformat()
    horizon_hi = (ft + timedelta(hours=window_hrs)).isoformat()
    # VVF
    for vlat, vlng, vts in con.execute(
        "SELECT lat, lng, ts FROM external_fires "
        "WHERE source = 'vigili_fuoco' AND ts BETWEEN ? AND ?",
        (horizon_lo, horizon_hi)):
        if km(vlat, vlng, lat, lng) <= radius_km:
            return "confirmed_vvf", f"VVF report at {vts}"
    # News
    for nlat, nlng, nts in con.execute(
        "SELECT lat, lng, ts FROM external_fires "
        "WHERE source IN ('ansa_news','italian_news_rss','dpc') "
        "AND ts BETWEEN ? AND ?",
        (horizon_lo, horizon_hi)):
        if km(nlat, nlng, lat, lng) <= radius_km * 2:
            return "confirmed_news", f"News at {nts}"
    return None, None

def _scan_burnscar(con, lat, lng, biome_threshold):
    """Find any burn-scar verification within ~5km of the event cell."""
    best_dnbr = None
    for raw, in con.execute(
        "SELECT raw_json FROM internal_fires "
        "WHERE ABS(lat - ?) < 0.05 AND ABS(lng - ?) < 0.05 "
        "AND raw_json LIKE '%burn_verification%'", (lat, lng)):
        try:
            rd = json.loads(raw)
            bv = rd.get("burn_verification") or {}
            d = bv.get("dnbr")
            if isinstance(d, (int, float)):
                if best_dnbr is None or d > best_dnbr:
                    best_dnbr = d
            elif bv.get("verified_burn") is True and best_dnbr is None:
                best_dnbr = biome_threshold or 0.27
        except Exception:
            pass
    if best_dnbr is None:
        return None, None, "unverifiable"
    if biome_threshold is not None and best_dnbr > biome_threshold:
        return "confirmed_burnscar", f"S2 dNBR={best_dnbr:.3f} (>{biome_threshold} biome threshold)", "strong"
    if biome_threshold is not None and best_dnbr < 0.10:
        return "refuted_no_scar", f"S2 dNBR={best_dnbr:.3f} (clear-of-fire)", "strong"
    return None, f"S2 dNBR={best_dnbr:.3f} ambiguous", "weak"

def reconcile_stage(con, stage_name, window_hrs, age_hrs):
    """Run a reconcile stage for events older than age_hrs that haven't been reconciled at this stage yet."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=age_hrs)).isoformat()
    age_col = {"t72h": ("t72h_reconciled_at", "t72h_outcome", "t72h_outcome_evidence"),
               "t14d": ("t14d_reconciled_at", "t14d_outcome", "t14d_outcome_evidence"),
               "t45d": ("t45d_reconciled_at", "t45d_outcome", "t45d_outcome_evidence")}[stage_name]
    reconciled_at_col, outcome_col, evidence_col = age_col
    rows = list(con.execute(
        f"SELECT event_key, cluster_lat, cluster_lng, first_ts, "
        f"       verification_tier, dnbr_threshold_biome, is_phoenix_led "
        f"FROM event_grades "
        f"WHERE first_ts <= ? AND {reconciled_at_col} IS NULL",
        (cutoff,)))
    if not rows:
        return 0
    updated = 0
    for ek, lat, lng, first_ts, tier, biome_t, phx_led in rows:
        ft = parse_ts(first_ts)
        if ft is None: continue
        outcome, evidence = _scan_corroborators(con, lat, lng, ft, window_hrs)
        refute_strength = None
        if outcome is None:
            bs_outcome, bs_evidence, bs_strength = _scan_burnscar(con, lat, lng, biome_t)
            if bs_outcome:
                outcome = bs_outcome
                evidence = bs_evidence
                refute_strength = bs_strength
            else:
                refute_strength = bs_strength
        if outcome is None:
            if tier == "T0" and phx_led:
                if refute_strength == "unverifiable":
                    outcome = "no_signal_unverifiable"
                    evidence = "No S2 burn-scar scene available in window (cloud or temporal gap)"
                else:
                    outcome = "refuted_likely_fp"
                    evidence = f"No corroborating evidence after {age_hrs}h"
            else:
                outcome = "no_signal"
                evidence = f"No new corroborating evidence at {stage_name}"
        now_iso = datetime.now(timezone.utc).isoformat()
        if stage_name == "t72h":
            con.execute(
                f"UPDATE event_grades SET {reconciled_at_col} = ?, "
                f"{outcome_col} = ?, {evidence_col} = ?, refute_strength = ? "
                f"WHERE event_key = ?",
                (now_iso, outcome, evidence, refute_strength, ek))
        else:
            con.execute(
                f"UPDATE event_grades SET {reconciled_at_col} = ?, "
                f"{outcome_col} = ?, {evidence_col} = ? "
                f"WHERE event_key = ?",
                (now_iso, outcome, evidence, ek))
        updated += 1
    con.commit()
    return updated

def reconcile_all_stages(con):
    n72  = reconcile_stage(con, "t72h", T72H_WINDOW_HRS, T72H_WINDOW_HRS)
    n14d = reconcile_stage(con, "t14d", T14D_WINDOW_HRS, T14D_WINDOW_HRS)
    n45d = reconcile_stage(con, "t45d", T45D_WINDOW_HRS, T45D_WINDOW_HRS)
    return n72, n14d, n45d


# ---------- upsert ----------
UPSERT_SQL = """
INSERT INTO event_grades (
    event_key, cluster_lat, cluster_lng, first_ts, representative_source,
    is_phoenix_led, verification_tier, corroborator_families, corroborator_count,
    corroborator_sources, has_vigili_fuoco, has_news, has_burn_scar,
    has_sar_change, has_lst_anomaly, race_valid, lead_likely_geometric,
    delivery_advantage_only, race_note, lead_min_vs_sensed, lead_min_vs_reported,
    comparator_source, comparator_revisit_min, graded_at, grader_version,
    comparator_class, comparator_panel, capable_comparator_count,
    worst_capable_lead_min, race_strict, below_comparator_floor,
    biome_class, dnbr_threshold_biome, phoenix_had_coverage
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(event_key) DO UPDATE SET
    verification_tier=excluded.verification_tier,
    corroborator_families=excluded.corroborator_families,
    corroborator_count=excluded.corroborator_count,
    corroborator_sources=excluded.corroborator_sources,
    has_vigili_fuoco=excluded.has_vigili_fuoco,
    has_news=excluded.has_news,
    has_burn_scar=excluded.has_burn_scar,
    has_sar_change=excluded.has_sar_change,
    has_lst_anomaly=excluded.has_lst_anomaly,
    race_valid=excluded.race_valid,
    lead_likely_geometric=excluded.lead_likely_geometric,
    delivery_advantage_only=excluded.delivery_advantage_only,
    race_note=excluded.race_note,
    lead_min_vs_sensed=excluded.lead_min_vs_sensed,
    lead_min_vs_reported=excluded.lead_min_vs_reported,
    comparator_source=excluded.comparator_source,
    comparator_revisit_min=excluded.comparator_revisit_min,
    graded_at=excluded.graded_at,
    grader_version=excluded.grader_version,
    comparator_class=excluded.comparator_class,
    comparator_panel=excluded.comparator_panel,
    capable_comparator_count=excluded.capable_comparator_count,
    worst_capable_lead_min=excluded.worst_capable_lead_min,
    race_strict=excluded.race_strict,
    below_comparator_floor=excluded.below_comparator_floor,
    biome_class=excluded.biome_class,
    dnbr_threshold_biome=excluded.dnbr_threshold_biome,
    phoenix_had_coverage=excluded.phoenix_had_coverage
"""

def upsert(con, g):
    con.execute(UPSERT_SQL, (
        g["event_key"], g["cluster_lat"], g["cluster_lng"], g["first_ts"],
        g["representative_source"], g["is_phoenix_led"], g["verification_tier"],
        g["corroborator_families"], g["corroborator_count"], g["corroborator_sources"],
        g["has_vigili_fuoco"], g["has_news"], g["has_burn_scar"],
        g["has_sar_change"], g["has_lst_anomaly"], g["race_valid"],
        g["lead_likely_geometric"], g["delivery_advantage_only"], g["race_note"],
        g["lead_min_vs_sensed"], g["lead_min_vs_reported"], g["comparator_source"],
        g["comparator_revisit_min"], g["graded_at"], g["grader_version"],
        g["comparator_class"], g["comparator_panel"], g["capable_comparator_count"],
        g["worst_capable_lead_min"], g["race_strict"], g["below_comparator_floor"],
        g["biome_class"], g["dnbr_threshold_biome"], g["phoenix_had_coverage"],
    ))


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=None)
    ap.add_argument("--reconcile", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(str(DB_PATH))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    # idempotent migrations for v1 -> v2
    for m in MIGRATIONS:
        try: con.execute(m)
        except sqlite3.OperationalError: pass  # already exists
    for idx in POST_MIGRATION_INDEXES:
        try: con.execute(idx)
        except sqlite3.OperationalError: pass

    if args.reconcile:
        n72, n14d, n45d = reconcile_all_stages(con)
        print(f"reconcile: t72h={n72} t14d={n14d} t45d={n45d}")
        return

    since = None
    if args.recent is not None:
        since = datetime.now(timezone.utc) - timedelta(days=args.recent)

    print(f"loading detections (since={since})...")
    internals, externals, corr = load_all(con, since=since)
    print(f"  internal_fires (conf>=0.5): {len(internals)}")
    print(f"  external_fires:             {len(externals)}")
    print(f"  corroboration_signals:      {len(corr)}")

    print("building phoenix coverage index...")
    phx_idx = phx_coverage_index(internals)

    print("clustering events...")
    events = cluster_events(internals, externals)
    print(f"  events: {len(events)}")

    print("grading...")
    tier_counts = {"T0": 0, "T1": 0, "T2": 0, "T3": 0}
    race_strict_count = 0; race_loose_only = 0; below_floor_count = 0
    con.execute("BEGIN")
    try:
        for ev in events:
            g = grade_event(ev, corr, phx_idx)
            upsert(con, g)
            tier_counts[g["verification_tier"]] += 1
            if g["race_strict"]: race_strict_count += 1
            elif g["race_valid"]: race_loose_only += 1
            if g["below_comparator_floor"]: below_floor_count += 1
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK"); raise

    print(f"  T0/T1/T2/T3: {tier_counts['T0']}/{tier_counts['T1']}/"
          f"{tier_counts['T2']}/{tier_counts['T3']}")
    print(f"  race-strict={race_strict_count}  race-loose-only={race_loose_only}")
    print(f"  below_comparator_floor (excluded from FP denom): {below_floor_count}")

    print("running multi-stage reconcile...")
    n72, n14d, n45d = reconcile_all_stages(con)
    print(f"  reconciled: t72h={n72} t14d={n14d} t45d={n45d}")


if __name__ == "__main__":
    main()
