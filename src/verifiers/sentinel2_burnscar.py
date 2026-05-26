"""Sentinel-2 burn-scar verifier — ground-truth layer for PHOENIX detections.

For any detection (PHOENIX or comparator), pulls the most recent Sentinel-2
L2A scene over a 1 km box, computes Normalized Burn Ratio (NBR) and dNBR
vs a 30-day pre-fire baseline. Annotates the detection as:

  verified_burn = True   if dNBR > 0.27  (clear burn scar)
  verified_burn = False  if dNBR < 0.10  (no scar, likely FP)
  verified_burn = None   if cloud > 60%  or no S-2 scene available yet

Uses Microsoft Planetary Computer STAC API (free, no auth, anonymous reads).
Fallback to CDSE STAC if MPC unavailable.

Schedule: runs every 6h via a daemon, checks all detections 24-72h old.
"""
from __future__ import annotations

import io
import json
import logging
import math
import sqlite3
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

DB = "/media/mark/AI_DGX/eumetsat_data/ground_truth.sqlite"
CACHE = Path("/media/mark/AI_DGX/eumetsat_data/s2_burnscar_cache")
CACHE.mkdir(parents=True, exist_ok=True)

# Microsoft Planetary Computer STAC API (anonymous reads, no auth needed)
MPC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
USER_AGENT = "PHOENIX-burnscar-verifier/1.0 (https://adr-wildfire.com/)"

NBR_BURN_THRESHOLD = 0.27        # standard dNBR threshold for "burned"
NBR_NO_BURN_THRESHOLD = 0.10     # below this = no significant burn
CLOUD_THRESHOLD = 80             # 2026-05-26: raised 60->80; window dNBR survives partial scene cloud
SAR_FALLBACK_DAYS = 12           # 2026-05-26: when S2 unavailable, fall back to S1 SAR change
BBOX_KM = 1.0                    # half-side of the verification box


def _bbox(lat, lon, half_km=BBOX_KM):
    """Return (west, south, east, north) bbox of half_km around point."""
    half_lat = half_km / 111.0
    half_lon = half_km / (111.0 * max(0.01, abs(math.cos(math.radians(lat)))))
    return (lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat)


