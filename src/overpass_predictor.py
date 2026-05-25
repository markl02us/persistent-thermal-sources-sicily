"""SGP4-based next-overpass predictor for the comparator satellites.

Used by grade_events.py to upgrade race_strict logic from nominal revisit to
TLE-propagated next-pass-given-swath. If sgp4 isn't installed or TLEs can't be
fetched, all predictors return None and the grader falls back to nominal revisit.

TLE source: Celestrak (active.txt). Cached for 24 h.
"""
from __future__ import annotations
import json, math, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    from sgp4.api import Satrec, jday
    SGP4_AVAILABLE = True
except ImportError:
    SGP4_AVAILABLE = False

TLE_CACHE = Path("/media/mark/AI_DGX/eumetsat_data/sat_tles.json")
TLE_TTL_HOURS = 24

# NORAD IDs at Sicily-relevant comparator constellations
COMPARATOR_NORAD = {
    "firms_viirs_snpp":   [37849],                       # Suomi NPP
    "firms_viirs_noaa20": [43013],                       # NOAA-20 / JPSS-1
    "firms_viirs_noaa21": [54234],                       # NOAA-21 / JPSS-2
    "firms_modis_nrt":    [25994, 27424],                # Terra, Aqua
    "firms_modis":        [25994, 27424],
    "slstr_frp_s3a":      [41335],                       # Sentinel-3A
    "slstr_frp_s3b":      [43437],                       # Sentinel-3B
    "firms_landsat":      [39084, 49260],                # Landsat 8, 9
    # Geostationary - always overhead, no overpass concept:
    "mtg_af_l2":          [],
    "seviri":             [],
    "fci_l1c":            [],
}

# Effective swath half-width in km (for in-view check)
SWATH_HALF_KM = {
    37849:  1530.0,   # VIIRS 3060 km swath
    43013:  1530.0,
    54234:  1530.0,
    25994:  1170.0,   # MODIS 2330 km swath
    27424:  1170.0,
    41335:  740.0,    # SLSTR 1420 km swath
    43437:  740.0,
    39084:  92.5,     # Landsat 8 OLI 185 km swath
    49260:  92.5,
}


CELESTRAK_GROUPS = ["active", "weather", "noaa", "resource", "science"]

# All NORAD IDs PHOENIX needs - used for direct fetch if not in any group
ALL_REQUIRED_NORAD = {37849, 43013, 54234, 25994, 27424, 41335, 43437, 39084, 49260}

def _parse_tle_3line(text):
    """Parse 3-line TLE block into {norad: {name, line1, line2}}."""
    out = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i in range(0, len(lines) - 2, 3):
        name = lines[i]; l1 = lines[i + 1]; l2 = lines[i + 2]
        if not l1.startswith("1 ") or not l2.startswith("2 "):
            continue
        try:
            norad = int(l1[2:7].strip())
        except Exception:
            continue
        out[norad] = {"name": name, "line1": l1, "line2": l2}
    return out

def _fetch_one_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "phoenix-wildfire/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("ascii", errors="ignore")
    except Exception:
        return ""

def _fetch_celestrak_active_tle():
    """Fetch and cache TLEs from multiple Celestrak groups, with per-NORAD
    fallback for satellites not in any standard group."""
    if TLE_CACHE.exists():
        try:
            age_h = (time.time() - TLE_CACHE.stat().st_mtime) / 3600.0
            if age_h < TLE_TTL_HOURS:
                cached = json.loads(TLE_CACHE.read_text())
                return {int(k): v for k, v in cached.items()}
        except Exception:
            pass
    by_norad = {}
    for group in CELESTRAK_GROUPS:
        text = _fetch_one_url(
            f"https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle")
        for norad, rec in _parse_tle_3line(text).items():
            by_norad.setdefault(norad, rec)
    # Direct fetch for any required NORADs missing from the groups
    missing = ALL_REQUIRED_NORAD - set(by_norad.keys())
    for nid in missing:
        text = _fetch_one_url(
            f"https://celestrak.org/NORAD/elements/gp.php?CATNR={nid}&FORMAT=tle")
        for norad, rec in _parse_tle_3line(text).items():
            by_norad.setdefault(norad, rec)
    if by_norad:
        try:
            TLE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            TLE_CACHE.write_text(json.dumps({str(k): v for k, v in by_norad.items()}))
        except Exception:
            pass
    return by_norad