def _rfc3339(t: datetime) -> str:
    """Format datetime as RFC 3339 with trailing Z (STAC-compatible).

    Bug fix 2026-05-26: previously used ``t.isoformat() + 'Z'`` which produced
    ``2026-05-25T11:04:53+00:00Z`` for tz-aware datetimes - STAC rejects this
    as a malformed datetime (double timezone indicator).
    """
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    else:
        t = t.astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stac_search(lat, lon, start_iso, end_iso, max_items=10):
    """Query MPC STAC for Sentinel-2 L2A scenes intersecting (lat,lon) in [start,end]."""
    w, s, e, n = _bbox(lat, lon)
    body = {
        "collections": ["sentinel-2-l2a"],
        "bbox": [w, s, e, n],
        "datetime": f"{start_iso}/{end_iso}",
        "query": {"eo:cloud_cover": {"lt": CLOUD_THRESHOLD}},
        "limit": max_items,
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    req = urllib.request.Request(
        MPC_STAC,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _read_window_from_cog(cog_url: str, lat: float, lon: float,
                           half_km: float = BBOX_KM) -> Optional[np.ndarray]:
    """Read a windowed sub-image from a remote COG using rasterio."""
    try:
        import rasterio
        from rasterio.windows import from_bounds
        from rasterio.warp import transform_bounds
    except ImportError:
        logger.error("rasterio not available")
        return None
    w, s, e, n = _bbox(lat, lon, half_km)
    try:
        with rasterio.Env(GDAL_HTTP_UNSAFESSL="YES", AWS_NO_SIGN_REQUEST="YES"):
            with rasterio.open(cog_url) as src:
                # Convert WGS84 bbox to source CRS
                src_w, src_s, src_e, src_n = transform_bounds(
                    "EPSG:4326", src.crs, w, s, e, n, densify_pts=21
                )
                win = from_bounds(src_w, src_s, src_e, src_n, src.transform)
                arr = src.read(1, window=win, boundless=True, fill_value=0)
                return arr.astype(np.float32)
    except Exception as exc:
        logger.warning("cog read failed for %s: %s", cog_url, exc)
        return None


def _match_shape(target: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Resample ``target`` to ``ref``'s shape using nearest-neighbor.

    Sentinel-2 B8 is 10 m and B12 is 20 m, so the windowed arrays come back
    at different sizes (e.g. 203x204 vs 102x102). NBR needs them aligned.
    """
    if target.shape == ref.shape:
        return target
    th, tw = target.shape
    rh, rw = ref.shape
    if th == 0 or tw == 0:
        return target
    ys = (np.arange(rh) * (th / rh)).astype(int).clip(0, th - 1)
    xs = (np.arange(rw) * (tw / rw)).astype(int).clip(0, tw - 1)
    return target[np.ix_(ys, xs)]


def _nbr(b8: np.ndarray, b12: np.ndarray) -> float:
    """Mean NBR over the window. Returns NaN if data is invalid."""
    b12 = _match_shape(b12, b8)
    valid = (b8 > 0) & (b12 > 0)
    if valid.sum() < 4:
        return float("nan")
    nbr_pix = (b8[valid] - b12[valid]) / (b8[valid] + b12[valid] + 1e-6)
    return float(np.nanmean(nbr_pix))


def _sar_fallback_check(lat: float, lon: float, detection_ts: datetime,
                        days: int = SAR_FALLBACK_DAYS, radius_km: float = 5.0) -> Optional[dict]:
    """Query DB for a sentinel1_sar_change event near (lat,lon) within +days of detection.
    Returns dict with verified_burn=True + evidence, or None if no SAR change available.
    Weaker than S2 dNBR (no quantitative dNBR) but all-weather and faster revisit."""
    try:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        lo = detection_ts.isoformat()
        hi = (detection_ts + timedelta(days=days)).isoformat()
        rows = con.execute(
            "SELECT lat, lng, ts, raw_json FROM external_fires "
            "WHERE source IN ('sentinel1_sar_change','nisar_change') "
            "AND ts BETWEEN ? AND ?",
            (lo, hi)).fetchall()
        con.close()
        for r in rows:
            # haversine-ish degree approx; 1 deg ~ 111 km
            dlat = (r["lat"] - lat) * 111.0
            dlng = (r["lng"] - lon) * 111.0 * abs(math.cos(math.radians(lat)))
            if (dlat * dlat + dlng * dlng) ** 0.5 <= radius_km:
                return {
                    "verified_burn": True,
                    "method": "sentinel1_sar_change",
                    "sar_ts": r["ts"],
                    "distance_km": round((dlat * dlat + dlng * dlng) ** 0.5, 2),
                }
        return None
    except Exception:
        return None


def verify_detection(lat: float, lon: float, detection_ts: datetime,
                     baseline_days: int = 30) -> dict:
    """Check Sentinel-2 burn scar for one detection.

    Returns dict: {
      verified_burn: True/False/None,
      dNBR: float or None,
      pre_NBR: float or None,
      post_NBR: float or None,
      post_scene_dt: ISO timestamp or None,
      pre_scene_dt: ISO timestamp or None,
      cloud_obscured: bool,
      error: str or None,
    }
    """
    out = {"verified_burn": None, "dNBR": None, "pre_NBR": None,
           "post_NBR": None, "post_scene_dt": None, "pre_scene_dt": None,
           "cloud_obscured": False, "error": None,
           "checked_at": datetime.now(timezone.utc).isoformat()}

    # Find post-fire scene (24h after to 14d after detection)
    post_start = _rfc3339(detection_ts + timedelta(hours=24))
    post_end = _rfc3339(detection_ts + timedelta(days=14))
    try:
        post_results = _stac_search(lat, lon, post_start, post_end, max_items=3)
    except Exception as exc:
        out["error"] = f"post stac search failed: {exc}"
        return out
    post_items = post_results.get("features", [])
    if not post_items:
        # 2026-05-26: try SAR fallback before giving up
        sar = _sar_fallback_check(lat, lon, detection_ts)
        if sar is not None:
            out["verified_burn"] = sar["verified_burn"]
            out["verified_via_sar"] = True
            out["sar_evidence"] = sar
            return out
        out["error"] = "no post-fire S-2 scene yet (wait 24h+)"
        return out

    post_item = post_items[0]
    post_scene_dt = post_item["properties"]["datetime"]
    out["post_scene_dt"] = post_scene_dt
    cloud = post_item["properties"].get("eo:cloud_cover", 0)
    if cloud > CLOUD_THRESHOLD:
        # 2026-05-26: try SAR fallback for cloud-obscured scenes
        sar = _sar_fallback_check(lat, lon, detection_ts)
        if sar is not None:
            out["verified_burn"] = sar["verified_burn"]
            out["verified_via_sar"] = True
            out["sar_evidence"] = sar
            out["cloud_obscured"] = True
            return out
        out["cloud_obscured"] = True
        out["error"] = f"post-fire scene cloudy ({cloud:.0f}%)"
        return out

    # Find pre-fire baseline scene
    pre_start = _rfc3339(detection_ts - timedelta(days=baseline_days))
    pre_end = _rfc3339(detection_ts - timedelta(hours=1))
    try:
        pre_results = _stac_search(lat, lon, pre_start, pre_end, max_items=3)
    except Exception as exc:
        out["error"] = f"pre stac search failed: {exc}"
        return out
    pre_items = pre_results.get("features", [])
    if not pre_items:
        out["error"] = "no pre-fire baseline S-2 scene in 30d window"
        return out
    pre_item = pre_items[0]
    out["pre_scene_dt"] = pre_item["properties"]["datetime"]

    # Read B8 (NIR, 10m) and B12 (SWIR-2, 20m) for both scenes.
    # MPC blob URLs require SAS signing (HTTP 409 without it). planetary_computer.sign()
    # appends the short-lived SAS token.
    try:
        import planetary_computer as _pc
        post_b8_url  = _pc.sign(post_item["assets"]["B08"]["href"])
        post_b12_url = _pc.sign(post_item["assets"]["B12"]["href"])
        pre_b8_url   = _pc.sign(pre_item["assets"]["B08"]["href"])
        pre_b12_url  = _pc.sign(pre_item["assets"]["B12"]["href"])
    except ImportError:
        out["error"] = "planetary_computer library missing"
        return out
    except Exception as exc:
        out["error"] = f"asset signing failed: {exc}"
        return out
    try:
        post_b8 = _read_window_from_cog(post_b8_url, lat, lon)
        post_b12 = _read_window_from_cog(post_b12_url, lat, lon)
        pre_b8 = _read_window_from_cog(pre_b8_url, lat, lon)
        pre_b12 = _read_window_from_cog(pre_b12_url, lat, lon)
    except Exception as exc:
        out["error"] = f"band read failed: {exc}"
        return out

    if any(x is None for x in (post_b8, post_b12, pre_b8, pre_b12)):
        out["error"] = "band data unavailable"
        return out

    post_nbr = _nbr(post_b8, post_b12)
    pre_nbr = _nbr(pre_b8, pre_b12)
    if not (math.isfinite(post_nbr) and math.isfinite(pre_nbr)):
        out["error"] = "NBR computation gave NaN"
        return out

    dnbr = pre_nbr - post_nbr  # positive dNBR = NBR decreased = burn scar
    out["post_NBR"] = round(post_nbr, 4)
    out["pre_NBR"] = round(pre_nbr, 4)
    out["dNBR"] = round(dnbr, 4)
    if dnbr > NBR_BURN_THRESHOLD:
        out["verified_burn"] = True
    elif dnbr < NBR_NO_BURN_THRESHOLD:
        out["verified_burn"] = False
    else:
        out["verified_burn"] = None  # ambiguous
    return out


def run_verification_batch(lookback_days: int = 7, only_unverified: bool = True) -> dict:
    """Verify all detections from the last N days that don't yet have a burn-scar tag."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)
              ).strftime("%Y-%m-%dT%H:%M:%S")
    # Only check confirmed wins or strong-confidence detections
    rows = list(con.execute(
        "SELECT id, source, lat, lng, ts, raw_json FROM internal_fires "
        "WHERE ts > ? "
        "AND source IN ('fci_l1c','subpixel_v1_alpha','wind_diff','mtg_af_l2') "
        "AND (raw_json IS NULL OR raw_json NOT LIKE '%expired%') "
        "AND (raw_json IS NULL OR raw_json NOT LIKE '%verified_burn%') "
        "ORDER BY ts DESC LIMIT 30",
        (cutoff,)
    ))
    logger.info("verifying %d candidates", len(rows))

    summary = {"total": len(rows), "verified_true": 0, "verified_false": 0,
               "ambiguous": 0, "errors": 0}
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["ts"].replace("+00:00", "")).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        result = verify_detection(r["lat"], r["lng"], dt)
        # Patch raw_json with verification result
        try:
            existing = json.loads(r["raw_json"]) if r["raw_json"] else {}
        except Exception:
            existing = {}
        existing["burn_verification"] = result
        con.execute("UPDATE internal_fires SET raw_json = ? WHERE id = ?",
                    (json.dumps(existing), r["id"]))
        con.commit()
        if result.get("verified_burn") is True:
            summary["verified_true"] += 1
        elif result.get("verified_burn") is False:
            summary["verified_false"] += 1
        elif result.get("error"):
            summary["errors"] += 1
        else:
            summary["ambiguous"] += 1
        logger.info("  id=%s @ (%.3f,%.3f) verified=%s dNBR=%s err=%s",
                    r["id"], r["lat"], r["lng"],
                    result.get("verified_burn"), result.get("dNBR"),
                    result.get("error"))
    con.close()
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_verification_batch()
    print(json.dumps(summary, indent=2))


def polling_loop(cfg: dict = None, interval_seconds: int = 21600) -> None:
    """Run Sentinel-2 burn-scar verification batch every 6h.

    Iterates over PHOENIX detections from last 7 days that are 24h+ old
    and haven't been burn-verified yet, looks up post/pre Sentinel-2 scenes
    via Microsoft Planetary Computer STAC, computes dNBR, and patches
    the result into internal_fires.raw_json["burn_verification"].
    """
    import time as _time
    logger.info("sentinel2_burnscar polling loop started (interval=%ds)", interval_seconds)
    # Initial delay 5 min so the service is fully up before STAC calls
    _time.sleep(300)
    while True:
        try:
            summary = run_verification_batch(lookback_days=7)
            logger.info("sentinel2_burnscar: %s", summary)
        except Exception as exc:
            logger.warning("sentinel2_burnscar: batch failed: %s", exc)
        _time.sleep(interval_seconds)