def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1); dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


def _eci_to_geo(r_eci_km, t):
    """Convert ECI position (km) at time t (datetime UTC) to (lat, lng) degrees.
    Uses simple GMST sidereal-time approximation - accurate enough for swath checks."""
    x, y, z = r_eci_km
    # GMST at t (approximation, degrees)
    jd_ut1 = jday(t.year, t.month, t.day, t.hour, t.minute, t.second + t.microsecond/1e6)[0]
    T = (jd_ut1 - 2451545.0) / 36525.0
    gmst_sec = 67310.54841 + (876600 * 3600 + 8640184.812866) * T + 0.093104 * T * T - 6.2e-6 * T * T * T
    gmst_deg = (gmst_sec % 86400) / 240.0
    # Rotate from ECI to ECEF
    th = math.radians(gmst_deg)
    x_e = x * math.cos(th) + y * math.sin(th)
    y_e = -x * math.sin(th) + y * math.cos(th)
    r = math.sqrt(x_e * x_e + y_e * y_e)
    lat = math.degrees(math.atan2(z, r))
    lng = math.degrees(math.atan2(y_e, x_e))
    if lng > 180: lng -= 360
    if lng < -180: lng += 360
    return lat, lng


def next_overpass_min(comparator_source, target_lat, target_lng, after_t,
                     horizon_min=1440, step_sec=30):
    """Return minutes from after_t to the next time any spacecraft in this
    comparator family has the target within its swath. None if no candidate
    or sgp4 unavailable. Geostationary comparators return 0 (always overhead).

    horizon_min: don't search beyond this many minutes (default 12h)
    step_sec: propagation step (60s gives ~6 km along-track at 7 km/s)
    """
    if not SGP4_AVAILABLE:
        return None
    norads = COMPARATOR_NORAD.get(comparator_source, [])
    if not norads:
        return None  # geostationary or unknown
    tles = _fetch_celestrak_active_tle()
    if not tles:
        return None
    # JSON cache rehydration: keys may be strings
    if tles and isinstance(next(iter(tles.keys()), 0), str):
        tles = {int(k): v for k, v in tles.items()}
    best = None
    for nid in norads:
        meta = tles.get(nid)
        if not meta:
            continue
        try:
            sat = Satrec.twoline2rv(meta["line1"], meta["line2"])
        except Exception:
            continue
        swath_half = SWATH_HALF_KM.get(nid, 1000.0)
        t = after_t
        end_t = after_t + timedelta(minutes=horizon_min)
        while t <= end_t:
            jd, fr = jday(t.year, t.month, t.day, t.hour, t.minute,
                          t.second + t.microsecond / 1e6)
            err, r, v = sat.sgp4(jd, fr)
            if err == 0:
                try:
                    sat_lat, sat_lng = _eci_to_geo(r, t)
                    if _haversine_km(sat_lat, sat_lng, target_lat, target_lng) < swath_half:
                        elapsed = (t - after_t).total_seconds() / 60.0
                        if best is None or elapsed < best:
                            best = elapsed
                        break  # found next pass for this sat
                except Exception:
                    pass
            t = t + timedelta(seconds=step_sec)
    return best


if __name__ == "__main__":
    # quick smoke-test
    import sys
    now = datetime.now(timezone.utc)
    for src in ["firms_viirs_snpp", "firms_viirs_noaa20", "slstr_frp_s3a",
                "firms_modis_nrt", "mtg_af_l2"]:
        m = next_overpass_min(src, 37.5, 14.0, now)
        print(f"  {src:24s} next overpass over Sicily: {m} min")
