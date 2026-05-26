#!/usr/bin/env python3
"""
ADR WildFire Solution - Advanced Detection and Response WildFire Monitoring System
Production-level wildfire detection for Sicily with multi-source integration.

ML second-stage: PhoenixClassifier filters Dozier/sub-pixel candidates.
  conf >= ml_accept_threshold  -> publish
  0.3 <= conf < threshold      -> status="provisional" (stored, no alert)
  conf < 0.3                   -> suppressed
  source == "effis"            -> bypass ML (ground-truth)
"""

import numpy as np
import logging
import yaml
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template_string, jsonify
from threading import Thread
import time
import json
from pathlib import Path
import os
import psutil
import GPUtil
from typing import Dict, List, Optional

# ── Phoenix ML classifier (second-stage) ─────────────────────────────────────
try:
    import sys as _sys
    _src_dir = Path(__file__).parent / "src"
    if str(_src_dir) not in _sys.path:
        _sys.path.insert(0, str(_src_dir))
    from ml_classifier import PhoenixClassifier as _PhoenixClassifier
    _ML_AVAILABLE = True
except Exception as _ml_import_err:
    _ML_AVAILABLE = False
    _PhoenixClassifier = None
    logging.warning("ml_classifier import failed: %s — ML filter disabled", _ml_import_err)

# Import system components
try:
    from eumetsat_api import EUMETSATClient
    from fire_detection import FireDetector
    from copernicus_effis import CopernicusEFFIS
    from enhanced_detection_system import EnhancedFireDetector
    from subpixel_enhancement import SubPixelEnhancementPipeline
except ImportError as e:
    logging.warning(f"Import error (running in synthetic mode): {e}")
    class EUMETSATClient:
        def __init__(self): pass
    class FireDetector:
        def __init__(self): pass
        def generate_synthetic_scene(self):
            from fire_detection import FireDetector as FD
            return FD().generate_synthetic_scene()
        def detect_fires_dozier(self, *args):
            from fire_detection import FireDetector as FD
            return FD().detect_fires_dozier(*args)
    class CopernicusEFFIS:
        def __init__(self): pass
        def get_current_fires(self): return []
    class EnhancedFireDetector:
        def __init__(self):
            from enhanced_detection_system import EnhancedFireDetector as EFD
            self.base_detector = EFD()._initialize_base_detector()
        def detect_fires_enhanced(self, bt_mir, bt_tir, lat, lon, additional_data=None):
            from fire_detection import FireDetector
            return FireDetector().detect_fires_dozier(bt_mir, bt_tir, lat, lon)
    class SubPixelEnhancementPipeline:
        def __init__(self): pass
        def process_frame(self, thermal_data, spectral_cube=None, timestamp=None):
            return {
                'ensemble': np.zeros_like(thermal_data),
                'ensemble_binary': np.zeros_like(thermal_data, dtype=bool),
                'confidence_map': np.zeros_like(thermal_data),
                'final_detections': np.zeros_like(thermal_data, dtype=bool)
            }

# S2 SWIR + SubPixelV3 detectors — independent import, never blocks main system
try:
    import sys as _s2sys, os as _s2os
    _s2_src = _s2os.path.join(_s2os.path.dirname(_s2os.path.abspath(__file__)), "src")
    if _s2_src not in _s2sys.path:
        _s2sys.path.insert(0, _s2_src)
    from sentinel2_swir import Sentinel2SWIRDetector
    from subpixel_v3 import SubPixelV3Detector
except ImportError as _ie2:
    logging.warning("S2/SubPixelV3 import error: %s", _ie2)
    class Sentinel2SWIRDetector:
        def __init__(self, cfg): pass
        def detect(self, **kw): return []
    class SubPixelV3Detector:
        def __init__(self, cfg): pass
        def detect(self, **kw): return []
        def process_frame(self, *a, **kw): return []

# Ground-truth + scoring + camera modules
try:
    import sys as _gt_sys
    _gt_sys.path.insert(0, '/home/mark/.openclaw/workspace/eumetsat_wildfire_detection')
    from src.ground_truth import (init_db, insert_internal,
                                  firms_polling_loop, effis_polling_loop)
    from src.scoring import compute_scoreboard, nightly_scoring_loop
    from src.terrestrial_camera import register_camera_routes
    GT_AVAILABLE = True
except ImportError as _gt_err:
    GT_AVAILABLE = False
    import logging as _glog
    _glog.getLogger(__name__).warning('GT modules not available: %s', _gt_err)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('adr_wildfire_solution.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load configuration
config_path = Path('config.yaml')
if not config_path.exists():
    default_config = {
        'area_of_interest': {
            'name': 'Sicily',
            'center': [37.4167, 13.5167],
            'bbox': {'west': 12.4, 'south': 36.6, 'east': 15.4, 'north': 38.3}
        },
        'eumetsat': {'collection': 'EO:EUM:DAT:MSG:RSS', 'poll_interval': 15},
        'firms': {'map_key': '', 'poll_interval': 30},
        'effis': {'update_interval': 5},
        'storage': {
            'base_path': '/media/mark/AI_DGX',
            'detections': 'eumetsat_data/detections',
            'logs': 'eumetsat_data/logs',
            'ground_truth_db': 'eumetsat_data/ground_truth.sqlite',
            'camera_frames': 'eumetsat_data/camera_frames'
        }
    }
    with open(config_path, 'w') as f:
        yaml.dump(default_config, f)

config = yaml.safe_load(open(config_path))

# Per-AOI ML thresholds (default 0.5 if not in config)
_PER_AOI_THRESHOLD_FILE = Path("/home/mark/.openclaw/workspace/eumetsat_wildfire_detection/per_aoi_thresholds.json")
_PER_AOI_OVERRIDES_CACHE = {"ts": 0, "data": None}


def _per_aoi_overrides() -> dict:
    """Read the per-AOI threshold overrides file (60s cache)."""
    import time as _t, json as _j
    now = _t.time()
    if _PER_AOI_OVERRIDES_CACHE["data"] is not None and (now - _PER_AOI_OVERRIDES_CACHE["ts"] < 60):
        return _PER_AOI_OVERRIDES_CACHE["data"]
    d = {}
    try:
        if _PER_AOI_THRESHOLD_FILE.exists():
            d = _j.loads(_PER_AOI_THRESHOLD_FILE.read_text())
    except Exception:
        d = {}
    _PER_AOI_OVERRIDES_CACHE["data"] = d
    _PER_AOI_OVERRIDES_CACHE["ts"] = now
    return d


def _hawkes_threshold_adjustment(lat: float, lon: float) -> float:
    """If Hawkes 24h prior > 0.5 for the (lat,lon) cell, lower ml_accept by 0.05.

    Maps the "lower MIR-delta trigger threshold by 1K" spec onto the ml_accept
    score axis (which is a sigmoid'd post-detector confidence). Empirically a
    1K reduction in MIR delta corresponds to ~0.04-0.06 confidence delta;
    we use -0.05 as the conservative middle.
    """
    try:
        from src.data_sources.hawkes_ignition import lookup_hawkes_prior
        r = lookup_hawkes_prior(lat, lon)
        if r.get("available") and r.get("prob_24h", 0.0) > 0.5:
            return -0.05
    except Exception:
        pass
    return 0.0


def _ml_threshold_for(lat: float, lon: float) -> float:
    """Return the ml_accept threshold for the tightest matching AOI.

    Priority:
      1. per_aoi_thresholds.json override (auto-tuned by FP density)
      2. config.yaml aoi.thresholds.ml_accept
      3. global default 0.5
    Then applies Hawkes adaptive reduction (-0.05 if prob_24h > 0.5),
    clamped to [0.40, 0.80].
    """
    overrides = _per_aoi_overrides()
    base = None
    for _name, _aoi in config.get('aois', {}).items():
        if not _aoi.get('enabled', True):
            continue
        s, w, n, e = _aoi['bbox']
        if s <= lat <= n and w <= lon <= e:
            override = overrides.get(_name)
            if isinstance(override, (int, float)):
                base = float(override)
            else:
                base = float(_aoi.get('thresholds', {}).get('ml_accept', 0.5))
            break
    if base is None:
        base = float(config.get('ml_accept_threshold', 0.5))
    adj = _hawkes_threshold_adjustment(lat, lon)
    return max(0.40, min(0.80, base + adj))


def recompute_per_aoi_thresholds(write: bool = True) -> dict:
    """Compute recommended ml_accept per AOI based on persistent_fp density.

    Formula: threshold = 0.5 + 0.02 * min(persistent_fp_count_30d / 5, 12)
    Clamped to [0.5, 0.75].

    Returns the mapping {aoi_name: threshold}. If `write` is True, also
    persists to per_aoi_thresholds.json.
    """
    import sqlite3 as _sql, json as _j
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    cutoff = (_dt.now(_tz.utc) - _td(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    suggestions = {}
    try:
        con = _sql.connect(str(gt_db))
        for name, aoi in config.get('aois', {}).items():
            if not aoi.get('enabled', True):
                continue
            try:
                s, w, n, e = aoi['bbox']
            except Exception:
                continue
            # Count detections from any source that landed in a FP zone within
            # this AOI's bbox (proxy for FP density)
            n_in_zone = 0
            try:
                rows = list(con.execute(
                    "SELECT lat, lng FROM external_fires WHERE ts > ? "
                    "AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
                    (cutoff, s, n, w, e)
                ))
                for la, lo in rows:
                    if _is_known_anomaly(la, lo):
                        n_in_zone += 1
            except Exception:
                pass
            # Threshold suggestion
            extra = 0.02 * min(n_in_zone / 5.0, 12.0)
            suggestion = round(min(0.75, max(0.5, 0.5 + extra)), 2)
            suggestions[name] = {"threshold": suggestion,
                                  "fp_count_30d": n_in_zone,
                                  "current_config": float(aoi.get('thresholds', {}).get('ml_accept', 0.5))}
        con.close()
    except Exception as exc:
        logger.warning("recompute_per_aoi_thresholds: %s", exc)
        return {}
    if write:
        try:
            override_data = {k: v["threshold"] for k, v in suggestions.items()}
            _PER_AOI_THRESHOLD_FILE.write_text(_j.dumps(override_data, indent=2))
            _PER_AOI_OVERRIDES_CACHE["data"] = None  # bust cache
            logger.info("recompute_per_aoi_thresholds: wrote %d overrides", len(override_data))
        except Exception as exc:
            logger.warning("recompute_per_aoi_thresholds write: %s", exc)
    return suggestions

# Initialize components
try:
    eumetsat_client = EUMETSATClient()
    fire_detector = FireDetector()
    effis_client = CopernicusEFFIS()
    enhanced_detector = EnhancedFireDetector()
    subpixel_pipeline = SubPixelEnhancementPipeline()
    SYSTEM_READY = True
    logger.info("All system components initialized successfully")
except Exception as e:
    logger.error(f"Component initialization error: {e}, running in synthetic mode")
    SYSTEM_READY = False

# New parallel detectors (independent init — never suppress main system)
try:
    s2_swir_detector = Sentinel2SWIRDetector(config)
    subpixel_v3_detector = SubPixelV3Detector(config)
    logger.info("S2 SWIR + SubPixelV3 detectors initialized")
except Exception as _e_init:
    logger.error("S2/SubPixelV3 init error: %s", _e_init)
    s2_swir_detector = Sentinel2SWIRDetector({})
    subpixel_v3_detector = SubPixelV3Detector({})

# Real-data evidence streams (replaces synthetic-scene call in monitoring loop).
# Each stream is independent — failure of one falls back through the chain to
# synthetic-scene so the loop never starves the downstream detectors.
try:
    from src.data_sources.seviri_rss import SeviriRssClient
    seviri_rss_client = SeviriRssClient()
    logger.info("seviri_rss_client initialized (MSG15-RSS, 5-min cadence, ~3km IR)")
except Exception as _e_seviri:
    seviri_rss_client = None
    logger.error("seviri_rss_client init failed: %s — synthetic fallback only", _e_seviri)

try:
    from src.data_sources.fci_l1c import FciL1cClient
    fci_l1c_client = FciL1cClient()
    logger.info("fci_l1c_client initialized (MTG-I1 FDHSI, 10-min cadence, ~2km IR)")
except Exception as _e_fci:
    fci_l1c_client = None

try:
    from src.data_sources.mtg_lst import MtgLstClient
    mtg_lst_client = MtgLstClient()
    logger.info("mtg_lst_client initialized (LSA-007 LST, 10-min cadence, 2km — feeds MIR-LST delta gate)")
except Exception as _e_lst:
    mtg_lst_client = None
    logger.warning("mtg_lst_client init failed: %s — subpixel_v1 will fall back to absolute MIR floor", _e_lst)

try:
    from src.data_sources.effis_fwi import EffisClient as EffisFwiClient
    effis_fwi_client = EffisFwiClient()
    logger.info("effis_fwi_client initialized (EFFIS Fire Weather Index, daily ECMWF 8km forecast — fire-danger prior)")
except Exception as _e_effis_fwi:
    effis_fwi_client = None
    logger.warning("effis_fwi_client init failed: %s — FWI prior unavailable", _e_effis_fwi)


# Sub-pixel detector v1 (alpha) — runs in parallel with legacy 3-pixel-cluster
# detector. Detections flow to detections_list tagged 'subpixel_v1_alpha' /
# status='provisional' so the live map can distinguish them.
try:
    from src.detectors.subpixel_v1 import detect_subpixel_v1, detection_to_dict
    SUBPIXEL_V1_AVAILABLE = True
    logger.info("subpixel_v1 detector available (single-pixel, MIR≥320K, Dozier≥12K, lightning-primed relaxation)")
except Exception as _e_sp1:
    SUBPIXEL_V1_AVAILABLE = False
    logger.error("subpixel_v1 import failed: %s", _e_sp1)
    logger.error("fci_l1c_client init failed: %s — SEVIRI-only mode", _e_fci)

# Wind-advection-corrected temporal differencing detector (Council Round 1
# Seat 2 recommendation). Subtracts upwind-shifted previous frame from current
# to expose advected fire plumes that the static-threshold detectors can't see.
# Free leverage, no training, no GPU.
try:
    from src.detectors.wind_advection import detect_wind_advection, detection_to_dict as wind_diff_to_dict
    WIND_DIFF_AVAILABLE = True
    _prev_frames = {}   # {source_stream: (bt_mir, lat, lon, timestamp)}
    logger.info("wind_advection detector available (ΔT_adv ≥ 3K residual, ICON-EU wind)")
except Exception as _e_wd:
    WIND_DIFF_AVAILABLE = False
    _prev_frames = {}
    logger.error("wind_advection import failed: %s", _e_wd)

# Initialize ML classifier singleton
_ml_classifier = None
if _ML_AVAILABLE:
    try:
        _ml_classifier = _PhoenixClassifier(device="cpu")
        logger.info("PhoenixClassifier initialized (second-stage ML filter active)")
    except Exception as _e:
        logger.warning("PhoenixClassifier init failed: %s — ML filter disabled", _e)

# Storage setup
detections_list = []
effis_retention_days = 7
storage_path = Path(config['storage']['base_path']) / config['storage']['detections']
storage_path.mkdir(parents=True, exist_ok=True)

# Flask web app
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>ADR WildFire Solution - LIVE Monitoring</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body { margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        #map { position: absolute; top: 0; bottom: 150px; width: 100%; }
        #info { position: absolute; bottom: 0; width: 100%; height: 150px;
                background: linear-gradient(to bottom, #2c3e50, #1a2530); padding: 15px; box-sizing: border-box;
                border-top: 3px solid #e74c3c; color: white; }
        .stat { display: inline-block; margin-right: 30px; }
        .stat-label { font-size: 12px; color: #bdc3c7; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-value { font-size: 28px; font-weight: bold; color: #ecf0f1; }
        .header { position: absolute; top: 10px; left: 10px; z-index: 1000;
                  background: rgba(44, 62, 80, 0.95); padding: 15px; border-radius: 8px;
                  box-shadow: 0 4px 15px rgba(0,0,0,0.3); color: white; }
        .live-badge { background: linear-gradient(45deg, #e74c3c, #c0392b); color: white;
                      padding: 5px 12px; border-radius: 20px; font-size: 13px; font-weight: bold;
                      display: inline-flex; align-items: center; }
        .pulse { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
                 background: #2ecc71; margin-right: 8px; animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .legend { position: absolute; top: 10px; right: 10px; z-index: 1000;
                  background: rgba(44, 62, 80, 0.95); padding: 15px; border-radius: 8px;
                  box-shadow: 0 4px 15px rgba(0,0,0,0.3); color: white; }
        .legend-item { margin: 6px 0; font-size: 14px; display: flex; align-items: center; }
        .legend-color { width: 16px; height: 16px; border-radius: 50%; margin-right: 8px; }
        .system-status { position: absolute; top: 130px; left: 10px; z-index: 1000;
                         background: rgba(44, 62, 80, 0.95); padding: 10px 15px; border-radius: 8px;
                         box-shadow: 0 4px 15px rgba(0,0,0,0.3); color: white; font-size: 12px; }
        .status-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
                            margin-right: 5px; }
        .status-ok { background: #2ecc71; }
        .status-warning { background: #f39c12; }
        .status-error { background: #e74c3c; }
        .alert-zone { stroke: #f39c12 !important; fill-opacity: 0.05 !important; stroke-width: 2px !important; stroke-dasharray: 5, 5 !important; }
        /* Meteosat-12 (MTG-I1) live stream widget — operator cross-check */
        .mtg-widget { position: absolute; bottom: 160px; right: 10px; z-index: 1000;
                      background: rgba(44, 62, 80, 0.95); border-radius: 8px;
                      box-shadow: 0 4px 15px rgba(0,0,0,0.3); color: white; overflow: hidden;
                      transition: width 0.25s ease, height 0.25s ease; }
        .mtg-widget.collapsed { width: 180px; height: 32px; }
        .mtg-widget.expanded { width: 360px; height: 240px; }
        .mtg-bar { display: flex; align-items: center; justify-content: space-between;
                   padding: 6px 10px; font-size: 12px; cursor: pointer;
                   background: linear-gradient(to right, #2c3e50, #1a2530);
                   border-bottom: 1px solid #34495e; user-select: none; }
        .mtg-bar .mtg-title { display: flex; align-items: center; gap: 6px; }
        .mtg-bar .mtg-dot { width: 8px; height: 8px; border-radius: 50%; background: #e74c3c;
                            animation: pulse 2s infinite; }
        .mtg-bar .mtg-tabs { display: none; gap: 4px; }
        .mtg-widget.expanded .mtg-tabs { display: inline-flex; }
        .mtg-tab { padding: 1px 8px; border-radius: 10px; background: #34495e;
                   font-size: 10px; cursor: pointer; }
        .mtg-tab.active { background: #3498db; }
        .mtg-frame { width: 100%; height: calc(100% - 32px); border: 0; display: block; }
        .mtg-widget.collapsed .mtg-frame { display: none; }
    </style>
</head>
<body>
    <div class="header">
        <h2 style="margin: 0; color: #ecf0f1;">ADR WildFire Solution</h2>
        <p style="margin: 5px 0; color: #bdc3c7;">Advanced Detection and Response System</p>
        <p style="margin:3px 0">
            <a href="/scoreboard.html" style="color:#f39c12;font-size:12px;">&#127942; Lead-Time Scoreboard</a>
            &nbsp;·&nbsp;
            <a href="/wins.html" style="color:#2ecc71;font-size:12px;">&#10003; Confirmed Wins</a>
        </p>
        <div style="margin-top: 8px;">
            <span class="pulse"></span>
            <span class="live-badge">PRODUCTION MODE - REAL-TIME MONITORING</span>
        </div>
    </div>

    <div class="system-status">
        <div><span class="status-indicator status-ok"></span> System: <span id="sys-status">OPERATIONAL</span></div>
        <div>CPU: <span id="cpu-util">0%</span>, RAM: <span id="ram-util">0%</span></div>
        <div>GPU: <span id="gpu-util">0%</span>, VRAM: <span id="gpu-mem">0%</span></div>
    </div>

    <div class="legend">
        <h3 style="margin: 0 0 12px 0; color: white;">Fire Detection Legend</h3>
        <div class="legend-item">
            <div class="legend-color" style="background: #e74c3c;"></div>
            <span>EFFIS Confirmed Fires</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #3498db;"></div>
            <span>ADR Enhanced Detection</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #f39c12;"></div>
            <span>Priority Alert Zone</span>
        </div>
        <div class="legend-item">
            <div class="legend-color" style="background: #95a5a6;"></div>
            <span>Provisional (ML borderline)</span>
        </div>
        <div class="legend-item" style="margin-top: 12px; font-size: 11px; color: #bdc3c7;">
            Data retained for 7 days | ML second-stage active
        </div>
    </div>

    <div id="map"></div>

    <div id="info">
        <div class="stat">
            <div class="stat-label">Active Detections</div>
            <div class="stat-value" id="total">0</div>
        </div>
        <div class="stat">
            <div class="stat-label">EFFIS Validated</div>
            <div class="stat-value" style="color: #e74c3c;" id="effis">0</div>
        </div>
        <div class="stat">
            <div class="stat-label">ADR Enhanced</div>
            <div class="stat-value" style="color: #3498db;" id="adr">0</div>
        </div>
        <div class="stat">
            <div class="stat-label">Last 24h</div>
            <div class="stat-value" id="recent">0</div>
        </div>
        <div class="stat">
            <div class="stat-label">EFFIS Update</div>
            <div class="stat-value" style="font-size: 14px;" id="effis-time">-</div>
        </div>
        <div class="stat">
            <div class="stat-label">System Health</div>
            <div class="stat-value" style="font-size: 14px; color: #2ecc71;" id="health">OPTIMAL</div>
        </div>
    </div>

    <script>
        const map = L.map('map').setView([37.4167, 13.5167], 9);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(map);

        const sicilyBounds = L.polygon([
            [36.6, 12.4], [36.6, 15.4], [38.3, 15.4], [38.3, 12.4]
        ], { color: '#3498db', weight: 2, fillOpacity: 0.05 }).addTo(map).bindPopup('Sicily Monitoring Region');

        L.circle([37.4167, 13.5167], {
            radius: 25000, color: '#f39c12', fillColor: '#f39c12',
            fillOpacity: 0.1, weight: 2, className: 'alert-zone'
        }).addTo(map).bindPopup('25km Priority Alert Zone');

        let markers = [];

        function updateDetections() {
            fetch('/api/detections')
                .then(r => r.json())
                .then(data => {
                    markers.forEach(m => map.removeLayer(m));
                    markers = [];
                    data.detections.forEach(d => {
                        const isEffis = d.source === 'effis';
                        const isProv = d.status === 'provisional';
                        const color = isEffis ? '#e74c3c' : isProv ? '#95a5a6' : '#3498db';
                        const radius = isEffis ? 9 : 11;
                        const age_hours = (Date.now() - new Date(d.timestamp)) / 3600000;
                        const opacity = Math.max(0.3, 1 - (age_hours / 168));
                        const marker = L.circleMarker([d.lat, d.lon], {
                            radius, fillColor: color, color, weight: 2,
                            opacity, fillOpacity: opacity * 0.8
                        }).addTo(map);
                        const ageStr = age_hours < 1 ? 'Just detected' :
                                      age_hours < 24 ? `${Math.floor(age_hours)}h ago` :
                                      `${Math.floor(age_hours/24)}d ago`;
                        const mlLine = d.ml_confidence !== undefined ?
                            `<b>ML conf:</b> ${(d.ml_confidence*100).toFixed(0)}% (${d.ml_decision || ''})<br>` : '';
                        const imgBlock = d.image_url ?
                            `<img src="${d.image_url}" style="width:100%;max-width:520px;border-radius:6px;margin-bottom:8px;border:1px solid #34495e;background:#1a2530"/>
                             <div style="font-size:11px;color:#7f8c8d;margin-bottom:8px;line-height:1.3">
                               Left: MIR brightness temperature (white circle marks the detected pixel).<br>
                               Right: Dozier Δ (MIR&minus;TIR); larger positive Δ &rarr; stronger fire signature.
                             </div>` : '';
                        const confBlock = d.confirmation ?
                            `<div style="background:linear-gradient(45deg,#2ecc71,#27ae60);color:#fff;padding:8px 10px;border-radius:6px;margin-bottom:8px;font-size:13px;">
                               <b>✓ CONFIRMED by ${d.confirmation.confirmed_by.toUpperCase()}</b><br>
                               PHOENIX detected <b>${d.confirmation.lead_min} min</b> ahead (sensing-time).<br>
                               <span style="font-size:11px;opacity:0.9">Comparator sensed at ${new Date(d.confirmation.comparator_sensed_at).toLocaleString()} — ${d.confirmation.km} km away</span>
                             </div>`
                            : '<div style="background:#34495e;color:#bdc3c7;padding:6px 10px;border-radius:6px;margin-bottom:8px;font-size:12px;">⏳ Awaiting independent confirmation</div>';
                        const titleSuffix = d.confirmation ? ' ✓' : (isProv ? ' (PROVISIONAL)' : '');
                        marker.bindPopup(`
                            <div style="min-width: 280px;max-width:560px;">
                                <h3 style="margin:0 0 10px 0;color:${color};">Wildfire Detection${titleSuffix}</h3>
                                ${confBlock}
                                ${imgBlock}
                                <b>Source:</b> ${d.source.toUpperCase()}<br>
                                <b>Location:</b> (${d.lat.toFixed(4)}, ${d.lon.toFixed(4)})<br>
                                <b>Temperature:</b> ${d.fire_temperature_c.toFixed(1)}&deg;C<br>
                                <b>Power:</b> ${d.frp_mw.toFixed(1)} MW<br>
                                <b>Confidence:</b> ${(d.confidence*100).toFixed(0)}%<br>
                                ${mlLine}
                                <b>Detected:</b> ${ageStr}<br>
                                <b>Full Time:</b> ${new Date(d.timestamp).toLocaleString()}
                            </div>`, {maxWidth: 600});
                        markers.push(marker);
                    });
                    document.getElementById('total').textContent = data.count;
                    document.getElementById('effis').textContent = data.effis_count;
                    document.getElementById('adr').textContent = data.adr_count;
                    document.getElementById('recent').textContent = data.recent_24h;
                    document.getElementById('effis-time').textContent = data.last_effis_update;
                    document.getElementById('health').textContent = data.system_health;
                    document.getElementById('sys-status').textContent = data.system_status;
                }).catch(err => console.error('Error fetching detections:', err));
        }

        function updateSystemInfo() {
            fetch('/api/system_info')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('cpu-util').textContent = data.cpu_util + '%';
                    document.getElementById('ram-util').textContent = data.ram_util + '%';
                    document.getElementById('gpu-util').textContent = data.gpu_util + '%';
                    document.getElementById('gpu-mem').textContent = data.gpu_memory + '%';
                }).catch(err => console.error('Error fetching system info:', err));
        }

        updateDetections();
        updateSystemInfo();
        setInterval(updateDetections, 30000);
        setInterval(updateSystemInfo, 5000);

        // Meteosat-12 (MTG-I1) live stream widget — toggleable operator cross-check.
        // The widget HTML is below this <script> block, so we must defer wiring
        // until DOMContentLoaded — otherwise getElementById('mtg-bar') returns
        // null and the whole script crashes, blocking marker rendering.
        const MTG_STREAMS = {
            europe: 'nigtvuOspmM',
            africa: 'U3jRSL3y8Vc'
        };
        function initMtgWidget() {
            const mtgWidget = document.getElementById('mtg-widget');
            const mtgFrame = document.getElementById('mtg-frame');
            const mtgLabel = document.getElementById('mtg-label');
            const mtgBar = document.getElementById('mtg-bar');
            if (!mtgWidget || !mtgBar) return;  // widget not in DOM yet — silently bail
            let mtgActive = 'europe';
            function mtgSetStream(which) {
                mtgActive = which;
                mtgFrame.src = `https://www.youtube-nocookie.com/embed/${MTG_STREAMS[which]}?autoplay=1&mute=1&playsinline=1`;
                document.querySelectorAll('.mtg-tab').forEach(t => t.classList.toggle('active', t.dataset.stream === which));
            }
            function mtgToggle() {
                const expanded = mtgWidget.classList.toggle('expanded');
                mtgWidget.classList.toggle('collapsed', !expanded);
                mtgLabel.textContent = expanded ? 'Meteosat-12 LIVE' : 'Meteosat-12 LIVE \u25B2';
                if (expanded && !mtgFrame.src) mtgSetStream(mtgActive);
                if (!expanded) mtgFrame.src = '';  // stop playback when collapsed
            }
            mtgBar.addEventListener('click', (e) => {
                if (e.target.classList.contains('mtg-tab')) {
                    e.stopPropagation();
                    mtgSetStream(e.target.dataset.stream);
                } else {
                    mtgToggle();
                }
            });
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', initMtgWidget);
        } else {
            initMtgWidget();
        }
    </script>
    <div id="mtg-widget" class="mtg-widget collapsed" title="EUMETSAT Meteosat-12 (MTG-I1) live geostationary view — updated every 10 min">
        <div id="mtg-bar" class="mtg-bar">
            <span class="mtg-title"><span class="mtg-dot"></span><span id="mtg-label">Meteosat-12 LIVE \u25B2</span></span>
            <span class="mtg-tabs">
                <span class="mtg-tab active" data-stream="europe">EU</span>
                <span class="mtg-tab" data-stream="africa">AF</span>
            </span>
        </div>
        <iframe id="mtg-frame" class="mtg-frame" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>
    </div>
</body>
</html>
"""


# ── PHOENIX UI v2 routes (Mark 2026-05-24) ──────────────────────────────────

_NOMINATIM_CACHE = {}
_FP_CACHE = {"data": None, "mtime": 0.0}


def _load_fp_zones_cached():
    """Read sources.json with simple mtime-based cache."""
    import os, json as _json
    p = "/home/mark/phoenix_false_positives/sources.json"
    if not os.path.exists(p):
        return {"sources": []}
    mtime = os.path.getmtime(p)
    if _FP_CACHE["data"] is None or _FP_CACHE["mtime"] != mtime:
        with open(p) as f:
            _FP_CACHE["data"] = _json.load(f)
        _FP_CACHE["mtime"] = mtime
    return _FP_CACHE["data"]


@app.route("/api/false_positive_zones")
def api_false_positive_zones():
    """Return the persistent-FP catalog as JSON for the gray-polygon layer."""
    from flask import jsonify
    return jsonify(_load_fp_zones_cached())


@app.route("/api/reverse_geocode")
def api_reverse_geocode():
    """Nominatim proxy with on-disk cache. Returns {label: 'Vicino a Cammarata, ...'}."""
    from flask import request, jsonify
    import urllib.request, urllib.parse, json as _json, time
    try:
        lat = float(request.args.get("lat", "0"))
        lon = float(request.args.get("lon", "0"))
    except Exception:
        return jsonify({"label": ""}), 400
    key = f"{round(lat, 3)},{round(lon, 3)}"
    if key in _NOMINATIM_CACHE:
        return jsonify({"label": _NOMINATIM_CACHE[key], "cached": True})
    # Polite-use: Nominatim allows ~1 req/sec, OK for our reverse-geocode load
    try:
        url = ("https://nominatim.openstreetmap.org/reverse?"
               + urllib.parse.urlencode({"lat": lat, "lon": lon,
                                          "format": "json", "zoom": 12,
                                          "accept-language": "it"}))
        req = urllib.request.Request(url, headers={
            "User-Agent": "PHOENIX/1.0 (https://adr-wildfire.com/)",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read().decode())
        addr = data.get("address", {})
        town = (addr.get("village") or addr.get("town") or addr.get("city")
                or addr.get("hamlet") or addr.get("municipality") or "")
        prov = addr.get("province") or addr.get("county") or ""
        if town and prov:
            label = f"Vicino a {town} ({prov})"
        elif town:
            label = f"Vicino a {town}"
        elif prov:
            label = f"Provincia di {prov}"
        else:
            label = data.get("display_name", "")[:80]
        _NOMINATIM_CACHE[key] = label
        return jsonify({"label": label, "cached": False})
    except Exception as exc:
        logger.warning("reverse_geocode failed: %s", exc)
        return jsonify({"label": "", "error": str(exc)}), 200


_COME_FUNZIONA_HTML = r"""<!DOCTYPE html>
<html lang="it"><head>
<meta charset="utf-8">
<title>Come funziona PHOENIX - rilevamento incendi Sicilia</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="PHOENIX e' un sistema sperimentale open-source di rilevamento incendi boschivi per la Sicilia. Multi-sensore, accademico, non commerciale.">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body{font-family:'Segoe UI',-apple-system,sans-serif;background:#fafaf9;color:#1c1917;line-height:1.6}
  h1{color:#dc2626;font-size:2em;font-weight:700;margin-bottom:0.3em}
  h2{color:#0369a1;margin-top:2.5em;border-bottom:2px solid #e0f2fe;padding-bottom:0.3em;font-size:1.5em;font-weight:600}
  h3{color:#0c4a6e;margin-top:1.5em;font-size:1.15em;font-weight:600}
  h4{color:#0284c7;margin:0 0 6px 0;font-weight:600}
  a{color:#0284c7;text-decoration:none}
  a:hover{text-decoration:underline}
  code{background:#f1f5f9;padding:2px 6px;border-radius:3px;font-size:0.85em}
  .emergency{background:linear-gradient(90deg,#dc2626,#ef4444);color:white;padding:14px 18px;border-radius:8px;margin:18px 0;font-weight:600;display:flex;align-items:center;gap:14px}
  .emergency .num{font-size:1.8em;background:white;color:#dc2626;padding:6px 14px;border-radius:6px}
  .live-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}
  .stat{background:white;padding:14px;border-radius:8px;border:1px solid #e7e5e4;text-align:center}
  .stat .v{font-size:1.7em;font-weight:700;color:#0284c7}
  .stat .l{font-size:0.78em;color:#78716c;text-transform:uppercase;letter-spacing:0.5px;margin-top:4px}
  .src-card{border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin:10px 0;background:white}
  .src-card .meta{font-size:0.78em;color:#64748b;margin-bottom:6px;font-style:italic}
  .src-card .status{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.75em;font-weight:600;margin-left:8px}
  .status-ok{background:#d1fae5;color:#065f46}
  .status-warn{background:#fef3c7;color:#92400e}
  .status-err{background:#fee2e2;color:#991b1b}
  .lang-en{display:none}
  body.en .lang-it{display:none}
  body.en .lang-en{display:block}
  table{border-collapse:collapse;width:100%;margin:1em 0;font-size:0.9em}
  th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #e2e8f0}
  th{background:#e0f2fe;color:#0c4a6e}
  blockquote{border-left:4px solid #f59e0b;padding:8px 14px;background:#fffbeb;color:#78350f;margin:14px 0;border-radius:0 6px 6px 0}
  .footnote{font-size:0.85em;color:#64748b;margin-top:0.5em}
</style>
</head><body class="max-w-4xl mx-auto px-6 py-6">

<nav class="text-sm mb-4 flex justify-between items-center">
  <div class="flex gap-3 flex-wrap">
    <a href="/" class="text-stone-700">Mappa</a>
    <span class="text-stone-300">|</span>
    <a href="/wins.html" class="text-stone-700">Vittorie</a>
    <span class="text-stone-300">|</span>
    <a href="/accuracy.html" class="text-stone-700">Accuratezza feed</a>
    <span class="text-stone-300">|</span>
    <a href="/falsi-positivi" class="text-stone-700">Falsi positivi</a>
  </div>
  <button onclick="document.body.classList.toggle('en')" class="text-stone-600 hover:underline">EN/IT</button>
</nav>

<div class="lang-it">

<div class="emergency">
  <div class="num">115</div>
  <div>
    <div style="font-size:1.05em">Vedi un incendio? Chiama subito il 115.</div>
    <div style="font-size:0.85em;opacity:0.95;font-weight:400">PHOENIX e' uno strumento di supporto e ricerca - NON sostituisce i Vigili del Fuoco. La tua segnalazione tempestiva salva vite.</div>
  </div>
</div>

<h1>Come funziona PHOENIX</h1>
<p class="text-stone-600 text-lg">Sistema sperimentale, open-source, accademico e non commerciale per il rilevamento precoce degli incendi boschivi in Sicilia. Multi-sensore, multi-sorgente, totalmente trasparente.</p>

<div class="live-stats" id="live-stats">
  <div class="stat"><div class="v" id="s-sources">-</div><div class="l">Fonti dati attive</div></div>
  <div class="stat"><div class="v" id="s-detections">-</div><div class="l">Rilevamenti 30g</div></div>
  <div class="stat"><div class="v" id="s-wins">-</div><div class="l">Vittorie 7g</div></div>
  <div class="stat"><div class="v" id="s-fp">-</div><div class="l">Falsi positivi mappati</div></div>
  <div class="stat"><div class="v" id="s-precision">-</div><div class="l">Precisione PHOENIX</div></div>
</div>

<h2>1. Missione</h2>
<p>PHOENIX cerca di rilevare gli incendi boschivi in Sicilia <b>prima</b> dei servizi operativi disponibili (NASA FIRMS, EUMETSAT MTG-AF-L2, Copernicus EFFIS, Sentinel-3 SLSTR, ...). Combiniamo dati satellitari geostazionari + polari, segnalazioni umane (giornali, Vigili del Fuoco, social), e verifica indipendente delle cicatrici di incendio con Sentinel-2.</p>
<p>L'obiettivo NON e' competere - e' <b>aumentare l'ecosistema globale</b> di rilevamento incendi, contribuire dati e algoritmi liberi, e ridurre il tempo tra ignizione e prima risposta. <a href="/wins.html">Celebriamo le vittorie di tutti i rilevatori</a>, non solo le nostre.</p>

<h2>2. Cosa puoi fare TU</h2>
<blockquote>
  <b>Vedi un incendio adesso?</b> Chiama il <b>115</b> (Vigili del Fuoco) o il <b>1515</b> (Corpo Forestale).<br>
  <b>Hai visto un incendio ieri/oggi?</b> Puoi cercare la coordinata sulla nostra <a href="/">mappa</a> e vedere se PHOENIX/FIRMS l'hanno rilevato. Se non c'e', segnalacelo tramite GitHub (link in fondo).<br>
  <b>Sei un giornalista o cittadino con foto?</b> Le immagini georeferenziate aiutano la verifica - condividile sui social con tag #incendiosicilia.<br>
  <b>Sei un ricercatore?</b> Tutti i dati sono open (CC-BY 4.0). Vedi sezione 8 per come citare.
</blockquote>

<h2>3. Le due tipologie di "tempo"</h2>
<p>Quando un satellite vede un incendio ci sono due tempi distinti:</p>
<ul class="list-disc list-inside ml-4">
  <li><b>Tempo di acquisizione sensore</b> - quando il sensore satellitare ha effettivamente fotografato il pixel infuocato.</li>
  <li><b>Tempo di consegna del feed</b> - quando i dati elaborati sono diventati disponibili a noi tramite il loro servizio pubblico.</li>
</ul>
<p>La differenza e' la <b>latenza di reportistica</b> - downlink, elaborazione, pubblicazione. PHOENIX elabora localmente i dati grezzi e quindi ha un vantaggio strutturale di "vicinanza", ma siamo onesti su questo: il confronto algoritmico va fatto <b>contro il tempo di acquisizione sensore</b>, non contro il tempo di consegna del feed. Vogliamo essere i migliori a <i>rilevare</i> incendi, non i piu' vicini ai dati.</p>

<h2>4. Le 15+ fonti di dati</h2>
<p>Ogni rilevamento PHOENIX e' incrociato con queste fonti pubbliche (status verificato in tempo reale - <span class="status status-ok">verde</span> = attivo, <span class="status status-warn">giallo</span> = scaffold, <span class="status status-err">rosso</span> = down):</p>

<h3>Satelliti termici - rilevamento attivo</h3>
<div class="src-card"><h4>EUMETSAT MTG-FCI L1c <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: 10 min &middot; Risoluzione: 2 km &middot; Bande: MIR 3.8&micro;m, TIR 10.5&micro;m, SWIR 1.6/2.2&micro;m</div>
PHOENIX legge direttamente i frame L1c, calcola il delta MIR vs baseline storica, applica il gate MIR-LST &ge; 8K, e produce candidati con localizzazione sub-pixel (NHI SWIR + algoritmo Dozier).</div>

<div class="src-card"><h4>EUMETSAT MTG Active Fire L2 <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: 10 min &middot; Latenza: ~9 min dopo fine scansione</div>
Il prodotto ufficiale Active Fire L2 di EUMETSAT. Lo trattiamo sia come <b>comparator</b> (per misurare il nostro lead-time algoritmico) sia come sorgente di evidenza indipendente nel punteggio di confidenza.</div>

<div class="src-card"><h4>NASA FIRMS - VIIRS SNPP + NOAA-20/21 + MODIS <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: ~6 ore (passaggi polari) &middot; Risoluzione: 375m VIIRS / 1km MODIS</div>
Il servizio operativo di riferimento. <a href="/accuracy.html">Precisione misurata</a>: VIIRS-SNPP 90.3%, NOAA-21 74%, NOAA-20 45% (molti FP su zone industriali). Tutte le 5 varianti vengono pollate ogni 30 min.</div>

<div class="src-card"><h4>Sentinel-3 SLSTR L2 FRP NRT <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: ~4 passaggi/giorno per la Sicilia &middot; Risoluzione: 1 km</div>
Validazione termica indipendente con risoluzione migliore di SEVIRI. Pollato ogni 30 min via EUMETSAT Data Store.</div>

<h3>Validazione cicatrici di incendio</h3>
<div class="src-card"><h4>Sentinel-2 L2A (Microsoft Planetary Computer) <span class="status status-ok">attivo</span></h4>
<div class="meta">Revisita: 5 giorni &middot; Risoluzione: 10-20 m</div>
24-72 ore dopo ogni rilevamento PHOENIX scarichiamo il chunk Sentinel-2 sopra il pixel sospetto, calcoliamo l'indice cicatrice normalizzata <code>dNBR = pre-NBR - post-NBR</code>. dNBR &gt; 0.27 = cicatrice confermata, dNBR &lt; 0.10 = nessuna cicatrice. <b>Questo e' il nostro arbitro indipendente di verita'.</b></div>

<h3>Atmosfera e fumo</h3>
<div class="src-card"><h4>Sentinel-5P TROPOMI (CO/NO2/HCHO/AAI) <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: 1-2 passaggi/giorno &middot; Risoluzione: 5.5x3.5 km</div>
CO elevato + AAI (Aerosol Index) sottovento di un incendio attivo = firma di fumo. Aumenta la confidenza del rilevamento.</div>

<div class="src-card"><h4>EEA / ARPA Sicilia - PM2.5, PM10, CO <span class="status status-warn">scaffold</span></h4>
<div class="meta">Cadenza: oraria &middot; 8 stazioni siciliane (Palermo, Catania, Siracusa, Messina, Agrigento, Trapani, Gela, Caltanissetta)</div>
Picchi di PM2.5 sopra 35 &micro;g/m&sup3; (soglia OMS) correlano con fumo da incendi vicini. Cross-check terrestre.</div>

<div class="src-card"><h4>CAMS Aerosol (via Open-Meteo) <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: oraria &middot; AOD, polvere, AQI europeo</div>
Backend CAMS del Copernicus Atmosphere Monitoring Service, esposto via API gratuita di Open-Meteo. Contesto aerosol per ogni AOI siciliana.</div>

<h3>Meteo (contesto per la propagazione)</h3>
<div class="src-card"><h4>Open-Meteo (T2m, vento, RH, precipitazioni) <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: oraria &middot; 7 AOI siciliane</div>
Vento (velocita' + direzione), temperatura, umidita' relativa, precipitazioni. Usato per disegnare frecce del vento e coni di proiezione del fumo sulla mappa.</div>

<div class="src-card"><h4>EFFIS Fire Weather Index (WMS overlay) <span class="status status-ok">attivo</span></h4>
<div class="meta">Layer overlay attivabile sulla mappa</div>
Indice di pericolo incendi ECMWF/Copernicus. Quando l'FWI e' "very high" o "extreme", anche un piccolo allarme termico merita verifica immediata.</div>

<h3>Fulmini - innesco principale degli incendi estivi</h3>
<div class="src-card"><h4>EUMETSAT MTG Lightning Imager <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: continua (90 sec polling) &middot; Copertura: disco Meteosat</div>
I fulmini sono il principale innesco naturale. Fulmini negli ultimi 30 minuti su un pixel aumentano la priorita' di rilevamento.</div>

<h3>Segnalazioni umane (corroborazione)</h3>
<div class="src-card"><h4>ANSA Sicilia RSS <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: 15 min</div>
Cronaca regionale ANSA. Filtra per parole chiave (incendio, rogo, fiamme, devastato, ettari, vigili del fuoco, ...) e geolocalizza tramite tabella di centroidi comunali (~30 comuni).</div>

<div class="src-card"><h4>Vigili del Fuoco - Feed nazionale <span class="status status-ok">attivo</span></h4>
<div class="meta">Cadenza: 15 min &middot; vigilfuoco.it/rss.xml</div>
RSS ufficiale del Corpo Nazionale dei Vigili del Fuoco. Stesso filtro per incidenti siciliani.</div>

<div class="src-card"><h4>Giornale di Sicilia <span class="status status-warn">in tuning</span></h4>
<div class="meta">Cadenza: 15 min &middot; gds.it</div>
Quotidiano regionale siciliano. Cronaca + cronaca locale per provincia.</div>

<h3>Verifica burned-area (ground truth ufficiale)</h3>
<div class="src-card"><h4>CEMS Rapid Mapping + EFFIS RDA <span class="status status-warn">endpoint intermittente</span></h4>
<div class="meta">Cadenza: 1h &middot; Risoluzione: polygon-level</div>
Prodotti ufficiali Copernicus per valutazione rapida del danno (CEMS RM) + archivio EFFIS Rapid Damage Assessment. Quando un'attivazione CEMS copre un rilevamento PHOENIX, conferma.</div>

<h2>5. Metodologia di rilevamento (dentro la "scatola nera")</h2>
<p>L'algoritmo PHOENIX procede in queste 9 fasi:</p>
<ol class="list-decimal list-inside ml-4 space-y-1">
  <li><b>Lettura frame L1c</b> da MTG-FCI ogni 10 min.</li>
  <li><b>Calcolo del delta MIR</b> rispetto al baseline (media + sigma per ora-del-giorno e per pixel, calcolato sugli ultimi 30 giorni).</li>
  <li><b>Gate MIR-LST &ge; 8K</b>: solo i pixel dove la temperatura MIR e' significativamente sopra la temperatura della superficie LST (da LSA-007) passano. Evita falsi positivi su superfici naturalmente calde (rocce, sabbia).</li>
  <li><b>Esclusione anomalie persistenti</b>: pixel dentro il <a href="/falsi-positivi">catalogo dei falsi positivi</a> (Etna, raffinerie, serre, parchi solari) vengono rifiutati. Floor MIR &ge; 305K per frame senza baseline.</li>
  <li><b>Cluster spaziale</b>: pixel adiacenti diventano un singolo evento incendio.</li>
  <li><b>Sub-pixel localization</b>: NHI-SWIR + algoritmo Dozier per localizzare il fronte di fuoco dentro il pixel da 2 km.</li>
  <li><b>Multi-signal confidence</b>: punteggio finale pesato di (LST-z, NHI-SWIR, Dozier, sigma spaziale, fulmini recenti, FWI, persistenza temporale).</li>
  <li><b>Pubblicazione</b>: se confidence &ge; 0.5, il rilevamento viene scritto su <code>internal_fires</code> e appare sulla mappa.</li>
  <li><b>Verifica a posteriori</b>: dopo 24-72 ore, Sentinel-2 NBR conferma o smentisce la cicatrice di incendio.</li>
</ol>

<h2>6. Il nostro catalogo di falsi positivi</h2>
<p>I sensori termici da satellite vedono molte sorgenti di calore che NON sono incendi: vulcani (Etna, Stromboli), raffinerie (Augusta-Priolo, Gela, Milazzo), grandi cave, parchi solari, complessi di serre (Pachino - "la cintura del pomodoro"). PHOENIX mantiene un <a href="/falsi-positivi">catalogo pubblico open-data</a> di 18+ sorgenti note, costruito tramite mining algoritmico + classificazione automatica con visione AI (Claude Sonnet 4.5) + cross-reference OpenStreetMap. Tutti i rilevamenti dentro queste zone vengono filtrati.</p>
<p>Il catalogo e' <b>liberamente scaricabile</b> (licenza CC-BY 4.0) ed e' utile a chiunque costruisca sistemi simili in Sicilia o altrove. Stiamo per pubblicarlo su GitHub + Zenodo con DOI citabile.</p>

<h2>7. Accuratezza per feed (la pagina che ci mette in dubbio)</h2>
<p>Nessun feed e' perfetto - <b>noi inclusi</b>. La pagina <a href="/accuracy.html">Accuratezza feed</a> mostra per ogni sorgente (interna PHOENIX o esterna):</p>
<ul class="list-disc list-inside ml-4">
  <li>True positives <b>corroborati</b> (almeno un'altra sorgente ha rilevato lo stesso incendio entro 5 km / +-2h)</li>
  <li>True positives <b>verificati da Sentinel-2</b> (dNBR &gt; 0.27)</li>
  <li>False positives <b>persistenti</b> (rilevamenti dentro zone del catalogo FP)</li>
  <li>False positives <b>smentiti da Sentinel-2</b> (dNBR &lt; 0.10)</li>
  <li><b>Sole reporter</b> - rilevamenti dove questo feed e' l'unico ad aver visto qualcosa (contributo unico)</li>
  <li><b>Precisione %</b> = TP / (TP + FP)</li>
</ul>
<p>Il fatto stesso che pubblichiamo questi numeri - <b>inclusi i nostri</b> - e' l'impegno scientifico. Un feed al 44% di precisione (e ne abbiamo trovati cosi') sta facendo piu' rumore che segnale, e bisogna saperlo.</p>

<h2>8. Funzionalita' della mappa</h2>
<ul class="list-disc list-inside ml-4 space-y-1">
  <li><b>Marker colorati</b> per confidence (verde basso, arancio medio, rosso alto).</li>
  <li><b>Filtri</b> per sorgente (PHOENIX / FIRMS / EUMETSAT / FP-mask).</li>
  <li><b>Overlay opzionali</b>: FWI (rischio incendi), fulmini, frecce del vento, coni di fumo proiettati.</li>
  <li><b>Click su un rilevamento</b>: dettagli + verifica cicatrice + link a Google Maps / Street View / FIRMS.</li>
  <li><b>"Vicino a me"</b> 📍: geolocalizzazione browser + raggio 25 km.</li>
  <li><b>"Riproduci 24h"</b> ▶: animazione del raffreddamento progressivo degli ultimi rilevamenti.</li>
  <li><b>Permalink URL</b>: la posizione/zoom/time-window della mappa si salvano nell'hash URL, condivisibili.</li>
</ul>

<h2>9. Open data + come citare</h2>
<p>Tutti i dati prodotti da PHOENIX sono pubblicati con licenza <b>CC-BY 4.0</b> (dati) e <b>MIT</b> (codice). Il catalogo dei falsi positivi sara' su GitHub con DOI Zenodo. Per citare:</p>
<pre style="background:#f8fafc;padding:12px;border-radius:6px;font-size:0.85em;overflow-x:auto">Ludwikowski, M. (2026). Persistent Thermal Sources Catalog — Sicily (v1.0.0).
Zenodo. DOI: 10.5281/zenodo.20369891
https://doi.org/10.5281/zenodo.20369891
https://github.com/markl02us/persistent-thermal-sources-sicily</pre>

<h2>10. Limitazioni note (onesta'  prima di tutto)</h2>
<ul class="list-disc list-inside ml-4 space-y-1">
  <li><b>FIRMS Ultra-Real-Time NON e' disponibile in Europa</b> (solo USA/Canada). Il floor di latenza real-time NRT e' ~30 min.</li>
  <li><b>GOES-LI/GOES-FCI non vedono la Sicilia</b> (oltre il limbo del disco terrestre). Affidiamo a MTG.</li>
  <li><b>Sentinel-2 ha 5 giorni di revisita</b>. La verifica cicatrice puo' richiedere fino a 7 giorni in caso di nuvolosita'.</li>
  <li><b>Non sostituiamo i servizi operativi</b> (DPC, Vigili del Fuoco, Corpo Forestale, Protezione Civile Regionale Sicilia). Integriamo. Se vedi un incendio chiama il 115.</li>
  <li>Il nostro detector sperimentale <code>subpixel_v1_alpha</code> e' in tuning attivo - alcuni dei suoi rilevamenti scadono senza corroborazione esterna; stiamo analizzando se sono falsi positivi o fuochi piu' piccoli del limite di FIRMS.</li>
</ul>

<h2>11. Crediti</h2>
<p>Costruito da Mark L. (ricerca indipendente, non commerciale) con dati di: <b>EUMETSAT</b> (MTG, SEVIRI, MTG-LI, SLSTR), <b>NASA FIRMS</b> (VIIRS, MODIS), <b>Copernicus</b> (EFFIS, CEMS, Sentinel-2/-3/-5P), <b>ESA</b> (WorldCover), <b>EEA/ARPA Sicilia</b> (qualita' dell'aria), <b>Microsoft Planetary Computer</b> (STAC API), <b>ANSA</b>, <b>Corpo Nazionale Vigili del Fuoco</b>, <b>Giornale di Sicilia</b> (segnalazioni cronaca), <b>Open-Meteo</b> (meteo + air quality CAMS), <b>OpenStreetMap + Esri</b> (basemap). Framework open-source: Leaflet, Tailwind, Flask, satpy, rasterio, eumdac. <b>Grazie a tutti.</b></p>

<p class="footnote mt-12 mb-4 border-t pt-4">PHOENIX e' un progetto sperimentale accademico e non commerciale. Versione 2 - aggiornata 2026-05-24. Contatto + segnalazioni: tramite la <a href="https://github.com/markl02us/persistent-thermal-sources-sicily">repository GitHub del catalogo FP</a>.</p>

</div>

<div class="lang-en">
<h1>How PHOENIX works</h1>

<div class="emergency">
  <div class="num">115</div>
  <div>
    <div style="font-size:1.05em">See a fire? Call 115 (Italian Fire Brigade) immediately.</div>
    <div style="font-size:0.85em;opacity:0.95;font-weight:400">PHOENIX is a research tool - NOT a replacement for emergency services. Your timely report saves lives.</div>
  </div>
</div>

<p class="text-stone-600 text-lg">Experimental, open-source, academic, non-commercial wildfire detection for Sicily. Multi-sensor, multi-source, fully transparent.</p>

<h2>1. Mission</h2>
<p>PHOENIX aims to detect wildfires in Sicily <b>faster</b> than today's operational services (NASA FIRMS, EUMETSAT MTG-AF-L2, Copernicus EFFIS, Sentinel-3 SLSTR, ...). We combine geostationary + polar satellite data, human reports (newspapers, fire-brigade, social), and independent burn-scar verification via Sentinel-2.</p>
<p>The goal is not to compete - it is to <b>augment the global ecosystem</b> of wildfire detection, contribute free data and algorithms, and shrink the gap between ignition and first response. <a href="/wins.html">We celebrate every detector's wins</a>, not just ours.</p>

<h2>2. What YOU can do</h2>
<blockquote>
  <b>Seeing a fire now?</b> Call <b>115</b> (Fire Brigade) or <b>1515</b> (Forest Corps).<br>
  <b>Saw one yesterday/today?</b> Search the coordinate on our <a href="/">map</a>. If we missed it, file an issue on GitHub.<br>
  <b>Journalist/citizen with photos?</b> Georeferenced images help verification - share with #incendiosicilia.<br>
  <b>Researcher?</b> All data CC-BY 4.0. See section 8 for citation.
</blockquote>

<h2>3. Two clocks</h2>
<p>When a satellite sees a fire, two distinct times exist:</p>
<ul class="list-disc list-inside ml-4">
  <li><b>Sensor-acquisition time</b> - when the satellite sensor actually captured the burning pixel.</li>
  <li><b>Feed-delivery time</b> - when the processed data became available to us via their public service.</li>
</ul>
<p>The difference is the <b>reporting latency</b> - downlink, processing, publish. PHOENIX processes raw data locally so we have a structural "proximity" advantage, but we are honest about this: algorithmic comparison should be <b>against sensor-acquisition time</b>, not feed-delivery time. We want to be the best detector, not just closest to the data.</p>

<h2>4-11. (Italian version above has full data-source cards + methodology + accuracy + map features + open data + limitations + credits.)</h2>
<p>15+ data feeds wired in. <a href="/accuracy.html">Live accuracy scoreboard</a>. <a href="/falsi-positivi">Open FP catalog</a>. Methodology mirrors the Italian narrative above. Toggle EN/IT in nav.</p>
</div>

<script>
fetch('/api/feed_accuracy').then(r=>r.json()).then(d=>{
  document.getElementById('s-sources').textContent = d.sources?.length || '-';
  const total = (d.sources||[]).reduce((a,s)=>a+s.total,0);
  document.getElementById('s-detections').textContent = total.toLocaleString();
  const phx = (d.sources||[]).filter(s=>s.kind==='internal');
  const tp = phx.reduce((a,s)=>a+s.tp,0);
  const fp = phx.reduce((a,s)=>a+s.fp,0);
  const prec = (tp+fp) > 0 ? ((tp/(tp+fp))*100).toFixed(0) + '%' : '-';
  document.getElementById('s-precision').textContent = prec;
});
fetch('/wins').then(r=>r.json()).then(d=>{
  const total = (d.count || 0) + (d.external_only_count || 0);
  document.getElementById('s-wins').textContent = total;
});
fetch('/api/false_positive_zones').then(r=>r.json()).then(d=>{
  document.getElementById('s-fp').textContent = (d.sources||[]).length;
});
</script>

</body></html>
"""


@app.route('/come-funziona')
def come_funziona():
    from flask import Response
    return Response(_COME_FUNZIONA_HTML, mimetype='text/html')


@app.route('/falsi-positivi')
def phoenix_false_positives_page():
    from flask import render_template
    return render_template('v2/falsi-positivi.html')

@app.route('/')
def index():
    from flask import render_template
    return render_template("v2/index.html")

def _enrich_with_confirmation(det):
    """For one detection, look in external_fires for any confirming source.
    A confirmation = external hit within 5 km AND sensed AFTER this detection.
    Adds 'confirmation' dict to the detection if confirmed."""
    try:
        import sqlite3, math
        lat = float(det.get('lat', 0.0) or 0.0)
        lng = float(det.get('lon', det.get('lng', 0.0)) or 0.0)
        det_ts = det.get('timestamp', '')
        if not det_ts:
            return det
        # Bounding box ~0.06° around the detection
        gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
        con = sqlite3.connect(str(gt_db))
        rows = list(con.execute(
            "SELECT source, lat, lng, ts FROM external_fires "
            "WHERE ts > ? AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
            (det_ts, lat - 0.06, lat + 0.06, lng - 0.06, lng + 0.06)
        ))
        con.close()
        best = None
        det_t = datetime.fromisoformat(det_ts.replace('Z', '+00:00') if det_ts.endswith('Z') else det_ts)
        if det_t.tzinfo is None:
            det_t = det_t.replace(tzinfo=timezone.utc)
        for src, elat, elng, ets in rows:
            R = 6371.0
            dlat = math.radians(elat - lat); dlon = math.radians(elng - lng)
            s = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(elat)) * math.sin(dlon/2)**2
            km = 2 * R * math.asin(math.sqrt(s))
            if km > 5.0:
                continue
            try:
                ext_t = datetime.fromisoformat(ets.replace('Z', '+00:00') if ets.endswith('Z') else ets)
                if ext_t.tzinfo is None:
                    ext_t = ext_t.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            lead_min = (ext_t - det_t).total_seconds() / 60.0
            if lead_min <= 0:
                continue
            if best is None or lead_min > best['lead_min']:
                best = {
                    'confirmed_by': src,
                    'comparator_sensed_at': ets,
                    'km': round(km, 2),
                    'lead_min': round(lead_min, 1),
                }
        if best is not None:
            det = dict(det)   # don't mutate the shared in-memory list
            det['confirmation'] = best
    except Exception as exc:
        logger.warning("_enrich_with_confirmation error: %s", exc)
    return det



def _fp_mask_zones():
    """Load the persistent-FP catalog for filtering comparator hits."""
    import os, json as _json
    p = "/home/mark/phoenix_false_positives/sources.json"
    if not os.path.exists(p):
        return []
    try:
        d = _json.loads(open(p).read())
        return [(s["lat"], s["lon"], s["radius_km"]) for s in d.get("sources", [])]
    except Exception:
        return []


def _is_in_fp_zone(lat, lon, zones):
    import math
    for slat, slon, rkm in zones:
        dlat = (lat - slat) * 111.0
        dlon = (lon - slon) * 111.0 * math.cos(math.radians(slat))
        if (dlat*dlat + dlon*dlon) ** 0.5 <= rkm:
            return True
    return False


@app.route('/api/detections')
def get_detections():
    """Read the last 7 days of detections from SQLite internal_fires.

    Previously this used the in-memory `detections_list` which resets on every
    service restart — that's why the map appeared empty after each deploy.
    DB-backed = survives restarts. Reformulated 2026-05-22."""
    import sqlite3, math, json as _json
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    cutoff = (datetime.now(timezone.utc) - timedelta(days=effis_retention_days)).isoformat()
    day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    detections_out = []
    externals_raw = []
    try:
        con = sqlite3.connect(str(gt_db))
        rows = list(con.execute(
            "SELECT id, source, aoi_id, lat, lng, ts, confidence, frp_mw, "
            "temperature_c, raw_json FROM internal_fires "
            "WHERE ts > ? "
            "AND (raw_json IS NULL OR raw_json NOT LIKE '%expired%') "
            "ORDER BY ts DESC LIMIT 500",
            (cutoff,)
        ))
        externals_raw = list(con.execute(
            "SELECT source, lat, lng, ts FROM external_fires WHERE ts > ?",
            (cutoff,)
        ))
        con.close()
    except Exception as exc:
        logger.error("get_detections DB read failed: %s", exc)
        rows = []

    def _parse_t(s):
        try:
            t = datetime.fromisoformat(s.replace('Z', '+00:00') if s.endswith('Z') else s)
            if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
            return t
        except Exception:
            return None

    parsed_ext = []
    for esrc, elat, elng, ets in externals_raw:
        et = _parse_t(ets)
        if et is None: continue
        parsed_ext.append((esrc, float(elat), float(elng), et, ets))

    def _confirm(lat, lng, det_t):
        best = None
        for esrc, elat, elng, et, ets in parsed_ext:
            if et <= det_t: continue
            R = 6371.0
            dlat = math.radians(elat - lat); dlon = math.radians(elng - lng)
            s = math.sin(dlat/2)**2 + math.cos(math.radians(lat)) * math.cos(math.radians(elat)) * math.sin(dlon/2)**2
            km = 2 * R * math.asin(math.sqrt(s))
            if km > 5.0: continue
            lead_min = (et - det_t).total_seconds() / 60.0
            if best is None or lead_min > best['lead_min']:
                best = {'confirmed_by': esrc, 'comparator_sensed_at': ets,
                        'km': round(km, 2), 'lead_min': round(lead_min, 1)}
        return best

    confirmed_count = 0
    adr_count = 0
    recent_24h = 0
    for det_id, src, aoi, lat, lng, ts, conf, frp, temp, raw in rows:
        det_t = _parse_t(ts)
        if det_t is None: continue
        image_url = None
        if raw:
            try:
                rd = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(rd, dict):
                    image_url = rd.get('image_url')
            except Exception:
                pass
        det = {
            'id': det_id,
            'source': src or 'unk',
            'aoi_id': aoi,
            'lat': float(lat),
            'lon': float(lng),
            'lng': float(lng),
            'timestamp': ts,
            'confidence': float(conf or 0.0),
            'frp_mw': float(frp or 0.0),
            'fire_temperature_c': float(temp or 0.0),
            'image_url': image_url,
        }
        m = _confirm(det['lat'], det['lon'], det_t)
        if m is not None:
            det['confirmation'] = m
            confirmed_count += 1
        if src in ('dgx', 'enhanced', 'fci_l1c', 'subpixel_v1_alpha'):
            adr_count += 1
        if ts > day_ago:
            recent_24h += 1
        detections_out.append(det)

    active_detections = detections_out
    enriched = detections_out
    effis_count = 0
    if False:   # legacy code below kept to satisfy old references
        latest = max([{'timestamp': ''}], key=lambda x: x['timestamp'])
        last_effis = datetime.fromisoformat(latest['timestamp']).strftime('%H:%M')
    else:
        last_effis = 'None'
    system_health = "OPTIMAL"
    if adr_count > 50:
        system_health = "HIGH ACTIVITY"
    elif len(active_detections) == 0:
        system_health = "STANDBY"
    return jsonify({
        'count': len(active_detections),
        'effis_count': effis_count,
        'adr_count': adr_count,
        'confirmed_count': confirmed_count,
        'recent_24h': recent_24h,
        'last_effis_update': last_effis,
        'detections': enriched,
        'system_health': system_health,
        'system_status': 'OPERATIONAL'
    })


@app.route('/api/detection-crop/<path:filename>')
def detection_crop(filename: str):
    """Serve the thermal-crop PNG for a specific detection's popup."""
    from flask import abort, send_from_directory
    from src.detectors.detection_imagery import CROPS_DIR
    # send_from_directory handles path traversal safely (filename must be relative)
    if "/" in filename or ".." in filename:
        return abort(400)
    full = CROPS_DIR / filename
    if not full.exists():
        return abort(404)
    return send_from_directory(str(CROPS_DIR), filename, mimetype="image/png")


@app.route('/api/system_info')
def get_system_info():
    cpu_percent = psutil.cpu_percent(interval=1)
    ram_percent = psutil.virtual_memory().percent
    gpu_percent = 0
    gpu_memory  = 0
    try:
        gpus = GPUtil.getGPUs()
        if gpus:
            gpu = gpus[0]
            gpu_percent = int(gpu.load * 100)
            gpu_memory  = int(gpu.memoryUtil * 100)
    except Exception:
        pass
    return jsonify({
        'cpu_util': int(cpu_percent),
        'ram_util': int(ram_percent),
        'gpu_util': gpu_percent,
        'gpu_memory': gpu_memory,
        'timestamp': datetime.now().isoformat()
    })


def _ml_apply_filter(detection: Dict,
                     pixel_window=None,
                     bypass_ml: bool = False) -> Optional[Dict]:
    """
    Run the PhoenixClassifier on a candidate detection.

    Args
    ----
    detection    : dict from Dozier / enhanced / sub-pixel detector.
    pixel_window : surrounding BT pixel array (H,W) or (H,W,2); None = synthetic.
    bypass_ml    : if True the detection always passes (used for EFFIS ground-truth).

    Returns
    -------
    Annotated detection dict with ml_* telemetry added, or None if suppressed.
    ml_decision is one of: "published" | "provisional" | "suppressed" | "bypassed"
    """
    # EFFIS ground-truth bypass
    if bypass_ml or detection.get('source') == 'effis':
        detection.update({
            'ml_confidence':    1.0,
            'ml_decision':      'bypassed',
            'raw_features_used': {},
        })
        return detection

    if _ml_classifier is None:
        # ML not available — pass through with neutral annotation
        detection.update({
            'ml_confidence':    0.5,
            'ml_decision':      'ml_unavailable',
            'raw_features_used': {},
        })
        return detection

    result = _ml_classifier.score(detection, pixel_window)
    conf   = result['confidence']
    thresh = _ml_threshold_for(float(detection.get('lat', 37.4)), float(detection.get('lon', 13.5)))

    detection.update({
        'ml_confidence':     conf,
        'ml_decision':       '',
        'raw_features_used': result.get('raw_features_used', {}),
        'ml_method':         result.get('method', 'unknown'),
    })

    if conf >= thresh:
        detection['ml_decision'] = 'published'
        detection['status'] = 'published'
        logger.debug("ML accept (%.2f >= %.2f) lat=%.4f lon=%.4f",
                     conf, thresh, detection.get('lat', 0), detection.get('lon', 0))
        return detection
    elif conf >= 0.3:
        detection['ml_decision'] = 'provisional'
        detection['status'] = 'provisional'
        logger.info("ML provisional (%.2f) lat=%.4f lon=%.4f — storing without alert",
                    conf, detection.get('lat', 0), detection.get('lon', 0))
        return detection   # stored but not alerted (callers check status)
    else:
        detection['ml_decision'] = 'suppressed'
        logger.info("ML suppressed (%.2f) lat=%.4f lon=%.4f",
                    conf, detection.get('lat', 0), detection.get('lon', 0))
        return None




_FEED_ACCURACY_CACHE = {"ts": 0, "data": None}
_FEED_ACCURACY_TTL_SEC = 60


def _support_feeds_query(con_db_path: str, lookback_days: int = 30) -> list:
    """Return per-source row counts for tables that track coverage rather than
    detection events. Used by /accuracy.html to show e.g. TROPOMI/MOD11/SAR
    swath coverage alongside the main detector precision table.
    """
    import sqlite3 as _sql
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    cutoff_iso = (_dt.now(_tz.utc) - _td(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%S")
    con = _sql.connect(con_db_path)
    out = []
    for tbl, src_col, ts_col, kind_label in [
        ("smoke_proxies", "source", "ts", "satellite_swath"),
        ("corroboration_signals", "source", "ts", "satellite_swath"),
        ("smoke_corroboration", "gas", "ts", "plume_match"),
        ("weather_obs", "aoi", "ts", "weather_grid"),
        ("air_obs", "aoi", "ts", "air_quality_grid"),
    ]:
        try:
            rows = list(con.execute(
                f"SELECT {src_col} AS src, COUNT(*) AS n, MAX({ts_col}) AS last_ts "
                f"FROM {tbl} WHERE {ts_col} > ? GROUP BY {src_col} ORDER BY n DESC",
                (cutoff_iso,)
            ))
            for src, n, last_ts in rows:
                out.append({
                    "table": tbl, "source": str(src) if src else "(unknown)",
                    "kind": kind_label, "rows_30d": int(n), "last_ts": last_ts,
                })
        except Exception:
            continue
    con.close()
    return out


@app.route('/api/feed_accuracy')
def api_feed_accuracy():
    """Per-source accuracy metrics, last 30 days.

    Classification rules per detection:
      - persistent_fp:    inside a known persistent thermal anomaly zone
                          (volcano, refinery, glasshouse complex, solar farm, etc.)
      - burn_verified_tp: Sentinel-2 NBR confirmed a real burn (dNBR > 0.27)
      - burn_disproven_fp: Sentinel-2 NBR confirmed no scar (dNBR < 0.10)
      - corroborated_tp:  any other source detected within 5 km / +-2 h
      - unconfirmed:      none of the above (pending verification)

    precision_pct = (corroborated_tp + burn_verified_tp) /
                    (corroborated_tp + burn_verified_tp + persistent_fp + burn_disproven_fp)
    sole_reporter = count of detections where this source was the ONLY one within
                    5 km / +-2 h (these are the unique contributions).
    """
    import sqlite3, math, json as _json, time as _time
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    now_ts = _time.time()
    if (_FEED_ACCURACY_CACHE["data"] is not None
            and now_ts - _FEED_ACCURACY_CACHE["ts"] < _FEED_ACCURACY_TTL_SEC):
        return jsonify(_FEED_ACCURACY_CACHE["data"])

    LOOKBACK_DAYS = 30
    MATCH_KM = 5.0
    MATCH_MIN = 120

    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
                  ).strftime("%Y-%m-%dT%H:%M:%S")
    internals = list(con.execute(
        "SELECT source, lat, lng, ts, raw_json FROM internal_fires "
        "WHERE ts > ? AND (raw_json IS NULL OR raw_json NOT LIKE '%expired%')",
        (cutoff_iso,)
    ))
    externals = list(con.execute(
        "SELECT source, lat, lng, ts FROM external_fires WHERE ts > ?",
        (cutoff_iso,)
    ))
    con.close()

    def _pt(s):
        if not s: return None
        s = s.replace('Z', '+00:00') if s.endswith('Z') else s
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _km(a_lat, a_lon, b_lat, b_lon):
        R = 6371.0
        dlat = math.radians(b_lat - a_lat); dlon = math.radians(b_lon - a_lon)
        s = math.sin(dlat/2)**2 + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(s))

    # Build unified detection list
    all_det = []
    for src, lat, lng, ts, raw in internals:
        dt = _pt(ts)
        if dt is None: continue
        all_det.append(('internal', src, float(lat), float(lng), dt, raw))
    for src, lat, lng, ts in externals:
        dt = _pt(ts)
        if dt is None: continue
        all_det.append(('external', src, float(lat), float(lng), dt, None))

    # Hour-bin index for O(n*k) match instead of O(n^2)
    EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
    hour_bins = defaultdict(list)
    for idx, (kind, src, lat, lng, dt, raw) in enumerate(all_det):
        h = int((dt - EPOCH).total_seconds() // 3600)
        hour_bins[h].append(idx)

    # Per-source counters
    by_source = defaultdict(lambda: {
        'kind': '', 'total': 0,
        'corroborated_tp': 0, 'burn_verified_tp': 0,
        'persistent_fp': 0, 'burn_disproven_fp': 0,
        'unconfirmed': 0, 'sole_reporter': 0,
    })

    for i, (kind, src, lat, lng, dt, raw) in enumerate(all_det):
        rec = by_source[src]
        rec['kind'] = kind
        rec['total'] += 1

        # 1. Persistent FP zone?
        if _is_known_anomaly(lat, lng):
            rec['persistent_fp'] += 1
            continue

        # 2. Burn-scar verification (internal only)
        bv_class = None
        if raw:
            try:
                rd = _json.loads(raw)
                bv = rd.get('burn_verification', {}) if isinstance(rd, dict) else {}
                vb = bv.get('verified_burn')
                if vb is True:
                    bv_class = 'burn_verified_tp'
                elif vb is False:
                    bv_class = 'burn_disproven_fp'
            except Exception:
                pass
        if bv_class:
            rec[bv_class] += 1
            continue

        # 3. Cross-source corroboration (check own and adjacent hour bins)
        h = int((dt - EPOCH).total_seconds() // 3600)
        match = False
        other_present = False
        for h2 in range(h - 2, h + 3):
            for j in hour_bins.get(h2, ()):
                if j == i:
                    continue
                kind2, src2, lat2, lng2, dt2, _ = all_det[j]
                if abs((dt - dt2).total_seconds()) > MATCH_MIN * 60:
                    continue
                if _km(lat, lng, lat2, lng2) > MATCH_KM:
                    continue
                other_present = True
                if src2 != src:
                    match = True
                    break
            if match:
                break
        if match:
            rec['corroborated_tp'] += 1
        else:
            rec['unconfirmed'] += 1
            if not other_present:
                rec['sole_reporter'] += 1

    out = []
    for src, rec in by_source.items():
        tp = rec['corroborated_tp'] + rec['burn_verified_tp']
        fp = rec['persistent_fp'] + rec['burn_disproven_fp']
        denom = tp + fp
        rec['tp'] = tp
        rec['fp'] = fp
        rec['precision_pct'] = round(100.0 * tp / denom, 1) if denom > 0 else None
        rec['source'] = src
        out.append(rec)

    out.sort(key=lambda r: (r['kind'] != 'internal', -r['total']))

    # PHOENIX 2026-05-24 — support feeds (coverage indicators, not detections)
    support = _support_feeds_query(con_db_path=str(gt_db),
                                    lookback_days=LOOKBACK_DAYS)

    summary = {
        'sources': out,
        'lookback_days': LOOKBACK_DAYS,
        'match_km': MATCH_KM,
        'match_min': MATCH_MIN,
        'computed_at': datetime.now(timezone.utc).isoformat(),
        'support_feeds': support,
        'note': ('Precision = (corroborated + burn-verified) / (TPs + FPs). '
                 'Unconfirmed detections are NOT in the denominator. '
                 'Sole-reporter = detection with no other source within 5 km / +-2 h '
                 '(these are the unique signal contributions of a feed).'),
    }
    _FEED_ACCURACY_CACHE["ts"] = now_ts
    _FEED_ACCURACY_CACHE["data"] = summary
    return jsonify(summary)


_ACCURACY_HTML = """<!DOCTYPE html>
<html><head>
<title>Feed Accuracy - PHOENIX scientific scoreboard</title>
<style>
  body{font-family:Segoe UI,sans-serif;background:#1a2530;color:#ecf0f1;padding:20px;margin:0}
  h1{color:#3498db;margin-bottom:6px}
  h1 .badge{font-size:.5em;background:#3498db;color:#1a2530;padding:3px 10px;border-radius:14px;vertical-align:middle;margin-left:8px}
  .sub{color:#bdc3c7;margin-bottom:18px;max-width:980px}
  a{color:#3498db;text-decoration:none}
  a:hover{text-decoration:underline}
  table{border-collapse:collapse;width:100%;margin-top:16px;font-size:.9em}
  th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #34495e}
  th{background:#2c3e50;color:#f39c12;text-align:center;position:sticky;top:0}
  td.source{text-align:left;font-weight:bold;color:#ecf0f1}
  td.internal{color:#2ecc71}
  td.external{color:#3498db}
  td.precision-high{color:#2ecc71;font-weight:bold}
  td.precision-med{color:#f39c12;font-weight:bold}
  td.precision-low{color:#e74c3c;font-weight:bold}
  td.precision-none{color:#7f8c8d}
  .group-header{background:#22313e;font-size:1em;color:#f39c12;font-weight:bold;text-align:left;padding:12px 10px;border-top:2px solid #34495e}
  .legend{font-size:.85em;color:#bdc3c7;margin:20px 0;line-height:1.6}
  .legend code{background:#2c3e50;padding:2px 6px;border-radius:3px;color:#ecf0f1}
</style></head><body>
<h1>Feed Accuracy <span class="badge">scientific scoreboard</span></h1>
<p class="sub">
  Per-source accuracy metrics over the last 30 days. Each row is a feed
  contributing into PHOENIX - either our own internal sub-detectors (green) or
  external comparators (blue). We classify every detection as a confirmed
  true-positive, a known false-positive, or unconfirmed, then compute precision.
  No feed is excluded - we hold ourselves to the same standard as every
  external source.<br>
  <a href="/">&larr; Live map</a> &nbsp;&middot;&nbsp; <a href="/wins.html">Confirmed wins</a> &nbsp;&middot;&nbsp; <a href="/falsi-positivi">FP catalog</a> &nbsp;&middot;&nbsp; <a href="/api/feed_accuracy">JSON</a>
</p>

<div class="legend">
  <b>Definitions:</b><br>
  <code>corroborated_tp</code> &mdash; another source independently detected the same fire within 5 km / &plusmn;2 h<br>
  <code>burn_verified_tp</code> &mdash; Sentinel-2 burn-scar verification confirmed a real burn (dNBR &gt; 0.27)<br>
  <code>persistent_fp</code> &mdash; detection landed inside a known persistent thermal anomaly (volcano, refinery, glasshouse, solar farm, quarry)<br>
  <code>burn_disproven_fp</code> &mdash; Sentinel-2 burn-scar verification confirmed NO real burn (dNBR &lt; 0.10)<br>
  <code>unconfirmed</code> &mdash; no other source, no burn verification yet (pending - could become TP or FP)<br>
  <code>sole_reporter</code> &mdash; detection where NO other source flagged anything within 5 km / &plusmn;2 h (this feed's unique catches)<br>
  <code>precision_%</code> &mdash; TP &divide; (TP + FP). Unconfirmed not in denominator.
</div>

<div id="support-feeds-section" style="margin-top:30px"></div>

<div id="per-aoi-section" style="margin-top:30px"></div>

<div id="totals" style="color:#bdc3c7;margin-bottom:10px;font-size:.9em"></div>
<table id="acc-table">
  <thead>
    <tr>
      <th style="text-align:left">Source</th>
      <th>Total</th>
      <th>Corroborated TP</th>
      <th>Burn-verified TP</th>
      <th>Persistent FP</th>
      <th>Burn-disproven FP</th>
      <th>Unconfirmed</th>
      <th>Sole reporter</th>
      <th>Precision %</th>
    </tr>
  </thead>
  <tbody id="acc-body"></tbody>
</table>

<script>
function precClass(p){
  if(p === null || p === undefined) return 'precision-none';
  if(p >= 80) return 'precision-high';
  if(p >= 50) return 'precision-med';
  return 'precision-low';
}
function precFmt(p){
  if(p === null || p === undefined) return '--';
  return p.toFixed(1) + '%';
}
fetch('/api/feed_accuracy').then(r=>r.json()).then(d=>{
  // Render support-feeds section
  const sf = document.getElementById('support-feeds-section');
  const support = d.support_feeds || [];
  if (sf && support.length) {
    let html = '<h2 style="color:#3498db;margin-top:24px">Support feeds <span style="font-size:.6em;color:#95a5a6">coverage indicators (not detection events)</span></h2>';
    html += '<p class="legend">These feeds provide contextual support — satellite swath coverage (TROPOMI, MOD11, SAR), weather, air quality, plume→fire matches — rather than fire detections proper. They feed the multi-signal confidence scoring.</p>';
    html += '<table><thead><tr><th style="text-align:left">Table</th><th style="text-align:left">Source</th><th style="text-align:left">Kind</th><th>Rows (30d)</th><th>Last ts</th></tr></thead><tbody>';
    support.forEach(s => {
      html += `<tr><td style="text-align:left;color:#bdc3c7">${s.table}</td><td style="text-align:left">${s.source}</td><td style="text-align:left;color:#7f8c8d">${s.kind}</td><td>${s.rows_30d.toLocaleString()}</td><td style="text-align:left;font-size:.85em;color:#95a5a6">${s.last_ts || '-'}</td></tr>`;
    });
    html += '</tbody></table>';
    sf.innerHTML = html;
  }
  const body = document.getElementById('acc-body');
  const totals = document.getElementById('totals');
  const sources = d.sources || [];
  const internalCount = sources.filter(s => s.kind === 'internal').length;
  const externalCount = sources.filter(s => s.kind === 'external').length;
  const totalDet = sources.reduce((a,s) => a + s.total, 0);
  totals.innerHTML = `Lookback: <b>${d.lookback_days}d</b> &middot; ${internalCount} internal sources + ${externalCount} external sources &middot; ${totalDet.toLocaleString()} total detections &middot; match window: ${d.match_km}km / ${d.match_min}min`;

  let html = '';
  let prevKind = null;
  sources.forEach(s => {
    if(s.kind !== prevKind){
      const label = s.kind === 'internal' ? 'PHOENIX internal sub-detectors (our own accuracy)' : 'External comparator feeds';
      html += `<tr><td class="group-header" colspan="9">${label}</td></tr>`;
      prevKind = s.kind;
    }
    html += `<tr>
      <td class="source ${s.kind}">${s.source}</td>
      <td>${s.total.toLocaleString()}</td>
      <td style="color:#2ecc71">${s.corroborated_tp.toLocaleString()}</td>
      <td style="color:#27ae60">${s.burn_verified_tp.toLocaleString()}</td>
      <td style="color:#e74c3c">${s.persistent_fp.toLocaleString()}</td>
      <td style="color:#c0392b">${s.burn_disproven_fp.toLocaleString()}</td>
      <td style="color:#7f8c8d">${s.unconfirmed.toLocaleString()}</td>
      <td style="color:#f39c12">${s.sole_reporter.toLocaleString()}</td>
      <td class="${precClass(s.precision_pct)}">${precFmt(s.precision_pct)}</td>
    </tr>`;
  });
  body.innerHTML = html;
});

// Per-AOI accuracy breakdown
fetch('/api/feed_accuracy_by_aoi').then(r=>r.json()).then(d=>{
  const sec = document.getElementById('per-aoi-section');
  const rows = d.rows || [];
  if (!sec || !rows.length) return;
  let html = '<h2 style="color:#3498db;margin-top:24px">Per-AOI accuracy breakdown <span style="font-size:.6em;color:#95a5a6">geographic distribution of precision (30d)</span></h2>';
  html += '<p class="legend">Same metrics as the main table, broken down by Sicilian sub-region. Helps identify geographic blind spots: a source might be 100% precise in agrigento but spraying in sicily_full where industrial sites cluster.</p>';
  html += '<table><thead><tr><th style="text-align:left">AOI</th><th style="text-align:left">Source</th><th style="text-align:left">Kind</th><th>Total</th><th>TP</th><th>FP</th><th>Unconfirmed</th><th>Precision %</th></tr></thead><tbody>';
  let prevAoi = null;
  rows.forEach(r => {
    if (r.aoi !== prevAoi) {
      html += `<tr><td colspan="8" style="background:#22313e;color:#f39c12;font-weight:bold;padding:8px;text-align:left">${r.aoi}</td></tr>`;
      prevAoi = r.aoi;
    }
    const p = r.precision_pct;
    const cls = p == null ? 'precision-none' : (p >= 80 ? 'precision-high' : p >= 50 ? 'precision-med' : 'precision-low');
    const pfmt = p == null ? '--' : p.toFixed(1) + '%';
    html += `<tr>
      <td style="text-align:left;color:#7f8c8d">${r.aoi}</td>
      <td style="text-align:left">${r.source}</td>
      <td style="text-align:left" class="${r.kind}">${r.kind}</td>
      <td>${r.total.toLocaleString()}</td>
      <td style="color:#2ecc71">${r.tp}</td>
      <td style="color:#e74c3c">${r.fp}</td>
      <td style="color:#7f8c8d">${r.unconfirmed}</td>
      <td class="${cls}">${pfmt}</td>
    </tr>`;
  });
  html += '</tbody></table>';
  sec.innerHTML = html;
});

// Active-learning warning banner (top of page)
fetch('/api/source_health').then(r=>r.json()).then(d=>{
  const warnings = d.warnings || [];
  if (warnings.length === 0) return;
  const banner = document.createElement('div');
  banner.style.cssText = 'background:#7c2d12;color:#fef3c7;padding:14px 18px;border-radius:6px;margin:0 0 18px 0;border-left:6px solid #f97316';
  let html = '<b>⚠️ Active-learning warning:</b> ' + warnings.length + ' source(s) below 60% precision:<ul style="margin:8px 0 0 20px">';
  warnings.forEach(w => {
    html += `<li><b>${w.source}</b> at ${w.precision_pct}% precision over ${w.total} detections (${w.persistent_fp} persistent FPs). ${w.action_suggestion}</li>`;
  });
  html += '</ul>';
  banner.innerHTML = html;
  document.body.insertBefore(banner, document.querySelector('h1'));
});
</script>
</body></html>"""


@app.route('/accuracy.html')
def accuracy_html():
    from flask import Response
    return Response(_ACCURACY_HTML, mimetype='text/html')


@app.route('/api/ignition_prior')
def api_ignition_prior():
    """Compute Hawkes ignition prior at a given (lat, lon). Optional ?fwi=N."""
    from flask import request
    try:
        lat = float(request.args.get("lat", "37.5"))
        lon = float(request.args.get("lon", "14.0"))
    except (TypeError, ValueError):
        return jsonify({"error": "bad lat/lon"}), 400
    fwi = request.args.get("fwi")
    try:
        fwi = float(fwi) if fwi is not None else None
    except ValueError:
        fwi = None
    try:
        from src.data_sources.lightning_li import compute_ignition_prior
        out = compute_ignition_prior(lat, lon, fwi=fwi)
        out["lat"] = lat; out["lon"] = lon
        # Hawkes augmentation (PHOENIX 2026-05-24)
        try:
            from src.data_sources.hawkes_ignition import lookup_hawkes_prior
            out["hawkes"] = lookup_hawkes_prior(lat, lon)
        except Exception as _hex:
            out["hawkes"] = {"available": False, "reason": str(_hex)}
        return jsonify(out)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


_SWAGGER_HTML = """<!DOCTYPE html>
<html><head>
<title>PHOENIX API Docs</title>
<meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head><body>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
window.onload = () => {
  SwaggerUIBundle({
    url: "/api/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    layout: "BaseLayout",
  });
};
</script>
</body></html>"""


@app.route('/api/docs.html')
def api_docs_html():
    from flask import Response
    return Response(_SWAGGER_HTML, mimetype='text/html')


@app.route('/api/wins.csv')
def api_wins_csv():
    """CSV export of /wins for researchers (direct in-process call to wins_json)."""
    import csv, io
    from flask import Response
    try:
        resp = wins_json()
        data = resp.get_json()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    buf = io.StringIO()
    cols = ["phoenix_ts", "phoenix_source", "ext_source", "comparator_sensed_at",
            "comparator_reported_at", "reporting_latency_min",
            "lead_min_vs_sensed", "lead_min_vs_reported",
            "lat", "lng", "aoi_id", "confidence", "frp_mw", "fused_frp_mw",
            "sensors_independent", "member_count", "km"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for ev in (data.get("wins") or []):
        w.writerow(ev)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=phoenix_wins.csv"})


@app.route('/api/feed_accuracy.csv')
def api_feed_accuracy_csv():
    """CSV export of per-feed accuracy table for researchers (direct call)."""
    import csv, io
    from flask import Response
    try:
        resp = api_feed_accuracy()
        data = resp.get_json()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    buf = io.StringIO()
    cols = ["source", "kind", "total", "tp", "fp", "corroborated_tp", "burn_verified_tp",
            "persistent_fp", "burn_disproven_fp", "unconfirmed", "sole_reporter", "precision_pct"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for s in (data.get("sources") or []):
        w.writerow(s)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=phoenix_feed_accuracy.csv"})


@app.route('/api/false_positive_zones.geojson')
def api_fp_geojson():
    """GeoJSON export of the FP catalog for QGIS / Leaflet / etc."""
    import json as _json
    from flask import Response
    try:
        resp = api_false_positive_zones()
        data = resp.get_json()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    features = []
    for s in (data.get("sources") or []):
        try:
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s.get("lon"), s.get("lat")]},
                "properties": {
                    "id": s.get("id"),
                    "category": s.get("category"),
                    "subcategory": s.get("subcategory"),
                    "name_en": s.get("name_en"),
                    "name_it": s.get("name_it"),
                    "radius_km": s.get("radius_km"),
                    "google_maps_url": s.get("google_maps_url"),
                },
            })
        except Exception:
            continue
    fc = {"type": "FeatureCollection", "features": features}
    return Response(_json.dumps(fc), mimetype="application/geo+json",
                    headers={"Content-Disposition": "attachment; filename=phoenix_fp_catalog.geojson"})


@app.route('/api/per_aoi_threshold_suggestion')
def api_per_aoi_threshold_suggestion():
    """Compute per-AOI ml_accept threshold suggestions based on local FP density.

    Optionally writes overrides if ?write=1 (auto-applied on next request).
    """
    from flask import request
    write = request.args.get("write", "0") == "1"
    s = recompute_per_aoi_thresholds(write=write)
    return jsonify({"suggestions": s, "written": write,
                     "override_file": str(_PER_AOI_THRESHOLD_FILE)})


@app.route('/wins.rss')
def wins_rss():
    """RSS 2.0 feed of confirmed PHOENIX wins for subscribers."""
    from flask import Response
    from datetime import datetime as _dt, timezone as _tz
    try:
        resp = wins_json()
        data = resp.get_json()
    except Exception as exc:
        return Response("<error>" + str(exc) + "</error>", mimetype="application/xml"), 500
    wins = (data.get("wins") or [])[:40]
    ext_wins = (data.get("external_only_wins") or [])[:20]

    def _esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\"", "&quot;"))

    items = []
    for w in wins:
        title = f"PHOENIX +{w.get('lead_min_vs_sensed', 0)} min lead vs {w.get('ext_source', '?')}"
        desc = (f"PHOENIX detected at {w.get('phoenix_ts', '')} - "
                f"comparator sensed at {w.get('comparator_sensed_at', '')} - "
                f"lat {w.get('lat', 0):.4f} lon {w.get('lng', 0):.4f} - "
                f"confidence {w.get('confidence', 0):.2f} - "
                f"FRP {w.get('fused_frp_mw', w.get('frp_mw', 0)):.2f} MW - "
                f"sensors agreeing: {w.get('sensors_independent', 1)}")
        items.append(f"""<item><title>{_esc(title)}</title><description>{_esc(desc)}</description><pubDate>{_esc(w.get('phoenix_ts', ''))}</pubDate><link>https://adr-wildfire.com/wins.html</link><guid isPermaLink="false">phoenix-{_esc(w.get('det_id', ''))}</guid></item>""")
    for w in ext_wins:
        title = f"External catch: {w.get('source', '?')} - PHOENIX missed"
        desc = (f"{w.get('source', '?')} detected at {w.get('sensed_at', '')} - "
                f"reported at {w.get('reported_at', '')} - "
                f"lat {w.get('lat', 0):.4f} lon {w.get('lng', 0):.4f}")
        items.append(f"""<item><title>{_esc(title)}</title><description>{_esc(desc)}</description><pubDate>{_esc(w.get('sensed_at', ''))}</pubDate><link>https://adr-wildfire.com/wins.html</link><guid isPermaLink="false">ext-{_esc(w.get('sensed_at', ''))}-{w.get('lat', 0)}</guid></item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>PHOENIX Wildfire Detection - Confirmed Wins</title>
<link>https://adr-wildfire.com/wins.html</link>
<description>Confirmed wildfire detections in Sicily by PHOENIX (and catches by other detectors PHOENIX missed). Updated continuously.</description>
<language>en</language>
<lastBuildDate>{_esc(_dt.now(_tz.utc).strftime('%a, %d %b %Y %H:%M:%S +0000'))}</lastBuildDate>
<atom:link xmlns:atom="http://www.w3.org/2005/Atom" href="https://adr-wildfire.com/wins.rss" rel="self" type="application/rss+xml"/>
{chr(10).join(items)}
</channel></rss>"""
    return Response(rss, mimetype="application/rss+xml")


@app.route('/api/source_health')
def api_source_health():
    """Active-learning: flag sources whose precision indicates they are
    contributing more noise than signal. Surfaces a warning banner
    in /accuracy.html.

    Threshold: precision < 60% AND total > 50 detections in 30 days.
    Returns: list of {source, precision_pct, total, fp_count, action_suggestion}.
    """
    try:
        resp = api_feed_accuracy()
        d = resp.get_json()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    warnings = []
    for s in (d.get('sources') or []):
        prec = s.get('precision_pct')
        total = s.get('total', 0)
        if prec is None or total < 50:
            continue
        if prec < 60:
            warnings.append({
                "source": s['source'], "kind": s['kind'],
                "precision_pct": prec, "total": total,
                "persistent_fp": s.get('persistent_fp', 0),
                "burn_disproven_fp": s.get('burn_disproven_fp', 0),
                "action_suggestion": (
                    "Consider raising the ML-accept threshold for this source, "
                    "or auditing its FP patterns. Persistent-FP hits may indicate "
                    "missing entries in the FP catalog (/falsi-positivi)."
                ),
                "severity": "high" if prec < 50 else "medium",
            })
    return jsonify({"warnings": warnings, "count": len(warnings),
                     "threshold_precision_pct": 60, "threshold_min_total": 50})


_PWA_MANIFEST = """{
  "name": "PHOENIX Wildfire Detection - Sicily",
  "short_name": "PHOENIX",
  "description": "Real-time wildfire detection and reporting for Sicily.",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#fafaf9",
  "theme_color": "#dc2626",
  "orientation": "any",
  "categories": ["weather", "utilities", "navigation"],
  "lang": "it",
  "icons": [
    {"src": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' fill='%23dc2626'/><text x='50' y='62' font-size='52' text-anchor='middle' fill='white' font-family='sans-serif' font-weight='bold'>P</text></svg>",
     "sizes": "192x192", "type": "image/svg+xml", "purpose": "any"},
    {"src": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' fill='%23dc2626'/><text x='50' y='62' font-size='52' text-anchor='middle' fill='white' font-family='sans-serif' font-weight='bold'>P</text></svg>",
     "sizes": "512x512", "type": "image/svg+xml", "purpose": "any"}
  ]
}"""


@app.route('/manifest.json')
def pwa_manifest():
    from flask import Response
    return Response(_PWA_MANIFEST, mimetype='application/manifest+json')


_SERVICE_WORKER_JS = """// PHOENIX service worker — minimal cache-then-network for the shell
const CACHE = 'phoenix-shell-v1';
const ASSETS = ['/', '/wins.html', '/accuracy.html', '/come-funziona', '/falsi-positivi'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS).catch(()=>{})));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k !== CACHE).map(k => caches.delete(k))
  )));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  // Network-first for API and dynamic content; cache-first for shell HTML
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api') || url.pathname === '/wins') {
    e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
  } else {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request).then(resp => {
        if (resp.ok && (url.pathname === '/' || url.pathname.endsWith('.html'))) {
          const copy = resp.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return resp;
      }))
    );
  }
});
"""


@app.route('/sw.js')
def pwa_service_worker():
    from flask import Response
    return Response(_SERVICE_WORKER_JS, mimetype='application/javascript')


@app.route('/api/feed_accuracy_by_aoi')
def api_feed_accuracy_by_aoi():
    """Per-source-per-AOI precision metrics last 30 days.

    For each (source, aoi) pair, compute:
      - total detections
      - TPs (corroborated)
      - FPs (persistent)
      - precision_pct

    Helps identify geographic blind spots: e.g. source X is excellent in
    agrigento but spraying in sicily_full.
    """
    import sqlite3 as _sql, math as _m
    from collections import defaultdict
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    LOOKBACK_DAYS = 30
    MATCH_KM = 5.0
    MATCH_MIN = 120
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    cutoff = (_dt.now(_tz.utc) - _td(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    con = _sql.connect(str(gt_db))
    internals = list(con.execute(
        "SELECT source, lat, lng, ts FROM internal_fires "
        "WHERE ts > ? AND (raw_json IS NULL OR raw_json NOT LIKE '%expired%')",
        (cutoff,)
    ))
    externals = list(con.execute(
        "SELECT source, lat, lng, ts FROM external_fires WHERE ts > ?", (cutoff,)
    ))
    con.close()

    def _km(a_lat, a_lon, b_lat, b_lon):
        dlat = _m.radians(b_lat - a_lat); dlon = _m.radians(b_lon - a_lon)
        s = (_m.sin(dlat/2)**2 + _m.cos(_m.radians(a_lat))
             * _m.cos(_m.radians(b_lat)) * _m.sin(dlon/2)**2)
        return 2 * 6371.0 * _m.asin(_m.sqrt(s))

    def _pt(s):
        if not s: return None
        try:
            s = s.replace('Z', '+00:00') if s.endswith('Z') else s
            dt = _dt.fromisoformat(s)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=_tz.utc)
            return dt
        except Exception:
            return None

    def _aoi_for(lat, lon):
        for name, aoi in config.get('aois', {}).items():
            if not aoi.get('enabled', True):
                continue
            try:
                s, w, n, e = aoi['bbox']
                if s <= lat <= n and w <= lon <= e:
                    return name
            except Exception:
                continue
        return 'outside_aoi'

    all_det = []
    for src, lat, lng, ts in internals:
        dt = _pt(ts)
        if dt is None: continue
        all_det.append(('internal', src, float(lat), float(lng), dt))
    for src, lat, lng, ts in externals:
        dt = _pt(ts)
        if dt is None: continue
        all_det.append(('external', src, float(lat), float(lng), dt))

    # Hour-bin index for fast matching
    from datetime import datetime as _D, timezone as _Z
    EPOCH = _D(1970, 1, 1, tzinfo=_Z.utc)
    bins = defaultdict(list)
    for i, (kind, src, lat, lng, dt) in enumerate(all_det):
        bins[int((dt - EPOCH).total_seconds() // 3600)].append(i)

    rows = defaultdict(lambda: {"total": 0, "tp": 0, "fp": 0, "unconfirmed": 0})
    for i, (kind, src, lat, lng, dt) in enumerate(all_det):
        aoi = _aoi_for(lat, lng)
        key = (src, kind, aoi)
        rows[key]["total"] += 1
        if _is_known_anomaly(lat, lng):
            rows[key]["fp"] += 1
            continue
        h = int((dt - EPOCH).total_seconds() // 3600)
        match = False
        for h2 in range(h - 2, h + 3):
            for j in bins.get(h2, ()):
                if j == i: continue
                kind2, src2, lat2, lng2, dt2 = all_det[j]
                if abs((dt - dt2).total_seconds()) > MATCH_MIN * 60: continue
                if _km(lat, lng, lat2, lng2) > MATCH_KM: continue
                if src2 != src:
                    match = True; break
            if match: break
        if match:
            rows[key]["tp"] += 1
        else:
            rows[key]["unconfirmed"] += 1

    out = []
    for (src, kind, aoi), v in rows.items():
        denom = v["tp"] + v["fp"]
        prec = round(100.0 * v["tp"] / denom, 1) if denom > 0 else None
        out.append({"source": src, "kind": kind, "aoi": aoi,
                    "total": v["total"], "tp": v["tp"], "fp": v["fp"],
                    "unconfirmed": v["unconfirmed"], "precision_pct": prec})
    out.sort(key=lambda r: (r['aoi'], r['kind'] != 'internal', -r['total']))
    return jsonify({"rows": out, "lookback_days": LOOKBACK_DAYS})


@app.route('/wins.ics')
def wins_ical():
    """iCalendar feed of confirmed PHOENIX wins + external-only wins.

    Subscribers in Google/Apple/Outlook Calendar get a new event for every
    confirmed wildfire, with location, lead time, and link to live map.
    """
    from flask import Response
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    try:
        resp = wins_json()
        data = resp.get_json()
    except Exception as exc:
        return Response("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR", mimetype='text/calendar')

    def _ical_dt(iso):
        try:
            s = iso.replace('Z', '+00:00') if iso.endswith('Z') else iso
            dt = _dt.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz.utc)
            return dt.astimezone(_tz.utc).strftime('%Y%m%dT%H%M%SZ')
        except Exception:
            return _dt.now(_tz.utc).strftime('%Y%m%dT%H%M%SZ')

    def _esc(s):
        return (str(s or "").replace("\\", "\\\\")
                .replace(";", "\\;").replace(",", "\\,")
                .replace("\n", "\\n"))

    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//PHOENIX Wildfire Detection//Sicily//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:PHOENIX confirmed wildfires - Sicily",
        "X-WR-CALDESC:Confirmed wildfire detections in Sicily (PHOENIX + comparator orgs)",
    ]
    # PHOENIX-led wins
    for w in (data.get('wins') or [])[:60]:
        dt = _ical_dt(w.get('phoenix_ts', ''))
        uid = "phoenix-" + str(w.get('det_id', 'na')) + "@adr-wildfire.com"
        summary = f"PHOENIX fire (+{w.get('lead_min_vs_sensed', 0)} min vs {w.get('ext_source', '?')})"
        desc = (f"Detected by PHOENIX at {w.get('phoenix_ts', '')}\\n"
                f"Comparator sensed at {w.get('comparator_sensed_at', '')}\\n"
                f"Location: {w.get('lat', 0):.4f},{w.get('lng', 0):.4f}\\n"
                f"Confidence: {w.get('confidence', 0):.2f} | FRP: {w.get('fused_frp_mw', w.get('frp_mw', 0)):.2f} MW\\n"
                f"Independent sensors agreeing: {w.get('sensors_independent', 1)}\\n"
                f"Live map: https://adr-wildfire.com/")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dt}",
            f"DTSTART:{dt}",
            f"DTEND:{dt}",
            f"SUMMARY:{_esc(summary)}",
            f"DESCRIPTION:{_esc(desc)}",
            f"GEO:{w.get('lat', 0):.6f};{w.get('lng', 0):.6f}",
            f"LOCATION:{_esc(w.get('aoi_id', '') + ' - Sicily')}",
            "URL:https://adr-wildfire.com/wins.html",
            "END:VEVENT",
        ])
    # External-only wins
    for w in (data.get('external_only_wins') or [])[:60]:
        dt = _ical_dt(w.get('sensed_at', ''))
        uid = f"external-{dt}-{w.get('lat', 0)}@adr-wildfire.com"
        summary = f"External fire caught by {w.get('source', '?')} (PHOENIX missed)"
        desc = (f"Sensor acquired at {w.get('sensed_at', '')}\\n"
                f"Feed delivered at {w.get('reported_at', '')}\\n"
                f"Location: {w.get('lat', 0):.4f},{w.get('lng', 0):.4f}")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dt}",
            f"DTSTART:{dt}",
            f"DTEND:{dt}",
            f"SUMMARY:{_esc(summary)}",
            f"DESCRIPTION:{_esc(desc)}",
            f"GEO:{w.get('lat', 0):.6f};{w.get('lng', 0):.6f}",
            "URL:https://adr-wildfire.com/wins.html",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines), mimetype="text/calendar")


@app.route('/api/user_fp_flag', methods=['POST'])
def api_user_fp_flag():
    """User-submitted false-positive flag for active learning.

    Body: {det_id?: int, lat: float, lng: float, source?: str, reason?: str}.
    Stored in user_fp_flags table; Mark sees these in his daily review batch.
    """
    from flask import request
    import sqlite3 as _sql
    from datetime import datetime as _dt, timezone as _tz
    try:
        data = request.get_json(force=True, silent=True) or {}
        lat = float(data.get('lat'))
        lng = float(data.get('lng'))
    except Exception:
        return jsonify({"error": "lat + lng required"}), 400
    det_id = data.get('det_id')
    source = data.get('source', 'unknown')
    reason = (data.get('reason') or '')[:500]
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = _sql.connect(str(gt_db), timeout=15.0)
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_fp_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, det_id INTEGER, source TEXT,
                lat REAL, lng REAL, reason TEXT,
                user_ip TEXT, reviewed_at TEXT, reviewer_action TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_ufp_ts ON user_fp_flags(ts)")
        con.execute(
            "INSERT INTO user_fp_flags (ts, det_id, source, lat, lng, reason, user_ip) "
            "VALUES (?,?,?,?,?,?,?)",
            (_dt.now(_tz.utc).isoformat(), det_id, source, lat, lng, reason,
             request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown'))
        )
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "message": "Grazie! La segnalazione sarà revisionata."})


@app.route('/robots.txt')
def robots_txt():
    from flask import Response
    body = """User-agent: *
Allow: /
Allow: /falsi-positivi
Allow: /come-funziona
Allow: /wins.html
Allow: /accuracy.html
Allow: /api/openapi.json
Allow: /api/docs.html
Disallow: /api/detection-crop/

Sitemap: https://adr-wildfire.com/sitemap.xml
"""
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap_xml():
    from flask import Response
    from datetime import datetime as _dt, timezone as _tz
    today = _dt.now(_tz.utc).strftime('%Y-%m-%d')
    urls = [
        "https://adr-wildfire.com/",
        "https://adr-wildfire.com/wins.html",
        "https://adr-wildfire.com/accuracy.html",
        "https://adr-wildfire.com/falsi-positivi",
        "https://adr-wildfire.com/come-funziona",
        "https://adr-wildfire.com/api/docs.html",
        "https://adr-wildfire.com/api/openapi.json",
        "https://adr-wildfire.com/wins.rss",
        "https://adr-wildfire.com/wins.ics",
    ]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lines.append(f"<url><loc>{u}</loc><lastmod>{today}</lastmod><changefreq>hourly</changefreq></url>")
    lines.append("</urlset>")
    return Response("\n".join(lines), mimetype='application/xml')


@app.route('/api/predict_next_24h')
def api_predict_next_24h():
    """Per-AOI ignition probability for next 24h.

    Combines:
      - historical fire-day rate per AOI (last 90 days)
      - recent lightning activity (last 6h)
      - current FWI (best-effort EFFIS WMS lookup; falls back to None)

    Returns per-AOI:
      {aoi, baseline_rate, lightning_boost, fwi_boost, total_score, action_level}
    """
    import sqlite3 as _sql, math as _m
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    cutoff_hist = (_dt.now(_tz.utc) - _td(days=90)).strftime("%Y-%m-%dT%H:%M:%S")
    cutoff_lit = (_dt.now(_tz.utc) - _td(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")
    con = _sql.connect(str(gt_db), timeout=15.0)
    con.execute("PRAGMA busy_timeout = 15000")

    out = []
    try:
        for name, aoi in config.get('aois', {}).items():
            if not aoi.get('enabled', True):
                continue
            try:
                s, w, n, e = aoi['bbox']
            except Exception:
                continue
            # Baseline: count of distinct fire-event days in this AOI in last 90d
            try:
                rows = con.execute(
                    "SELECT DISTINCT date(ts) FROM external_fires "
                    "WHERE ts > ? AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ? "
                    "AND source LIKE 'firms_%'",
                    (cutoff_hist, s, n, w, e)
                ).fetchall()
                fire_days = len(rows)
            except Exception:
                fire_days = 0
            baseline_rate = fire_days / 90.0  # daily ignition probability

            # Lightning boost: strikes in AOI bbox last 6h (from lightning_li cache)
            try:
                from src.data_sources.lightning_li import recent_strikes_in_window
                strikes = recent_strikes_in_window(window_minutes=360)
                in_aoi = [st for st in strikes
                          if s <= st.lat <= n and w <= st.lon <= e]
                lightning_boost = min(1.0, len(in_aoi) / 10.0)
            except Exception:
                lightning_boost = 0.0

            # FWI boost: skip per-cell WMS lookup; use median Sicily FWI proxy
            fwi_boost = 0.0  # placeholder; pluggable when per-pixel FWI is wired

            # Total score (normalized 0-1)
            score = min(1.0, baseline_rate + 0.3 * lightning_boost + 0.2 * fwi_boost)
            level = ("low" if score < 0.2 else
                     "moderate" if score < 0.5 else
                     "high" if score < 0.8 else "extreme")
            out.append({
                "aoi": name,
                "bbox": [s, w, n, e],
                "historical_fire_days_90d": fire_days,
                "baseline_rate": round(baseline_rate, 3),
                "lightning_boost": round(lightning_boost, 3),
                "fwi_boost": round(fwi_boost, 3),
                "score_next_24h": round(score, 3),
                "action_level": level,
            })
    finally:
        con.close()

    out.sort(key=lambda r: -r["score_next_24h"])
    return jsonify({"forecasts": out,
                     "generated_at": _dt.now(_tz.utc).isoformat(),
                     "note": ("Experimental short-term ignition forecast. "
                              "Combines historical fire-day rate, recent lightning, "
                              "and FWI (when wired). Probabilities are rough — "
                              "use as a relative ranking across AOIs, not absolute risk.")})


@app.route('/api/fire_density_grid')
def api_fire_density_grid():
    """Historical fire-density grid over Sicily, last 90 days.

    Returns a GeoJSON FeatureCollection of Point features. Each point is
    one ~0.05deg (~5km) grid cell with `count` (cumulative fire detections)
    and `density` (count / 90 days). For Leaflet heat-layer or choropleth.
    """
    import sqlite3 as _sql
    from collections import defaultdict
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    GRID_DEG = 0.05
    LOOKBACK_DAYS = 90
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    cutoff = (_dt.now(_tz.utc) - _td(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    cells = defaultdict(int)
    con = _sql.connect(str(gt_db), timeout=15.0)
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        rows = con.execute(
            "SELECT lat, lng FROM external_fires "
            "WHERE ts > ? AND source LIKE 'firms_%' "
            "AND lat BETWEEN 36.6 AND 38.3 AND lng BETWEEN 12.4 AND 15.4",
            (cutoff,)
        )
        for lat, lng in rows:
            try:
                key = (round(float(lat) / GRID_DEG) * GRID_DEG,
                       round(float(lng) / GRID_DEG) * GRID_DEG)
                cells[key] += 1
            except Exception:
                continue
        # Also include internal_fires (non-expired)
        rows2 = con.execute(
            "SELECT lat, lng FROM internal_fires "
            "WHERE ts > ? AND (raw_json IS NULL OR raw_json NOT LIKE '%expired%') "
            "AND lat BETWEEN 36.6 AND 38.3 AND lng BETWEEN 12.4 AND 15.4",
            (cutoff,)
        )
        for lat, lng in rows2:
            try:
                key = (round(float(lat) / GRID_DEG) * GRID_DEG,
                       round(float(lng) / GRID_DEG) * GRID_DEG)
                cells[key] += 1
            except Exception:
                continue
    finally:
        con.close()

    features = []
    for (lat, lng), count in cells.items():
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"count": int(count),
                           "density_per_day": round(count / float(LOOKBACK_DAYS), 4)},
        })
    fc = {"type": "FeatureCollection", "features": features,
          "metadata": {"grid_deg": GRID_DEG, "lookback_days": LOOKBACK_DAYS,
                       "total_cells": len(features)}}
    return jsonify(fc)


@app.route('/api/webhook_subscribe', methods=['POST'])
def api_webhook_subscribe():
    """Subscribe a URL to receive POST callbacks on every PHOENIX-confirmed win.

    Body: {url: str, secret?: str (HMAC signing), email?: str (notify on failures)}
    Subscriber URLs receive: {event: "phoenix_win", win: {...}} on every new win.
    """
    from flask import request
    import sqlite3 as _sql, secrets as _secrets
    from datetime import datetime as _dt, timezone as _tz
    try:
        data = request.get_json(force=True, silent=True) or {}
        url = data.get('url', '').strip()
        if not url or not url.startswith(('http://', 'https://')):
            return jsonify({"error": "valid http(s) url required"}), 400
    except Exception:
        return jsonify({"error": "bad request body"}), 400
    secret_hmac = data.get('secret') or _secrets.token_urlsafe(24)
    email = data.get('email')
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = _sql.connect(str(gt_db), timeout=15.0)
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS webhook_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            secret TEXT,
            email TEXT,
            created_at TEXT,
            last_fired_at TEXT,
            failure_count INTEGER DEFAULT 0,
            disabled INTEGER DEFAULT 0
        )""")
        try:
            con.execute(
                "INSERT INTO webhook_subscriptions (url, secret, email, created_at) "
                "VALUES (?, ?, ?, ?)",
                (url, secret_hmac, email, _dt.now(_tz.utc).isoformat())
            )
            con.commit()
            return jsonify({"ok": True,
                             "subscribed_url": url,
                             "secret": secret_hmac,
                             "note": ("Save this secret. PHOENIX will POST {event, win} "
                                      "to your URL with X-PHOENIX-Signature: sha256=HMAC(secret, body) header. "
                                      "After 5 consecutive failures the subscription auto-disables.")})
        except _sql.IntegrityError:
            return jsonify({"error": "url already subscribed"}), 409
    finally:
        con.close()


@app.route('/api/webhook_unsubscribe', methods=['POST'])
def api_webhook_unsubscribe():
    """Unsubscribe a URL. Body: {url, secret}."""
    from flask import request
    import sqlite3 as _sql
    try:
        data = request.get_json(force=True, silent=True) or {}
        url = data.get('url', '').strip()
        secret = data.get('secret', '').strip()
    except Exception:
        return jsonify({"error": "bad request"}), 400
    if not url or not secret:
        return jsonify({"error": "url + secret required"}), 400
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = _sql.connect(str(gt_db), timeout=15.0)
    con.execute("PRAGMA busy_timeout = 15000")
    try:
        row = con.execute("SELECT secret FROM webhook_subscriptions WHERE url = ?",
                           (url,)).fetchone()
        if not row:
            return jsonify({"error": "not subscribed"}), 404
        if row[0] != secret:
            return jsonify({"error": "secret mismatch"}), 403
        con.execute("DELETE FROM webhook_subscriptions WHERE url = ?", (url,))
        con.commit()
    finally:
        con.close()
    return jsonify({"ok": True, "unsubscribed_url": url})


@app.route('/api/recent_burned_area')
def api_recent_burned_area():
    """Per-AOI burned-area estimate from Sentinel-2 dNBR verifications (30d).

    For each verified burn (dNBR > 0.27), credit a ~2km radius burn footprint
    (~1257 ha worst case) — this is a rough upper bound, NOT a high-precision
    measurement. Per-pixel polygon integration would be the real fix.
    """
    import sqlite3 as _sql, json as _j, math as _m
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    LOOKBACK_DAYS = 30
    BURN_FOOTPRINT_HA = 1257  # ~2km radius
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    cutoff = (_dt.now(_tz.utc) - _td(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    con = _sql.connect(str(gt_db), timeout=15.0)
    con.execute("PRAGMA busy_timeout = 15000")
    per_aoi = {}
    total_verified = 0
    total_disproven = 0
    try:
        rows = list(con.execute(
            "SELECT lat, lng, raw_json FROM internal_fires "
            "WHERE ts > ? AND raw_json LIKE '%burn_verification%'", (cutoff,)
        ))
        for lat, lng, raw in rows:
            try:
                rd = _j.loads(raw) if raw else {}
                bv = (rd.get("burn_verification") or {})
                vb = bv.get("verified_burn")
                if vb is True:
                    total_verified += 1
                    aoi_name = "outside_aoi"
                    for name, aoi in config.get('aois', {}).items():
                        try:
                            s, w, n, e = aoi['bbox']
                            if s <= float(lat) <= n and w <= float(lng) <= e:
                                aoi_name = name; break
                        except Exception:
                            continue
                    per_aoi.setdefault(aoi_name, {"count": 0, "est_ha": 0.0})
                    per_aoi[aoi_name]["count"] += 1
                    per_aoi[aoi_name]["est_ha"] += BURN_FOOTPRINT_HA
                elif vb is False:
                    total_disproven += 1
            except Exception:
                continue
    finally:
        con.close()
    out = [{"aoi": k, "verified_count": v["count"], "estimated_ha": v["est_ha"]}
            for k, v in sorted(per_aoi.items(), key=lambda kv: -kv[1]["est_ha"])]
    return jsonify({
        "per_aoi": out,
        "total_verified_burns": total_verified,
        "total_disproven": total_disproven,
        "lookback_days": LOOKBACK_DAYS,
        "footprint_assumption_ha": BURN_FOOTPRINT_HA,
        "note": ("Rough upper-bound area estimate; per-pixel polygon integration "
                  "would give precise figures. Use as relative ranking across AOIs."),
    })


@app.route('/api/feed_accuracy_by_aoi.csv')
def api_feed_accuracy_by_aoi_csv():
    """CSV export of /api/feed_accuracy_by_aoi for researchers."""
    import csv, io
    from flask import Response
    try:
        resp = api_feed_accuracy_by_aoi()
        data = resp.get_json()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    buf = io.StringIO()
    cols = ["aoi", "source", "kind", "total", "tp", "fp", "unconfirmed", "precision_pct"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in (data.get('rows') or []):
        w.writerow(r)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=phoenix_feed_accuracy_by_aoi.csv"})


@app.route('/api/predict_next_24h.csv')
def api_predict_next_24h_csv():
    """CSV export of /api/predict_next_24h forecasts."""
    import csv, io
    from flask import Response
    try:
        resp = api_predict_next_24h()
        data = resp.get_json()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    buf = io.StringIO()
    cols = ["aoi", "historical_fire_days_90d", "baseline_rate",
            "lightning_boost", "fwi_boost", "score_next_24h", "action_level"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in (data.get('forecasts') or []):
        w.writerow(r)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=phoenix_predict_next_24h.csv"})


@app.route('/api/smoke_yolo')
def api_smoke_yolo():
    """Recent PHOENIX detections with YOLOv8 smoke-detection verification."""
    import sqlite3 as _sql, json as _j
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = _sql.connect(str(gt_db), timeout=15.0)
    con.execute("PRAGMA busy_timeout = 15000")
    rows = list(con.execute(
        "SELECT id, source, lat, lng, ts, raw_json FROM internal_fires "
        "WHERE ts > datetime('now', '-7 days') "
        "AND raw_json LIKE '%smoke_yolo%' "
        "ORDER BY ts DESC LIMIT 100"
    ))
    con.close()
    out = []
    for det_id, src, lat, lng, ts, raw in rows:
        try:
            r = _j.loads(raw) if raw else {}
            sy = (r.get('smoke_yolo') or {})
        except Exception:
            sy = {}
        out.append({"det_id": det_id, "source": src, "lat": lat, "lng": lng, "ts": ts,
                    "has_smoke": sy.get("has_smoke"),
                    "max_conf": sy.get("max_conf"),
                    "n_boxes": sy.get("n_boxes"),
                    "checked_at": sy.get("checked_at")})
    smoke_count = sum(1 for x in out if x.get("has_smoke") is True)
    return jsonify({"results": out, "count": len(out), "with_smoke": smoke_count})


@app.route('/api/joint_dozier')
def api_joint_dozier():
    """Recent joint multi-satellite (FCI+SLSTR+S2) Dozier-certified detections.

    Joint Bayesian inversion: when 2+ sensors observe the same scene within
    ±7 min, solve for sub-pixel (p, T_f, T_bg) using all channels at once.
    Cuts FCI ΔT certification threshold from 4K → 1K → +10min lead-time.
    """
    from flask import request
    try:
        hours = int(request.args.get("hours", "24"))
    except (TypeError, ValueError):
        hours = 24
    try:
        from src.verifiers.joint_dozier import get_recent
        return jsonify(get_recent(hours=hours, limit=200))
    except Exception as exc:
        return jsonify({"error": str(exc), "results": [], "count": 0}), 500


@app.route('/api/openapi.json')
def api_openapi_spec():
    """OpenAPI 3.1 spec for PHOENIX endpoints."""
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "PHOENIX Wildfire Detection API",
            "version": "1.0.0",
            "description": ("Public read-only API for PHOENIX wildfire-detection "
                            "research system (Sicily). All data CC-BY 4.0. "
                            "https://adr-wildfire.com/come-funziona"),
            "contact": {"url": "https://github.com/markl02us/persistent-thermal-sources-sicily"},
            "license": {"name": "CC-BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
        },
        "servers": [{"url": "https://adr-wildfire.com", "description": "Production"}],
        "paths": {
            "/api/detections": {
                "get": {
                    "summary": "Active fire detections",
                    "parameters": [
                        {"name": "hours", "in": "query", "schema": {"type": "integer", "default": 24}},
                        {"name": "include_comparators", "in": "query", "schema": {"type": "integer", "default": 0}},
                    ],
                    "responses": {"200": {"description": "JSON with detections array"}},
                }
            },
            "/api/feed_accuracy": {
                "get": {
                    "summary": "Per-source accuracy + precision metrics (30d)",
                    "responses": {"200": {"description": "JSON with sources[] + support_feeds[]"}},
                }
            },
            "/wins": {
                "get": {
                    "summary": "PHOENIX-led wins + external-only wins (7d)",
                    "responses": {"200": {"description": "JSON with wins[] + external_only_wins[]"}},
                }
            },
            "/api/false_positive_zones": {
                "get": {"summary": "Persistent thermal source catalog (FP zones)",
                        "responses": {"200": {"description": "JSON with sources[] (lat, lon, radius_km, category, ...)"}}}
            },
            "/api/burn_verification": {
                "get": {"summary": "Sentinel-2 burn-scar verification results",
                        "responses": {"200": {"description": "JSON with verifications[]"}}}
            },
            "/api/news_reports": {
                "get": {"summary": "ANSA + Vigili del Fuoco + Giornale di Sicilia wildfire news (7d)",
                        "responses": {"200": {"description": "JSON with reports[]"}}}
            },
            "/api/slstr_hits": {
                "get": {"summary": "Sentinel-3 SLSTR FRP detections over Sicily (7d)",
                        "responses": {"200": {"description": "JSON with detections[]"}}}
            },
            "/api/air_quality": {
                "get": {"summary": "ARPA Sicilia + EEA air-quality observations (24h)",
                        "responses": {"200": {"description": "JSON with observations[]"}}}
            },
            "/api/lightning": {
                "get": {"summary": "Recent MTG-LI lightning strikes (last 30 min)",
                        "responses": {"200": {"description": "JSON with strikes[]"}}}
            },
            "/api/ignition_prior": {
                "get": {
                    "summary": "Hawkes ignition prior at a given coordinate",
                    "parameters": [
                        {"name": "lat", "in": "query", "schema": {"type": "number"}, "required": True},
                        {"name": "lon", "in": "query", "schema": {"type": "number"}, "required": True},
                        {"name": "fwi", "in": "query", "schema": {"type": "number"}, "required": False},
                    ],
                    "responses": {"200": {"description": "JSON with prior + components{}"}},
                }
            },
            "/api/daily_digest": {
                "get": {"summary": "24h activity summary",
                        "responses": {"200": {"description": "JSON with phoenix_by_source + external_by_source + ..."}}}
            },
            "/scoreboard": {
                "get": {"summary": "Lead-time scoreboard (wins/losses/pushes vs FIRMS+EFFIS)",
                        "responses": {"200": {"description": "JSON scoreboard"}}}
            },
            "/api/openapi.json": {
                "get": {"summary": "This spec",
                        "responses": {"200": {"description": "OpenAPI 3.1 JSON"}}}
            },
        },
    }
    return jsonify(spec)


@app.route('/api/lightning')
def api_lightning():
    """Recent MTG-LI lightning strikes (in-memory cache, last 30 min)."""
    try:
        from src.data_sources.lightning_li import recent_strikes_in_window
        strikes = recent_strikes_in_window(window_minutes=30)
        out = []
        for s in strikes:
            try:
                out.append({
                    "lat": float(s.lat),
                    "lon": float(s.lon),
                    "ts": s.ts.isoformat() if hasattr(s.ts, 'isoformat') else str(s.ts),
                    "energy": getattr(s, 'energy', None),
                    "group_size": getattr(s, 'group_size', None),
                })
            except Exception:
                continue
        return jsonify({"strikes": out, "count": len(out), "window_min": 30})
    except Exception as exc:
        return jsonify({"strikes": [], "count": 0, "error": str(exc)})


@app.route('/api/news_reports')
def api_news_reports():
    """Last N news reports (ANSA + future feeds) flagged as wildfire-related."""
    import sqlite3, json as _json
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    rows = list(con.execute(
        "SELECT source, lat, lng, ts, raw_json FROM external_fires "
        "WHERE source = 'ansa_news' AND ts > datetime('now','-7 days') "
        "ORDER BY ts DESC LIMIT 100"
    ))
    con.close()
    out = []
    for src, lat, lng, ts, raw in rows:
        try:
            r = _json.loads(raw) if raw else {}
        except Exception:
            r = {}
        out.append({"source": src, "lat": lat, "lng": lng, "ts": ts,
                    "title": r.get("title"), "link": r.get("link"),
                    "place": r.get("place"),
                    "matched_keywords": r.get("matched_keywords", [])})
    return jsonify({"reports": out, "count": len(out)})


@app.route('/api/slstr_hits')
def api_slstr_hits():
    """Last N Sentinel-3 SLSTR FRP hits over Sicily."""
    import sqlite3, json as _json
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    rows = list(con.execute(
        "SELECT source, lat, lng, ts, raw_json FROM external_fires "
        "WHERE source LIKE 'slstr_frp%' AND ts > datetime('now','-7 days') "
        "ORDER BY ts DESC LIMIT 100"
    ))
    con.close()
    out = []
    for src, lat, lng, ts, raw in rows:
        try:
            r = _json.loads(raw) if raw else {}
        except Exception:
            r = {}
        out.append({"source": src, "lat": lat, "lng": lng, "ts": ts,
                    "frp_mw": r.get("frp_mw"),
                    "product_id": r.get("product_id")})
    return jsonify({"detections": out, "count": len(out)})


@app.route('/api/air_quality')
def api_air_quality():
    """Latest air-quality observations from ARPA/EEA Sicilian stations."""
    import sqlite3
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    out = []
    try:
        rows = list(con.execute(
            "SELECT station_id, lat, lng, ts, pm25, pm10, co "
            "FROM air_quality WHERE ts > datetime('now','-24 hours') "
            "ORDER BY ts DESC LIMIT 200"
        ))
        for sid, lat, lng, ts, pm25, pm10, co in rows:
            out.append({"station_id": sid, "lat": lat, "lng": lng, "ts": ts,
                        "pm25": pm25, "pm10": pm10, "co": co})
    except Exception:
        pass
    con.close()
    return jsonify({"observations": out, "count": len(out)})


def _build_daily_digest(lookback_hours: int = 24) -> dict:
    """Compose summary of last N hours of PHOENIX activity."""
    import sqlite3 as _sql, json as _json
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    cutoff = (_dt.now(_tz.utc) - _td(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%S")
    con = _sql.connect(str(gt_db))
    out = {
        "lookback_hours": lookback_hours,
        "generated_at": _dt.now(_tz.utc).isoformat(),
    }
    try:
        # Internal detections by source
        out["phoenix_by_source"] = [
            {"source": r[0], "total": r[1], "expired": r[2], "non_expired": r[1] - r[2]}
            for r in con.execute(
                "SELECT source, COUNT(*), SUM(CASE WHEN raw_json LIKE '%expired%' THEN 1 ELSE 0 END) "
                "FROM internal_fires WHERE ts > ? GROUP BY source", (cutoff,)
            )
        ]
        out["external_by_source"] = [
            {"source": r[0], "total": r[1]}
            for r in con.execute(
                "SELECT source, COUNT(*) FROM external_fires WHERE ts > ? GROUP BY source ORDER BY 2 DESC",
                (cutoff,)
            )
        ]
        # Burn verifications (parsed in Python — embedding JSON-quoted strings
        # inside SQLite LIKE patterns inside a Python string is fragile).
        bv_total = 0; bv_confirmed = 0; bv_disproven = 0
        import json as _bvj
        for (raw,) in con.execute(
            "SELECT raw_json FROM internal_fires WHERE raw_json LIKE '%burn_verification%' AND ts > ?",
            (cutoff,),
        ):
            bv_total += 1
            try:
                rd = _bvj.loads(raw) if raw else {}
                vb = (rd.get("burn_verification") or {}).get("verified_burn")
                if vb is True: bv_confirmed += 1
                elif vb is False: bv_disproven += 1
            except Exception:
                pass
        out["burn_verifications"] = [bv_total, bv_confirmed, bv_disproven]
        # Support feeds activity
        out["support_feeds"] = []
        for tbl in ("smoke_proxies", "corroboration_signals", "smoke_corroboration",
                    "weather_obs", "air_obs"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {tbl} WHERE ts > ?", (cutoff,)).fetchone()[0]
                out["support_feeds"].append({"table": tbl, "rows": int(n)})
            except Exception:
                pass
    finally:
        con.close()

    # PHOENIX 2026-05-24 — FCI snapshot count (for baseline build readiness)
    try:
        from pathlib import Path as _DP
        fci_dir = _DP("/media/mark/AI_DGX/eumetsat_data/fci_scratch/baseline_frames")
        out["fci_snapshot_count"] = len(list(fci_dir.glob("*.npz"))) if fci_dir.exists() else 0
        out["fci_baseline_ready"] = out["fci_snapshot_count"] >= 200
    except Exception:
        out["fci_snapshot_count"] = None

    # PHOENIX 2026-05-24 — per-AOI threshold overrides currently active
    try:
        out["per_aoi_thresholds"] = _per_aoi_overrides()
    except Exception:
        out["per_aoi_thresholds"] = {}

    return out


@app.route('/api/daily_digest')
def api_daily_digest():
    return jsonify(_build_daily_digest())


def _send_daily_digest_email() -> bool:
    """Compose the daily digest, format as text, send via Gmail SMTP."""
    import smtplib, json as _json
    from email.mime.text import MIMEText
    from datetime import datetime as _dt, timezone as _tz
    secrets_path = Path("/home/mark/.openclaw/workspace/eumetsat_wildfire_detection/secrets/gmail.json")
    if not secrets_path.exists():
        logger.warning("daily_digest: gmail.json not found at %s", secrets_path)
        return False
    creds = _json.loads(secrets_path.read_text())

    digest = _build_daily_digest()

    # Format as human-readable text
    lines = [
        f"PHOENIX Daily Digest - {_dt.now(_tz.utc).strftime('%Y-%m-%d')} UTC",
        "=" * 70, "",
        f"Lookback window: {digest['lookback_hours']}h", "",
        "## PHOENIX internal detectors",
    ]
    for src in digest.get("phoenix_by_source", []):
        lines.append(f"  {src['source']:25s} total={src['total']:5d}  non-expired={src['non_expired']:5d}  expired={src['expired']:5d}")
    if not digest.get("phoenix_by_source"):
        lines.append("  (no PHOENIX detections in window)")

    lines.extend(["", "## External comparators (top 10)"])
    for src in (digest.get("external_by_source") or [])[:10]:
        lines.append(f"  {src['source']:35s} {src['total']:5d}")

    bv = digest.get("burn_verifications") or (0, 0, 0)
    lines.extend([
        "", "## Sentinel-2 burn verifications",
        f"  Total verified: {bv[0] or 0}",
        f"  Confirmed burn (dNBR > 0.27): {bv[1] or 0}",
        f"  Disproven (dNBR < 0.10):      {bv[2] or 0}",
    ])

    lines.extend(["", "## Support feeds activity"])
    for sf in digest.get("support_feeds", []):
        lines.append(f"  {sf['table']:30s} {sf['rows']:5d} rows")

    lines.extend([
        "", "",
        "Live dashboards:",
        "  Map:          https://adr-wildfire.com/",
        "  Wins:         https://adr-wildfire.com/wins.html",
        "  Accuracy:     https://adr-wildfire.com/accuracy.html",
        "  FP catalog:   https://adr-wildfire.com/falsi-positivi",
        "  Methodology:  https://adr-wildfire.com/come-funziona",
        "",
        "GitHub repo (FP catalog): https://github.com/markl02us/persistent-thermal-sources-sicily",
        "",
        "PHOENIX runs autonomously. This digest is auto-generated daily at 06:00 UTC.",
    ])

    body = "\n".join(lines)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"PHOENIX daily digest - {_dt.now(_tz.utc).strftime('%Y-%m-%d')} UTC"
    msg["From"] = creds.get("from_address", creds["username"])
    msg["To"] = "markl02us@yahoo.com"

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(creds["username"], creds["app_password"])
            smtp.sendmail(msg["From"], [msg["To"]], msg.as_string())
        logger.info("daily_digest: email sent to %s", msg["To"])
        return True
    except Exception as exc:
        logger.warning("daily_digest: SMTP send failed: %s", exc)
        return False


@app.route('/api/burn_verification')
def api_burn_verification():
    """Most recent S-2 burn-scar verification results from internal_fires.raw_json."""
    import sqlite3, json as _json
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    rows = list(con.execute(
        "SELECT id, source, lat, lng, ts, raw_json FROM internal_fires "
        "WHERE ts > datetime('now','-14 days') "
        "AND raw_json LIKE '%burn_verification%' "
        "ORDER BY ts DESC LIMIT 100"
    ))
    con.close()
    out = []
    for det_id, src, lat, lng, ts, raw in rows:
        try:
            r = _json.loads(raw) if raw else {}
            bv = r.get("burn_verification", {}) if isinstance(r, dict) else {}
        except Exception:
            bv = {}
        out.append({"det_id": det_id, "source": src, "lat": lat, "lng": lng, "ts": ts,
                    "verified_burn": bv.get("verified_burn"),
                    "dNBR": bv.get("dNBR"),
                    "pre_NBR": bv.get("pre_NBR"),
                    "post_NBR": bv.get("post_NBR"),
                    "post_scene_dt": bv.get("post_scene_dt"),
                    "error": bv.get("error")})
    return jsonify({"verifications": out, "count": len(out)})


@app.route('/scoreboard')
def scoreboard_json():
    """Lead-time benchmark: wins/losses/pushes vs FIRMS+EFFIS ground truth."""
    if GT_AVAILABLE:
        try:
            return jsonify(compute_scoreboard(config))
        except Exception as _se:
            return jsonify({'error': str(_se)}), 500
    return jsonify({'error': 'scoring module not available'}), 503


_SCOREBOARD_HTML = '<!DOCTYPE html><html><head><title>ADR WildFire - Lead-Time Scoreboard</title><style>body{font-family:Segoe UI,sans-serif;background:#1a2530;color:#ecf0f1;padding:20px}h1{color:#e74c3c}h2{color:#f39c12}.card{background:#2c3e50;border-radius:8px;padding:20px;margin:12px 0;display:inline-block;min-width:160px;margin-right:12px}.val{font-size:3em;font-weight:bold}.lbl{font-size:.8em;color:#bdc3c7;text-transform:uppercase;letter-spacing:.5px}table{border-collapse:collapse;width:100%;margin-top:16px}th,td{padding:10px 14px;text-align:left;border-bottom:1px solid #34495e}th{background:#2c3e50;color:#f39c12}tr:hover{background:#2c3e50}.win{color:#2ecc71}.loss{color:#e74c3c}.push{color:#95a5a6}a{color:#3498db}.sev-low{color:#95a5a6}.sev-medium{color:#f39c12}.sev-high{color:#e67e22}.sev-critical{color:#e74c3c;font-weight:bold}#narr-table td{font-size:.85em;max-width:320px;word-break:break-word}</style></head><body><h1>ADR WildFire - Lead-Time Scoreboard</h1><p><a href="/">Back to map</a></p><div id="summary"></div><h2>Per-AOI Breakdown</h2><table><thead><tr><th>AOI</th><th>Wins</th><th>Losses</th><th>Pushes</th><th>Dup</th><th>Median Lead (min)</th></tr></thead><tbody id="aoi-body"></tbody></table><h2>Hermes Narrations <small style="font-size:.6em;color:#bdc3c7">(last 10 high-confidence)</small></h2><table id="narr-table"><thead><tr><th>Time (UTC)</th><th>AOI</th><th>Severity</th><th>Summary</th><th>Lead (min)</th></tr></thead><tbody id="narr-body"></tbody></table><script>fetch("/scoreboard").then(function(r){return r.json();}).then(function(d){var s=document.getElementById("summary");var med=d.median_lead_time_min!=null?d.median_lead_time_min.toFixed(1):"--";var html=\'\'+\'<div class="card"><div class="val win">\'+d.wins+\'</div><div class="lbl">Wins</div></div>\'+\'<div class="card"><div class="val loss">\'+d.losses+\'</div><div class="lbl">Losses</div></div>\'+\'<div class="card"><div class="val">\'+d.total+\'</div><div class="lbl">Total</div></div>\'+\'<div class="card"><div class="val win">\'+(d.win_rate*100).toFixed(1)+\'%</div><div class="lbl">Win%</div></div>\'+\'<div class="card"><div class="val">\'+med+\'</div><div class="lbl">Median Lead</div></div>\';s.innerHTML=html;var tbody=document.getElementById("aoi-body");for(var aoi in d.by_aoi){var a=d.by_aoi[aoi];var tr=document.createElement("tr");var ml=a.median_lead_time_min!=null?a.median_lead_time_min.toFixed(1):"--";tr.innerHTML=\'<td>\'+aoi+\'</td>\'+\'<td class="win">\'+a.wins+\'</td>\'+\'<td class="loss">\'+a.losses+\'</td>\'+\'<td class="push">\'+a.pushes+\'</td>\'+\'<td>\'+a.duplicates+\'</td><td>\'+ml+\'</td>\';tbody.appendChild(tr);}var nb=document.getElementById("narr-body");var narrs=d.recent_narrations||[];if(narrs.length===0){nb.innerHTML=\'<tr><td colspan=5 style="color:#7f8c8d">No narrations yet</td></tr>\';}else{narrs.forEach(function(n){var lt=n.lead_time_min!=null?n.lead_time_min.toFixed(1):"--";var sev=n.severity||"low";var tr2=document.createElement("tr");tr2.innerHTML=\'<td>\'+n.ts+\'</td><td>\'+n.aoi_id+\'</td>\'+\'<td class="sev-\'+sev+\'">\'+sev+\'</td>\'+\'<td>\'+n.summary+\'</td><td>\'+lt+\'</td>\';nb.appendChild(tr2);});}});</script></body></html>'


@app.route('/scoreboard.html')
def scoreboard_html():
    from flask import Response
    return Response(_SCOREBOARD_HTML, mimetype='text/html')


@app.route('/wins')
def wins_json():
    """Grade-aware win list, sourced from event_grades.

    The grader daemon (scripts/grade_events.py, every 5 min) clusters all
    detections (PHOENIX + external) into fire events and assigns each a
    verification tier (T0..T3) plus race-validity flag. We expose:

      verified_wins         PHOENIX-led, race_valid, tier >= T1
      verified_external     >= T1 events comparator caught first (PHOENIX co-detected)
      unconfirmed_phoenix   T0 PHOENIX-only, T+72h reconcile pending
      refuted_phoenix       T0 PHOENIX-only, T+72h passed without corroboration
      external_only_wins    T0 external — comparator caught, PHOENIX missed

    The `firms_test` synthetic feed is excluded.
    """
    return _wins_json_graded()


def _wilson_ci(k, n, z=1.96):
    """Wilson score interval for binomial proportion. Returns (lo, hi) in [0,1]."""
    import math as _m
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = z * _m.sqrt(p * (1 - p) / n + z2 / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


EXCLUDED_TEST_SOURCES = {'firms_test', 'firms_stub'}


def _wins_json_graded():
    """v2: race_strict is the headline metric; refuted events are excluded
    from verified_wins even if race-valid (closes the council-found bug).
    Surfaces comparator_panel, refute_strength, biome_class, phoenix_had_coverage,
    and Wilson 95% CIs for honest precision."""
    import sqlite3, json as _json
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT * FROM event_grades "
        "WHERE first_ts > datetime('now', '-7 days') "
        "ORDER BY first_ts DESC"
    ))
    # Lookup PHOENIX detection per graded event for image_url + aoi + confidence
    phx_lookup = {}
    for r in rows:
        if not r['is_phoenix_led']:
            continue
        key = (r['representative_source'], r['first_ts'],
               round(r['cluster_lat'], 4), round(r['cluster_lng'], 4))
        if key in phx_lookup:
            continue
        match = con.execute(
            "SELECT id, aoi_id, confidence, frp_mw, temperature_c, raw_json "
            "FROM internal_fires "
            "WHERE source = ? AND ts = ? "
            "  AND ABS(lat - ?) < 0.0005 AND ABS(lng - ?) < 0.0005 "
            "LIMIT 1",
            (r['representative_source'], r['first_ts'],
             r['cluster_lat'], r['cluster_lng'])
        ).fetchone()
        phx_lookup[key] = match
    con.close()

    def _phx(r):
        if not r['is_phoenix_led']:
            return None
        key = (r['representative_source'], r['first_ts'],
               round(r['cluster_lat'], 4), round(r['cluster_lng'], 4))
        return phx_lookup.get(key)

    def _img(phx):
        try:
            if not phx: return None
            raw = phx['raw_json']
            if not raw: return None
            rd = _json.loads(raw)
            if isinstance(rd, dict):
                return rd.get('image_url')
        except Exception:
            pass
        return None

    def base(r):
        srcs = (r['corroborator_sources'] or '').split(',') if r['corroborator_sources'] else []
        phx = _phx(r)
        # comparator_panel is a JSON column; parse for client
        try:
            panel = _json.loads(r['comparator_panel']) if r['comparator_panel'] else []
        except Exception:
            panel = []
        return {
            'event_key': r['event_key'],
            'lat': r['cluster_lat'],
            'lng': r['cluster_lng'],
            'first_ts': r['first_ts'],
            'representative_source': r['representative_source'],
            'is_phoenix_led': bool(r['is_phoenix_led']),
            'aoi_id': (phx['aoi_id'] if phx else None),
            'det_id': (phx['id'] if phx else None),
            'confidence': (phx['confidence'] if phx and phx['confidence'] is not None else 0),
            'frp_mw': (phx['frp_mw'] if phx and phx['frp_mw'] is not None else 0),
            'temp_c': (phx['temperature_c'] if phx and phx['temperature_c'] is not None else 0),
            'image_url': _img(phx),
            'verification_tier': r['verification_tier'],
            'corroborator_count': r['corroborator_count'],
            'corroborator_sources': srcs,
            'has_vvf': bool(r['has_vigili_fuoco']),
            'has_news': bool(r['has_news']),
            'has_burn_scar': bool(r['has_burn_scar']),
            'has_sar': bool(r['has_sar_change']),
            'has_lst': bool(r['has_lst_anomaly']),
            'race_valid': (bool(r['race_valid']) if r['race_valid'] is not None else None),
            'race_strict': (bool(r['race_strict']) if r['race_strict'] is not None else None),
            'lead_likely_geometric': (bool(r['lead_likely_geometric'])
                                       if r['lead_likely_geometric'] is not None else None),
            'race_note': r['race_note'],
            'lead_min_vs_sensed': r['lead_min_vs_sensed'],
            'lead_min_vs_reported': r['lead_min_vs_reported'],
            'worst_capable_lead_min': r['worst_capable_lead_min'],
            'capable_comparator_count': r['capable_comparator_count'],
            'comparator_source': r['comparator_source'],
            'comparator_revisit_min': r['comparator_revisit_min'],
            'comparator_class': r['comparator_class'],
            'comparator_panel': panel,
            'below_comparator_floor': bool(r['below_comparator_floor']) if r['below_comparator_floor'] is not None else False,
            'biome_class': r['biome_class'],
            'dnbr_threshold_biome': r['dnbr_threshold_biome'],
            'wui_built_pct': r['wui_built_pct'] if 'wui_built_pct' in r.keys() else None,
            'wui_class': r['wui_class'] if 'wui_class' in r.keys() else None,
            'phoenix_had_coverage': (bool(r['phoenix_had_coverage'])
                                       if r['phoenix_had_coverage'] is not None else None),
            'refute_strength': r['refute_strength'],
            't72h_outcome': r['t72h_outcome'],
            't72h_outcome_evidence': r['t72h_outcome_evidence'],
            't72h_reconciled_at': r['t72h_reconciled_at'],
            't14d_outcome': r['t14d_outcome'],
            't14d_outcome_evidence': r['t14d_outcome_evidence'],
            't45d_outcome': r['t45d_outcome'],
            't45d_outcome_evidence': r['t45d_outcome_evidence'],
            # back-compat aliases consumed by /api/wins.csv, /wins.rss, /wins.ics
            'phoenix_ts': r['first_ts'] if r['is_phoenix_led'] else None,
            'phoenix_source': r['representative_source'] if r['is_phoenix_led'] else None,
            'ext_source': r['comparator_source'] or '',
            'ext_ts': None,
            'lead_min': r['lead_min_vs_sensed'],
            'comparator_sensed_at': None,
            'comparator_reported_at': None,
            'km': 0,
            'member_count': max(1, (r['corroborator_count'] or 0) + 1),
        }

    REFUTED_OUTCOMES = {'refuted_likely_fp', 'burn_disproven_fp', 'refuted_no_scar'}

    verified_wins = []         # PHX-led, race_valid (loose OR strict), tier >= T1, NOT refuted.
                               # The two subsets - strict and marginal - are flagged by
                               # `race_strict` so the page can badge them differently and asterisk.
    verified_external = []     # PHX-led >= T1 but not race-valid (PHX wasn't first), OR external-led >= T1
    unconfirmed_phoenix = []   # PHX-led T0, t72h pending
    refuted_phoenix = []       # PHX-led T0, t72h refuted
    unverifiable_phoenix = []  # PHX-led T0, t72h no_signal_unverifiable (cloud/gap)
    external_only = []         # external-led T0
    below_floor_phoenix = []   # PHX-led, below all comparators' detection floor

    for r in rows:
        if r['representative_source'] in EXCLUDED_TEST_SOURCES:
            continue
        b = base(r)
        tier = r['verification_tier']
        outcome = r['t72h_outcome']
        is_phx = bool(r['is_phoenix_led'])
        below_floor = bool(r['below_comparator_floor']) if r['below_comparator_floor'] is not None else False
        if is_phx:
            # PHOENIX-first win requires an EXTERNAL corroborator - not cross-PHOENIX-family
            # corroboration (which is just internal consistency). External = external satellite
            # comparator (comparator_source), VVF, news/dpc, burn-scar, SAR, LST.
            has_external_corroborator = (
                r['comparator_source'] is not None
                or r['has_vigili_fuoco']
                or r['has_news']
                or r['has_burn_scar']
                or r['has_sar_change']
                or r['has_lst_anomaly']
            )
            if (tier in ('T1', 'T2', 'T3') and outcome not in REFUTED_OUTCOMES
                    and has_external_corroborator):
                verified_wins.append(b)
            elif tier in ('T1', 'T2', 'T3'):
                # Cross-PHOENIX-family corroboration only; useful as internal-consistency
                # signal but not a "first detection vs external" win.
                verified_external.append(b)
            elif below_floor:
                below_floor_phoenix.append(b)
            elif outcome == 'no_signal_unverifiable':
                unverifiable_phoenix.append(b)
            elif outcome in REFUTED_OUTCOMES:
                refuted_phoenix.append(b)
            else:
                unconfirmed_phoenix.append(b)
        else:
            if tier in ('T1', 'T2', 'T3'):
                verified_external.append(b)
            else:
                external_only.append(b)

    # Statistical summary with Wilson 95% CIs ----------------------------------
    phx_total = (len(verified_wins) + len(verified_external) + len(unconfirmed_phoenix)
                 + len(refuted_phoenix) + len(unverifiable_phoenix) + len(below_floor_phoenix))
    # Resolved set = those with a determined T+72h outcome (refuted or confirmed via corroborator)
    resolved_confirmed = len(verified_wins) + sum(1 for w in verified_external if w['is_phoenix_led'])
    resolved_refuted = len(refuted_phoenix)
    resolved_total = resolved_confirmed + resolved_refuted
    precision_lo, precision_hi = _wilson_ci(resolved_confirmed, resolved_total)
    strict_count    = sum(1 for w in verified_wins if w.get('race_strict'))
    marginal_count  = sum(1 for w in verified_wins
                          if w.get('race_valid') and not w.get('race_strict'))
    vs_human_count  = sum(1 for w in verified_wins
                          if (w.get('race_valid') is None)
                          and (w.get('has_vvf') or w.get('has_news')))
    burnscar_count  = sum(1 for w in verified_wins
                          if w.get('has_burn_scar') and not w.get('race_valid'))
    strict_win_rate_lo, strict_win_rate_hi = _wilson_ci(strict_count, max(1, resolved_total))
    any_win_rate_lo, any_win_rate_hi = _wilson_ci(len(verified_wins), max(1, resolved_total))

    # Per-sub-detector precision
    per_detector = {}
    detector_buckets = {}
    for w in (verified_wins + verified_external + refuted_phoenix
              + unconfirmed_phoenix + unverifiable_phoenix + below_floor_phoenix):
        if not w['is_phoenix_led']:
            continue
        src = w['representative_source']
        d = detector_buckets.setdefault(src, {'confirmed': 0, 'refuted': 0,
                                              'unconfirmed': 0, 'unverifiable': 0,
                                              'below_floor': 0, 'total': 0})
        d['total'] += 1
        tier = w['verification_tier']
        if tier in ('T1', 'T2', 'T3'):
            d['confirmed'] += 1
        elif w['below_comparator_floor']:
            d['below_floor'] += 1
        elif w['t72h_outcome'] == 'no_signal_unverifiable':
            d['unverifiable'] += 1
        elif w['t72h_outcome'] in REFUTED_OUTCOMES:
            d['refuted'] += 1
        else:
            d['unconfirmed'] += 1
    for src, d in detector_buckets.items():
        resolved = d['confirmed'] + d['refuted']
        lo, hi = _wilson_ci(d['confirmed'], max(1, resolved))
        per_detector[src] = {
            **d,
            'resolved_n': resolved,
            'precision_point': (d['confirmed'] / resolved) if resolved > 0 else None,
            'precision_wilson_lo': lo,
            'precision_wilson_hi': hi,
        }

    return jsonify({
        # back-compat
        'wins': verified_wins,
        'count': len(verified_wins),
        'pair_match_count_raw': len(verified_wins),

        # v2 sections
        'verified_wins': verified_wins,
        'verified_external': verified_external,
        'unconfirmed_phoenix': unconfirmed_phoenix,
        'unconfirmed_count': len(unconfirmed_phoenix),
        'refuted_phoenix': refuted_phoenix,
        'refuted_count': len(refuted_phoenix),
        'unverifiable_phoenix': unverifiable_phoenix,
        'unverifiable_count': len(unverifiable_phoenix),
        'below_floor_phoenix': below_floor_phoenix,
        'below_floor_count': len(below_floor_phoenix),
        'external_only_wins': external_only,
        'external_only_count': len(external_only),

        # statistical summary
        'stats': {
            'phoenix_events_total': phx_total,
            'resolved_confirmed': resolved_confirmed,
            'resolved_refuted': resolved_refuted,
            'resolved_total': resolved_total,
            'precision_point': (resolved_confirmed / resolved_total) if resolved_total > 0 else None,
            'precision_wilson_lo': precision_lo,
            'precision_wilson_hi': precision_hi,
            'phoenix_first_count': len(verified_wins),
            'race_strict_count': strict_count,
            'race_marginal_count': marginal_count,
            'vs_human_count': vs_human_count,
            'burnscar_count': burnscar_count,
            'any_win_rate_point': (len(verified_wins) / resolved_total) if resolved_total > 0 else None,
            'any_win_rate_wilson_lo': any_win_rate_lo,
            'any_win_rate_wilson_hi': any_win_rate_hi,
            'strict_win_rate_point': (strict_count / resolved_total) if resolved_total > 0 else None,
            'strict_win_rate_wilson_lo': strict_win_rate_lo,
            'strict_win_rate_wilson_hi': strict_win_rate_hi,
            'note': ('Precision = confirmed / (confirmed + refuted). '
                     'Wilson 95% CI shown. Refuted means no Vigili del Fuoco, '
                     'no news, no Sentinel-2 burn-scar evidence after 72h.'),
        },
        'per_detector': per_detector,

        'lead_cap_min': 120,
        'event_cluster_km': 5.0,
        'event_cluster_min': 30,
        'race_strict_threshold': 0.5,
        'grader_version': 'v2',
    })


def _wins_json_legacy_unused():
    """Original on-the-fly clustering — kept for reference only, not routed."""
    import sqlite3, math, json as _json
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    internals = list(con.execute(
        "SELECT id, source, aoi_id, lat, lng, ts, confidence, frp_mw, "
        "temperature_c, raw_json FROM internal_fires "
        "WHERE ts > datetime('now', '-7 days') "
        "AND confidence >= 0.5 "
        "ORDER BY ts DESC"
    ))
    externals = list(con.execute(
        "SELECT source, lat, lng, ts, ingested_at FROM external_fires "
        "WHERE ts > datetime('now', '-7 days')"
    ))
    con.close()

    LEAD_MAX_MIN = 120          # 2 h — beyond this, not the same fire event
    EVENT_CLUSTER_KM = 5.0
    EVENT_CLUSTER_MIN = 30

    def _parse_t(s):
        s = s.replace('Z', '+00:00') if s.endswith('Z') else s
        t = datetime.fromisoformat(s)
        if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
        return t

    def _km(a_lat, a_lon, b_lat, b_lon):
        R = 6371.0
        dlat = math.radians(b_lat - a_lat); dlon = math.radians(b_lon - a_lon)
        s = math.sin(dlat/2)**2 + math.cos(math.radians(a_lat)) * math.cos(math.radians(b_lat)) * math.sin(dlon/2)**2
        return 2 * R * math.asin(math.sqrt(s))

    # Parse externals once
    parsed_ext = []
    for esrc, elat, elng, ets, eingested in externals:
        try:
            et = _parse_t(ets)
        except Exception:
            continue
        try:
            eit = _parse_t(eingested) if eingested else et
        except Exception:
            eit = et
        parsed_ext.append((esrc, float(elat), float(elng), et, ets, eit, eingested))

    # Pair-match each internal to nearest external w/in 5 km AND 0 < lead ≤ 120 min
    pair_wins = []
    for det_id, src, aoi, lat, lng, ts, conf, frp, temp, raw in internals:
        if _is_known_anomaly(lat, lng):
            continue
        try:
            det_t = _parse_t(ts)
        except Exception:
            continue
        best = None
        for ext_src, elat, elng, et, ets, eit, eingested in parsed_ext:
            if et <= det_t:
                continue
            lead_min = (et - det_t).total_seconds() / 60.0
            if lead_min > LEAD_MAX_MIN:
                continue
            km = _km(lat, lng, elat, elng)
            if km > 5.0:
                continue
            # lead_min_vs_sensed = algorithm-vs-algorithm (sensor acquisition time)
            # lead_min_vs_reported = wall-clock incl. comparator reporting latency
            lead_vs_reported = (eit - det_t).total_seconds() / 60.0
            reporting_latency = (eit - et).total_seconds() / 60.0
            if best is None or lead_min < best['lead_min_vs_sensed']:
                # Prefer SHORTEST lead (closest in time = most likely same fire)
                best = {'ext_source': ext_src,
                        'comparator_sensed_at': ets,
                        'comparator_reported_at': eingested or ets,
                        'reporting_latency_min': round(reporting_latency, 1),
                        'km': round(km, 2),
                        'lead_min_vs_sensed': round(lead_min, 1),
                        'lead_min_vs_reported': round(lead_vs_reported, 1),
                        # Back-compat alias for older clients/scripts
                        'ext_ts': ets,
                        'lead_min': round(lead_min, 1)}
        if best is None:
            continue
        image_url = None
        try:
            rd = _json.loads(raw) if raw else {}
            image_url = rd.get('image_url') if isinstance(rd, dict) else None
        except Exception:
            pass
        pair_wins.append({
            'det_id': det_id, 'phoenix_ts': ts, 'phoenix_source': src,
            'aoi_id': aoi, 'lat': float(lat), 'lng': float(lng),
            'confidence': float(conf or 0), 'frp_mw': float(frp or 0),
            'temp_c': float(temp or 0), 'image_url': image_url,
            **best,
            '_det_t': det_t,
        })

    # Event-clustering: collapse PHOENIX detections that are within
    # EVENT_CLUSTER_KM and EVENT_CLUSTER_MIN of each other into ONE event.
    # The event's representative win is the one with the EARLIEST detection
    # timestamp (highest lead-time).
    pair_wins.sort(key=lambda w: w['_det_t'])
    events = []
    for w in pair_wins:
        merged = False
        for ev in events:
            if abs((w['_det_t'] - ev['_first_t']).total_seconds()) > EVENT_CLUSTER_MIN * 60:
                continue
            if _km(w['lat'], w['lng'], ev['lat'], ev['lng']) > EVENT_CLUSTER_KM:
                continue
            ev['_member_count'] += 1
            # Keep the earliest detection as the representative
            if w['_det_t'] < ev['_first_t']:
                ev.update({k: v for k, v in w.items() if not k.startswith('_')})
                ev['_first_t'] = w['_det_t']
            merged = True
            break
        if not merged:
            ev = dict(w)
            ev['_first_t'] = w['_det_t']
            ev['_member_count'] = 1
            events.append(ev)

    # PHOENIX 2026-05-24 — joint multi-sat fusion: for each event, count
    # how many INDEPENDENT sensors saw it (PHOENIX subsource + each comparator
    # source within 5 km / +-2 h) and compute a weighted-average fused FRP.
    # Sensor weights reflect ground resolution (smaller pixel = higher weight).
    SENSOR_WEIGHT = {
        'slstr_frp_s3a': 1.0, 'slstr_frp_s3b': 1.0,    # 1 km
        'fci_l1c':       0.8,                            # 2 km
        'firms_viirs_snpp': 0.9, 'firms_viirs_noaa20': 0.9, 'firms_viirs_noaa21': 0.9,  # 375 m but degraded by acquisition time uncertainty
        'firms_modis_nrt': 0.5,                          # 1 km but noisier
        'mtg_af_l2':     0.7,
        'wind_diff':     0.4,
        'subpixel_v1_alpha': 0.5,
        'seviri':        0.5,                            # 3 km
    }
    for ev in events:
        # Pull all detections within 5 km / +-2 h of the event's representative point
        event_t = ev['_det_t'] if '_det_t' in ev else _parse_t(ev['phoenix_ts'])
        nearby_sources = set()
        nearby_frps = []
        # PHOENIX hit itself
        nearby_sources.add(ev['phoenix_source'])
        if ev.get('frp_mw'):
            w = SENSOR_WEIGHT.get(ev['phoenix_source'], 0.5)
            nearby_frps.append((float(ev['frp_mw']), w))
        # Comparator (always at least 1 — it's how it became a win)
        nearby_sources.add(ev.get('ext_source', ''))
        # Scan ALL externals for nearby matches and accumulate
        for ext_src, elat, elng, et, ets, eit, eingested in parsed_ext:
            if abs((et - event_t).total_seconds()) > 7200:  # +-2h
                continue
            if _km(ev['lat'], ev['lng'], elat, elng) > 5.0:
                continue
            nearby_sources.add(ext_src)
        ev['sensors_independent'] = len(nearby_sources)
        # Fused FRP if we have at least one numeric value
        if nearby_frps:
            total_w = sum(w for _, w in nearby_frps)
            ev['fused_frp_mw'] = round(
                sum(v * w for v, w in nearby_frps) / total_w, 3
            ) if total_w > 0 else None
        else:
            ev['fused_frp_mw'] = None

    # Sort newest-first for display; strip private fields
    events.sort(key=lambda e: e['phoenix_ts'], reverse=True)
    for ev in events:
        ev['member_count'] = ev.pop('_member_count')
        ev.pop('_first_t', None)
        ev.pop('_det_t', None)
    # ---------------------------------------------------------------
    # External-only wins: comparator detections that PHOENIX missed.
    # We celebrate these — credit to FIRMS / EUMETSAT / MODIS / VIIRS /
    # SLSTR / ANSA / DPC etc. when they catch a fire we did not see.
    # ---------------------------------------------------------------
    # Parse PHOENIX detections once (excluding known anomalies / FP zones)
    parsed_int = []
    for det_id, src, aoi, lat, lng, ts, conf, frp, temp, raw in internals:
        if _is_known_anomaly(lat, lng):
            continue
        try:
            it = _parse_t(ts)
        except Exception:
            continue
        parsed_int.append((float(lat), float(lng), it))

    EXT_MATCH_KM = 5.0
    EXT_MATCH_MIN = 120  # same 2h window as PHOENIX wins

    external_only = []
    for ext_src, elat, elng, et, ets, eit, eingested in parsed_ext:
        matched = False
        for ilat, ilng, it in parsed_int:
            if abs((et - it).total_seconds()) > EXT_MATCH_MIN * 60:
                continue
            if _km(elat, elng, ilat, ilng) > EXT_MATCH_KM:
                continue
            matched = True
            break
        if matched:
            continue
        external_only.append({
            'source': ext_src,
            'sensed_at': ets,
            'reported_at': eingested or ets,
            'reporting_latency_min': round((eit - et).total_seconds() / 60.0, 1),
            # back-compat
            'ts': ets,
            'lat': elat,
            'lng': elng,
            '_t': et,
        })

    # Cluster external-only events the same way: 5 km / 30 min
    external_only.sort(key=lambda w: w['_t'])
    ext_events = []
    for w in external_only:
        merged = False
        for ev in ext_events:
            if abs((w['_t'] - ev['_first_t']).total_seconds()) > EVENT_CLUSTER_MIN * 60:
                continue
            if _km(w['lat'], w['lng'], ev['lat'], ev['lng']) > EVENT_CLUSTER_KM:
                continue
            ev['_member_count'] += 1
            ev.setdefault('sources', set()).add(w['source'])
            if w['_t'] < ev['_first_t']:
                ev.update({'source': w['source'], 'ts': w['ts'],
                           'sensed_at': w.get('sensed_at', w['ts']),
                           'reported_at': w.get('reported_at', w['ts']),
                           'reporting_latency_min': w.get('reporting_latency_min', 0.0),
                           'lat': w['lat'], 'lng': w['lng']})
                ev['_first_t'] = w['_t']
            merged = True
            break
        if not merged:
            ev = {'source': w['source'], 'ts': w['ts'],
                  'sensed_at': w.get('sensed_at', w['ts']),
                  'reported_at': w.get('reported_at', w['ts']),
                  'reporting_latency_min': w.get('reporting_latency_min', 0.0),
                  'lat': w['lat'], 'lng': w['lng'],
                  '_first_t': w['_t'], '_member_count': 1,
                  'sources': {w['source']}}
            ext_events.append(ev)

    ext_events.sort(key=lambda e: e['ts'], reverse=True)
    for ev in ext_events:
        ev['member_count'] = ev.pop('_member_count')
        ev['sources'] = sorted(ev.pop('sources'))
        ev.pop('_first_t', None)

    return jsonify({
        'wins': events,
        'count': len(events),
        'pair_match_count_raw': len(pair_wins),
        'external_only_wins': ext_events,
        'external_only_count': len(ext_events),
        'lead_cap_min': LEAD_MAX_MIN,
        'event_cluster_km': EVENT_CLUSTER_KM,
        'event_cluster_min': EVENT_CLUSTER_MIN,
    })


_WINS_HTML = """<!DOCTYPE html>
<html><head>
<title>PHOENIX Sicily — wildfire detection ledger</title>
<style>
  body{font-family:Segoe UI,sans-serif;background:#1a2530;color:#ecf0f1;padding:20px;margin:0;max-width:1100px}
  h1{color:#2ecc71;margin-bottom:6px}
  h1 .badge{font-size:.5em;background:#2ecc71;color:#1a2530;padding:3px 10px;border-radius:14px;vertical-align:middle;margin-left:8px}
  h2{margin-top:32px}
  .sub{color:#bdc3c7;margin-bottom:18px;line-height:1.45}
  a{color:#3498db;text-decoration:none}
  a:hover{text-decoration:underline}
  .win{border:1px solid #2ecc71;border-radius:8px;padding:14px;margin:12px 0;background:#22313e;display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
  .win img{width:240px;height:auto;border-radius:6px;border:1px solid #34495e}
  .meta{flex:1;min-width:280px}
  .lead{color:#2ecc71;font-size:1.3em;font-weight:bold;margin-bottom:4px}
  .src{color:#f39c12}
  .kv{font-size:.9em;color:#bdc3c7;margin:3px 0}
  .empty{padding:20px;color:#7f8c8d;text-align:center;border:1px dashed #34495e;border-radius:8px}
  .tier{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75em;font-weight:bold;margin-right:6px;vertical-align:middle}
  /* Color-blind-safe palette (Wong 2011): grey, blue, teal, vermillion */
  .tier-T0{background:#7f8c8d;color:#1a2530}
  .tier-T1{background:#3498db;color:#fff}
  .tier-T2{background:#1abc9c;color:#1a2530}
  .tier-T3{background:#e67e22;color:#1a2530;border:1px solid #d35400}
  .race-valid{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75em;background:#2ecc71;color:#1a2530;margin-right:6px;font-weight:bold}
  .race-geometric{display:inline-block;padding:2px 8px;border-radius:10px;font-size:.75em;background:#e67e22;color:#fff;margin-right:6px}
  details{margin:12px 0;padding:12px;background:#22313e;border:1px solid #34495e;border-radius:8px}
  summary{cursor:pointer;color:#ecf0f1;font-weight:bold;font-size:1.05em;padding:4px 0}
  summary:hover{color:#3498db}
  .compact{font-size:.85em;color:#bdc3c7}
  .compact .win{padding:8px 10px;margin:6px 0;background:#1f2a36;border-color:#34495e;border-width:1px}
  .compact .lead{font-size:1em;color:#bdc3c7}
  table{width:100%;border-collapse:collapse;font-size:.85em;margin-top:8px}
  th,td{padding:4px 8px;border-bottom:1px solid #34495e;text-align:left}
  th{color:#95a5a6}
  .nav{padding:8px 0;border-bottom:1px solid #34495e;margin-bottom:18px}
  .profile{display:flex;gap:2px;margin:8px 0;font-size:.85em;border-radius:6px;overflow:hidden}
  .profile div{flex:1;padding:8px 10px;color:#fff;text-align:center;min-width:60px}
  .profile .verified{background:#27ae60}
  .profile .codetected{background:#2980b9}
  .profile .externalonly{background:#d35400}
  .profile .unconfirmed{background:#7f8c8d}
  .profile .unverifiable{background:#8e44ad}
  .profile .belowfloor{background:#34495e}
  .profile .refuted{background:#c0392b}
  .profile .label{font-size:.7em;display:block;opacity:.85}
  .profile .num{font-size:1.3em;font-weight:bold}
  .detchip{display:inline-block;margin:4px 6px 4px 0;padding:8px 12px;background:#1f2a36;border:1px solid #34495e;border-radius:6px;font-size:.85em;font-family:Consolas,monospace}
  .detchip b{color:#ecf0f1}
  .detchip .ci{color:#95a5a6}
  .panel{font-size:.8em;color:#95a5a6;margin-top:6px;background:#1a2530;padding:6px 8px;border-radius:4px;font-family:Consolas,monospace}
  .panel .row{margin:2px 0}
  .panel .race-strict{color:#2ecc71;font-weight:bold}
  .panel .race-marginal{color:#e67e22}
  .panel .below-floor{color:#7f8c8d;font-style:italic}
  .pct{font-family:Consolas,monospace;color:#ecf0f1}
</style></head><body>
<h1 id="page-title">PHOENIX Sicily — wildfire detection ledger</h1>
<div class="nav">
  <a href="/" data-i18n="nav_map">← Live map</a> &nbsp;·&nbsp;
  <a href="/accuracy.html" data-i18n="nav_accuracy">Per-feed accuracy</a> &nbsp;·&nbsp;
  <a href="/come-funziona" data-i18n="nav_methodology">Methodology</a> &nbsp;·&nbsp;
  <a href="/falsi-positivi" data-i18n="nav_fp_catalog">FP catalog</a> &nbsp;·&nbsp;
  <a href="/wins">JSON</a> &nbsp;·&nbsp;
  <a href="/api/event_grades.csv">CSV</a> &nbsp;·&nbsp;
  <a href="https://github.com/markl02us/persistent-thermal-sources-sicily">GitHub</a>
  <span style="float:right">
    <button id="lang-en" aria-label="Switch to English">EN</button>
    <button id="lang-it" aria-label="Passa all'italiano">IT</button>
  </span>
</div>
<p class="sub" id="intro-1">
  <b data-i18n="intro_1_b">What this page is.</b> <span data-i18n="intro_1">A weekly audit of every fire signal PHOENIX produced for Sicily —
  algorithmic leads where our detector beat a comparator within its revisit window, co-detections
  with authoritative sources, fires we missed, and our own sole-reporter detections that 72 hours
  later either still have no corroborating evidence or have been refuted.</span>
</p>
<p class="sub" id="intro-2">
  <b data-i18n="intro_2_b">What it isn't.</b> <span data-i18n="intro_2">A leaderboard. PHOENIX is an experimental academic system; the
  authoritative wildfire authority for Sicily is <b>115 (Vigili del Fuoco)</b>. Every claim below
  is reproducible from <a href="/api/event_grades.csv">the linked CSV</a>.</span>
</p>

<!-- System profile strip — equal visual weight for every outcome category -->
<h3 style="margin-top:18px;color:#ecf0f1;font-size:1em">Last 7 days — every event by outcome</h3>
<div class="profile" id="profile-strip"></div>

<!-- Precision band with Wilson 95% CI -->
<div id="precision-band" style="margin:14px 0;padding:12px;background:#22313e;border-left:4px solid #3498db;border-radius:4px"></div>

<!-- Per-PHOENIX-sub-detector precision -->
<details open style="margin-top:14px">
  <summary style="color:#ecf0f1">📊 PHOENIX sub-detector precision (resolved subset)</summary>
  <p class="sub" style="margin-top:6px">
    Per the per-feed accuracy rule, every PHOENIX sub-detector gets the same Wilson 95% CI
    treatment as the external comparators. Resolved = confirmed (T1+T2+T3) + refuted (T+72h with no evidence).
  </p>
  <div id="per-detector"></div>
</details>

<!-- Tier legend — T0 first, since that's the modal outcome -->
<details style="margin-top:14px">
  <summary style="color:#ecf0f1">Tier definitions (T0 → T3)</summary>
  <table>
    <tr><th>Tier</th><th>Meaning</th></tr>
    <tr><td><span class="tier tier-T0">T0</span></td><td>Sole reporter — no independent corroborator within 5&nbsp;km / ±2&nbsp;h. Most events sit here. Most are false positives or below comparator detection floors.</td></tr>
    <tr><td><span class="tier tier-T1">T1</span></td><td>≥1 independent satellite family corroborated within 5&nbsp;km / ±2&nbsp;h.</td></tr>
    <tr><td><span class="tier tier-T2">T2</span></td><td>Vigili del Fuoco match (±24&nbsp;h) <i>or</i> Italian news / Protezione Civile match.</td></tr>
    <tr><td><span class="tier tier-T3">T3</span></td><td>Burn-scar verified (Sentinel-2 dNBR &gt; biome threshold: 0.27 forest / 0.18 shrub / 0.12 grass) <i>or</i> Vigili del Fuoco + ≥2 satellite sources.</td></tr>
  </table>
  <p class="sub" style="margin-top:14px">
    <b style="color:#2ecc71">Race-strict</b> = PHOENIX's lead beats the comparator AND is less than 50% of the comparator's revisit period —
    a genuine algorithmic advantage. <b style="color:#e67e22">Likely geometric</b> = lead exceeds revisit (we won because their sensor hadn't passed yet, not because our algorithm was faster).
    Vigili del Fuoco, ANSA news, and similar <i>human-dispatch</i> / <i>social</i> sources do not qualify for race-strict —
    their revisit isn't a sensor cadence, it's reporting latency. They corroborate truth (T2), but they don't race satellites.
  </p>
  <p class="sub">
    <b>Two clocks:</b> sensor-acquisition Δ (algorithm vs algorithm) and feed-delivered Δ (wall clock vs user). We always show both.
  </p>
  <p class="sub">
    <b>Multi-stage reconcile:</b> T+72&nbsp;h preliminary, T+14&nbsp;d after the post-fire Sentinel-2 pass typically clears clouds, T+45&nbsp;d for long-tail confirmation. Each pass can upgrade or downgrade. Cloud-occluded events flagged as <i>unverifiable</i>, not refuted.
  </p>
</details>

<section role="region" aria-label="Confirmed fires Sicily this week">
<h2 style="color:#e67e22" data-i18n="auth_h2">🚒 Confirmed fires in Sicily this week (authoritative sources)</h2>
<p class="sub" style="margin-top:-6px" data-i18n="auth_p">
  Union of every fire that Vigili del Fuoco, NASA FIRMS, EUMETSAT, Sentinel-3 SLSTR, or other
  authoritative sources reported. These are the ground-truth events for the week; PHOENIX's
  contribution to each (co-detected / missed) is shown per row.
</p>
<div id="authoritative-totals" class="compact"></div>
<div id="authoritative-list"></div>
</section>

<section role="region" aria-label="PHOENIX-first algorithmic leads">
<h2 style="color:#2ecc71" data-i18n="race_h2">✅ PHOENIX-first algorithmic leads (corroborated)</h2>
<p class="sub" style="margin-top:-6px" data-i18n="race_p">
  Events where PHOENIX detected before a corroborator AND the event was confirmed (≥T1, not refuted at T+72h).
  Two-tier badge: <b style="color:#2ecc71">RACE-STRICT</b> means PHOENIX's lead was &lt;50% of the comparator's revisit window
  — algorithmic advantage clearly exceeds orbital geometry. <b style="color:#e67e22">Race-marginal</b>*
  means PHOENIX still detected first, but by a margin comparable to the comparator's poll cadence
  — within revisit, real first-detection, but the strict bootstrap test does not yet separate it from chance.
  Both stay listed as wins; the asterisk explains the methodology nuance.
</p>
<div id="totals" class="compact"></div>
<div id="wins-list"></div>
<p class="sub" style="font-size:.8em;color:#95a5a6;margin-top:8px">
  <b>Asterisk notes (methodology nuance, not retraction):</b><br>
  <b>Race-marginal*</b> — PHOENIX detected before the satellite comparator, but the lead was ≥50% of
  the comparator's nominal revisit cadence. The null-distribution bootstrap on race-strict (lead &lt;50%
  revisit) yields p=1.0 — the strict subset is statistically indistinguishable from chance under random
  comparator-time shuffling. These events are real first-detections; the asterisk flags that the
  algorithmic margin is small relative to comparator poll noise.<br>
  <b>First vs VVF* / First vs news*</b> — PHOENIX produced the detection before the Vigili del Fuoco
  dispatch report (or news article) for the same fire. Human-dispatch sources don't have a sensor-cadence
  revisit, so the satellite race-strict bar doesn't apply. The lead is still a real algorithm-vs-reporting
  advantage. PHOENIX-first wins require ≥1 external corroborator (sat / VVF / news / burn-scar);
  cross-PHOENIX-family corroboration counts as internal consistency, not a "win", and is moved to the
  co-detected section.
</p>
</section>

<h2 style="color:#3498db">🤝 Co-detected with comparator</h2>
<p class="sub" style="margin-top:-6px">
  Fires confirmed by ≥1 independent comparator family within 5&nbsp;km / ±2&nbsp;h
  where PHOENIX did not race-win. Comparator may have led; we co-detected. Still real fires, fully credited.
</p>
<div id="verified-external-totals" class="compact"></div>
<div id="verified-external-list"></div>

<h2 style="color:#f39c12">👏 Caught by others, missed by PHOENIX</h2>
<p class="sub" style="margin-top:-6px">
  Real fires detected by NASA, EUMETSAT, Copernicus, Vigili del Fuoco, or other
  comparators that PHOENIX did NOT independently flag within ±5&nbsp;km / ±2&nbsp;h. These
  are honest coverage gaps — full credit to the teams that caught them. Each row notes whether PHOENIX even had a detector running at the time.
</p>
<div id="external-only-totals" class="compact"></div>
<div id="external-only-list"></div>

<!-- Refuted is OPEN BY DEFAULT — council priority. Hiding bad news behind a click is the textbook anti-pattern. -->
<details open style="border-color:#c0392b">
  <summary style="color:#e74c3c">❌ Refuted at T+72h (no corroborating evidence — likely false positives)</summary>
  <p class="sub" style="margin-top:6px">
    Sole-reporter PHOENIX detections where 72 hours have passed and no Vigili del Fuoco, news,
    or Sentinel-2 burn-scar evidence emerged. These are likely false positives — they bound our precision.
    Published openly because hiding FPs is the worst pattern.
  </p>
  <div id="refuted-totals" class="compact"></div>
  <div id="refuted-list" class="compact"></div>
</details>

<details style="margin-top:14px">
  <summary style="color:#bdc3c7">🟡 Unconfirmed PHOENIX leads (T0, awaiting T+72h reconcile)</summary>
  <p class="sub" style="margin-top:6px">
    Sole-reporter PHOENIX detections younger than 72&nbsp;h. Most will resolve to refuted based on the historical tail.
  </p>
  <div id="unconfirmed-totals" class="compact"></div>
  <div id="unconfirmed-list" class="compact"></div>
</details>

<details style="margin-top:14px">
  <summary style="color:#bdc3c7">⚠️ Unverifiable (cloud-blocked or no Sentinel-2 scene)</summary>
  <p class="sub" style="margin-top:6px">
    Sole-reporter PHOENIX detections where no clear Sentinel-2 pass was available in the reconcile window.
    These are <i>not</i> refuted — we just can't verify. Counts separately from the FP precision denominator.
  </p>
  <div id="unverifiable-totals" class="compact"></div>
  <div id="unverifiable-list" class="compact"></div>
</details>

<details style="margin-top:14px">
  <summary style="color:#bdc3c7">📉 Below comparator detection floor</summary>
  <p class="sub" style="margin-top:6px">
    Sole-reporter PHOENIX detections where the event's FRP is below the physical detection floor of every comparator that could have seen it.
    A comparator literally couldn't have caught these — counted separately, not against precision.
  </p>
  <div id="below-floor-totals" class="compact"></div>
  <div id="below-floor-list" class="compact"></div>
</details>

<div style="margin-top:36px;padding:14px;background:#1f2a36;border:1px solid #34495e;border-radius:8px;font-size:.85em;color:#bdc3c7">
  <b>Methodology &amp; reproducibility.</b>
  Grading code: <a href="https://github.com/markl02us/persistent-thermal-sources-sicily">github.com/markl02us/persistent-thermal-sources-sicily</a> (MIT/CC-BY 4.0).
  Schema:&nbsp;<a href="/api/event_grades">/api/event_grades</a> · CSV:&nbsp;<a href="/api/event_grades.csv">phoenix_event_grades.csv</a>.
  <br>
  <b>Reproduce these grades yourself.</b>
  Daily raw-input snapshots (CSV) are published at
  <a href="/data/snapshots/">/data/snapshots/</a>. Each contains
  <code>internal_fires.csv</code>, <code>external_fires.csv</code>,
  <code>corroboration_signals.csv</code>, the published
  <code>event_grades.csv</code>, plus <code>SHA256SUMS</code>. Run
  <code>scripts/regrade.py</code> (in the repo) against the raw inputs and
  diff against the published grades — should produce zero mismatches.
  <br>
  Comparator revisit cadences, biome dNBR thresholds, comparator-class rules,
  and the strict-race definition (lead &lt; 50% of comparator revisit) are
  documented in <code>scripts/grade_events.py</code>. Found a mismatch?
  Open an issue or email markl02us+phoenix@yahoo.com.
</div>

<script>
const SRC_LABEL = {
  'firms_viirs_snpp':  '🛰️ NASA FIRMS (VIIRS SNPP)',
  'firms_viirs_noaa20':'🛰️ NASA FIRMS (VIIRS NOAA-20)',
  'firms_viirs_noaa21':'🛰️ NASA FIRMS (VIIRS NOAA-21)',
  'firms_viirs_noaa20_global':'🛰️ NASA FIRMS (VIIRS NOAA-20 global)',
  'firms_modis':       '🛰️ NASA FIRMS (MODIS)',
  'firms_modis_nrt':   '🛰️ NASA FIRMS (MODIS NRT)',
  'firms_landsat':     '🛰️ NASA FIRMS (Landsat)',
  'mtg_af_l2':         '🛰️ EUMETSAT MTG-AF-L2',
  'effis':             '🇪🇺 Copernicus EFFIS',
  'slstr_frp_s3a':     '🛰️ Sentinel-3A SLSTR FRP',
  'slstr_frp_s3b':     '🛰️ Sentinel-3B SLSTR FRP',
  'ansa_news':         '📰 ANSA Sicilia news',
  'italian_news_rss':  '📰 Italian news RSS',
  'vigili_fuoco':      '🚒 Vigili del Fuoco',
  'dpc':               '🇮🇹 Protezione Civile',
  'sentinel1_sar_change':'🛰️ Sentinel-1 SAR change',
  'tropomi_hcho_anomaly':'🛰️ Sentinel-5P TROPOMI HCHO',
  'subpixel_v1_alpha': 'PHOENIX subpixel_v1',
  'wind_diff':         'PHOENIX wind_diff',
  'fci_l1c':           'PHOENIX FCI L1C',
  'adr':               'PHOENIX ADR',
};
// Bilingual i18n (EN default, IT toggle). Keep strings short.
const I18N = {
  en: {
    nav_map: '← Live map', nav_accuracy: 'Per-feed accuracy',
    nav_methodology: 'Methodology', nav_fp_catalog: 'FP catalog',
    intro_1_b: 'What this page is.',
    intro_1: 'A weekly audit of every fire signal PHOENIX produced for Sicily — algorithmic leads where our detector beat a comparator within its revisit window, co-detections with authoritative sources, fires we missed, and our own sole-reporter detections that 72 hours later either still have no corroborating evidence or have been refuted.',
    intro_2_b: "What it isn't.",
    intro_2: 'A leaderboard. PHOENIX is an experimental academic system; the authoritative wildfire authority for Sicily is <b>115 (Vigili del Fuoco)</b>. Every claim below is reproducible from <a href="/api/event_grades.csv">the linked CSV</a>.',
    auth_h2: '🚒 Confirmed fires in Sicily this week (authoritative sources)',
    auth_p: 'Union of every fire that Vigili del Fuoco, NASA FIRMS, EUMETSAT, Sentinel-3 SLSTR, or other authoritative sources reported. These are the ground-truth events for the week; PHOENIX\\'s contribution to each (co-detected / missed) is shown per row.',
    race_h2: '✅ Race-strict algorithmic leads (PHOENIX-led)',
    race_p: 'PHOENIX detected before a satellite comparator AND the lead is &lt;50% of the comparator\\'s revisit window. Refuted events excluded.',
  },
  it: {
    nav_map: '← Mappa', nav_accuracy: 'Precisione per fonte',
    nav_methodology: 'Metodologia', nav_fp_catalog: 'Catalogo falsi positivi',
    intro_1_b: 'Cosa è questa pagina.',
    intro_1: 'Un controllo settimanale di ogni segnale di incendio prodotto da PHOENIX per la Sicilia — anticipi algoritmici dove il nostro rilevatore ha battuto un comparatore entro il suo periodo di rivisita, co-rilevamenti con fonti autoritative, incendi mancati, e le nostre stesse rilevazioni sole-reporter che dopo 72 ore non hanno ancora evidenza di conferma o sono state confutate.',
    intro_2_b: 'Cosa non è.',
    intro_2: 'Una classifica. PHOENIX è un sistema accademico sperimentale; l\\'autorità ufficiale antincendio per la Sicilia è <b>il 115 (Vigili del Fuoco)</b>. Ogni affermazione qui sotto è riproducibile da <a href="/api/event_grades.csv">il CSV collegato</a>.',
    auth_h2: '🚒 Incendi confermati in Sicilia questa settimana (fonti autoritative)',
    auth_p: 'Unione di ogni incendio segnalato da Vigili del Fuoco, NASA FIRMS, EUMETSAT, Sentinel-3 SLSTR o altre fonti autoritative. Sono gli eventi di ground-truth della settimana; il contributo di PHOENIX a ciascuno (co-rilevato / mancato) è mostrato per riga.',
    race_h2: '✅ Anticipi algoritmici stretti (PHOENIX in testa)',
    race_p: 'PHOENIX ha rilevato prima di un comparatore satellitare E l\\'anticipo è &lt;50% del periodo di rivisita del comparatore. Eventi confutati esclusi.',
  }
};
function applyLang(lang){
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const k = el.getAttribute('data-i18n');
    if (I18N[lang] && I18N[lang][k]) el.innerHTML = I18N[lang][k];
  });
  localStorage.setItem('phoenix_lang', lang);
}
const initialLang = localStorage.getItem('phoenix_lang') || ((navigator.language||'en').startsWith('it') ? 'it' : 'en');
document.addEventListener('DOMContentLoaded', () => {
  applyLang(initialLang);
  document.getElementById('lang-en')?.addEventListener('click', () => applyLang('en'));
  document.getElementById('lang-it')?.addEventListener('click', () => applyLang('it'));
});

function label(s){ return SRC_LABEL[s] || (s||'').toUpperCase(); }
function tierBadge(t){ return `<span class="tier tier-${t}" aria-label="Tier ${t}">${t}</span>`; }
function provenance(w){
  const d = w.first_ts ? w.first_ts.slice(0, 10) : '';
  const firms = `https://firms.modaps.eosdis.nasa.gov/map/#d:${d};@${w.lng},${w.lat},10z`;
  const cop = `https://browser.dataspace.copernicus.eu/?zoom=13&lat=${w.lat}&lng=${w.lng}&fromTime=${d}T00:00:00.000Z&toTime=${d}T23:59:59.999Z&datasetId=S2L2A`;
  return `<a href="${firms}" target="_blank" aria-label="Verify on NASA FIRMS map">🛰️ FIRMS</a> · <a href="${cop}" target="_blank" aria-label="Verify on Copernicus Browser">🌍 Copernicus Browser</a>`;
}
function raceBadge(w){
  if(w.race_strict === true) return '<span class="race-valid" aria-label="Race-strict win">RACE-STRICT</span>';
  if(w.race_valid === true) return '<span class="race-geometric" aria-label="Race-marginal — see footnote">Race-marginal*</span>';
  if(w.has_vvf) return '<span class="race-geometric" aria-label="First-detected before Vigili del Fuoco report — see footnote">First vs VVF*</span>';
  if(w.has_news) return '<span class="race-geometric" aria-label="First-detected before news — see footnote">First vs news*</span>';
  if(w.has_burn_scar) return '<span class="race-geometric" aria-label="Burn-scar confirmed by Sentinel-2">Burn-scar confirmed</span>';
  if(w.lead_likely_geometric === true) return '<span class="race-geometric">Likely geometric</span>';
  return '';
}
function corroborators(w){
  const c = w.corroborator_sources || [];
  if(c.length === 0) return '<i style="color:#7f8c8d">no independent corroborator yet</i>';
  return c.map(label).join(', ');
}
function fmtTs(s){ return s ? new Date(s).toLocaleString() : '—'; }
function map(w){ return `<a href="https://www.google.com/maps?q=${w.lat},${w.lng}" target="_blank">Open map</a>`; }
function pct(p){ return p===null||p===undefined ? '—' : (p*100).toFixed(2)+'%'; }
function ci(lo, hi){ return `[${(lo*100).toFixed(2)}%–${(hi*100).toFixed(2)}%]`; }
function comparatorPanel(w){
  const p = w.comparator_panel || [];
  if(p.length === 0) return '';
  const rows = p.map(c => {
    let cls = '';
    if(c.below_floor) cls = 'below-floor';
    else if(c.capable && c.lead_min < c.revisit_min * 0.5) cls = 'race-strict';
    else if(c.capable) cls = 'race-marginal';
    const flag = c.below_floor ? ' below-floor'
               : (!c.capable && c.lead_min <= 0) ? ' before-phx'
               : '';
    return `<div class="row ${cls}">${label(c.source)} · revisit ${c.revisit_min}min · lead ${c.lead_min}min · class ${c.class || '?'}${flag}</div>`;
  });
  return `<div class="panel"><b>Comparator panel (all capable sources within 5km/±2h):</b>${rows.join('')}</div>`;
}

function renderRaceValid(arr){
  return arr.map(w=>{
    const algoLead = w.lead_min_vs_sensed;
    const wallLead = w.lead_min_vs_reported;
    const comp = label(w.comparator_source);
    const biome = w.biome_class ? `<div class="kv"><b>Biome:</b> ${w.biome_class} (dNBR threshold ${w.dnbr_threshold_biome})</div>` : '';
    const WUI_LABEL = {U:'Urban (dense)', I:'WUI Interface', W:'Wildland', N:'Other'};
    const wuiBadge = w.wui_class ? ` · <b>WUI:</b> ${WUI_LABEL[w.wui_class] || w.wui_class} (${(w.wui_built_pct||0)}% built)` : '';
    return `
    <div class="win">
      ${w.image_url ? `<img src="${w.image_url}" alt="thermal crop"/>` : ''}
      <div class="meta">
        <div class="lead">${tierBadge(w.verification_tier)} ${raceBadge(w)} +${algoLead} min vs <span class="src">${comp}</span></div>
        <div class="kv">${w.race_note || ''}</div>
        <div class="kv"><b>Algorithm Δ (sensor-acquisition):</b> +${algoLead} min</div>
        ${wallLead !== null ? `<div class="kv"><b>Wall-clock Δ (feed-delivered):</b> +${wallLead} min</div>` : ''}
        <div class="kv"><b>PHOENIX detected at:</b> ${fmtTs(w.first_ts)}</div>
        <div class="kv"><b>Source:</b> ${label(w.representative_source)} · <b>Conf:</b> ${((w.confidence||0)*100).toFixed(0)}% · <b>FRP:</b> ${(w.frp_mw||0).toFixed(2)} MW</div>
        <div class="kv"><b>Location:</b> ${w.lat.toFixed(4)} °N, ${w.lng.toFixed(4)} °E${w.aoi_id ? ' ('+w.aoi_id+')' : ''}</div>
        ${biome}${wuiBadge ? `<div class="kv">${wuiBadge.substring(3)}</div>` : ''}
        <div class="kv"><b>Independent corroborators:</b> ${corroborators(w)}</div>
        ${w.t72h_outcome ? `<div class="kv"><b>T+72h:</b> ${w.t72h_outcome} <span style="color:#7f8c8d">(${w.t72h_outcome_evidence || ''})</span></div>` : ''}
        ${w.t14d_outcome ? `<div class="kv"><b>T+14d:</b> ${w.t14d_outcome}</div>` : ''}
        ${comparatorPanel(w)}
        <div class="kv">${map(w)} · ${provenance(w)}</div>
      </div>
    </div>`;
  }).join('');
}

function renderCoDetected(arr){
  return arr.map(w=>{
    return `
    <div class="win" style="border-color:#3498db;background:#1f2a36">
      ${w.image_url ? `<img src="${w.image_url}" alt="thermal crop"/>` : ''}
      <div class="meta">
        <div class="lead" style="color:#3498db">${tierBadge(w.verification_tier)} ${raceBadge(w)} ${w.is_phoenix_led ? 'PHOENIX co-detected' : 'Comparator-led'}</div>
        <div class="kv"><b>First detection at:</b> ${fmtTs(w.first_ts)} <span style="color:#7f8c8d">by ${label(w.representative_source)}</span></div>
        ${w.race_note ? `<div class="kv">${w.race_note}</div>` : ''}
        <div class="kv"><b>Independent corroborators:</b> ${corroborators(w)}</div>
        <div class="kv"><b>Location:</b> ${w.lat.toFixed(4)} °N, ${w.lng.toFixed(4)} °E${w.aoi_id ? ' ('+w.aoi_id+')' : ''}</div>
        ${w.t72h_outcome ? `<div class="kv"><b>T+72h:</b> ${w.t72h_outcome}</div>` : ''}
        ${comparatorPanel(w)}
        <div class="kv">${map(w)} · ${provenance(w)}</div>
      </div>
    </div>`;
  }).join('');
}

function renderExternalOnly(arr){
  return arr.map(w=>{
    const coverage = w.phoenix_had_coverage === true
      ? '<span style="color:#e67e22">PHOENIX detector WAS running — algorithm gap, not a data-feed gap.</span>'
      : w.phoenix_had_coverage === false
        ? '<span style="color:#3498db">PHOENIX detector was NOT running during this acquisition — data-feed gap, not algorithm gap.</span>'
        : '<span style="color:#7f8c8d">PHOENIX coverage at this time: unknown.</span>';
    return `
    <div class="win" style="border-color:#f39c12;background:#2a2418">
      <div class="meta">
        <div class="lead" style="color:#f39c12">${tierBadge(w.verification_tier)} ✅ Caught by ${label(w.representative_source)}</div>
        <div class="kv"><b>Sensor acquired at:</b> ${fmtTs(w.first_ts)}</div>
        <div class="kv"><b>Where:</b> ${w.lat.toFixed(4)} °N, ${w.lng.toFixed(4)} °E</div>
        <div class="kv"><b>Other corroborators:</b> ${corroborators(w)}</div>
        ${w.t72h_outcome ? `<div class="kv"><b>T+72h:</b> ${w.t72h_outcome}</div>` : ''}
        <div class="kv">${coverage}</div>
        <div class="kv">${map(w)} · ${provenance(w)}</div>
      </div>
    </div>`;
  }).join('');
}

function renderAuthoritative(d){
  // Union of every event with an authoritative-source confirmation:
  // external-only catches + co-detected. Sort newest-first.
  const all = [...(d.external_only_wins||[]), ...(d.verified_external||[])];
  all.sort((a,b) => (b.first_ts||'').localeCompare(a.first_ts||''));
  if(all.length === 0) return '<div class="empty">No authoritative confirmations in the last 7 days.</div>';
  return all.map(w => {
    const phx = w.is_phoenix_led
      ? '<span style="color:#2ecc71">PHOENIX co-detected</span>'
      : (w.phoenix_had_coverage === true
          ? '<span style="color:#e74c3c">PHOENIX missed (algorithm gap)</span>'
          : w.phoenix_had_coverage === false
            ? '<span style="color:#95a5a6">PHOENIX missed (no detector coverage)</span>'
            : '<span style="color:#95a5a6">PHOENIX coverage unknown</span>');
    return `
    <div class="win" style="border-color:#e67e22;background:#2a2418">
      <div class="meta">
        <div class="lead" style="color:#e67e22">${tierBadge(w.verification_tier)} ${label(w.representative_source)}</div>
        <div class="kv"><b>Sensor acquired at:</b> ${fmtTs(w.first_ts)}</div>
        <div class="kv"><b>Where:</b> ${w.lat.toFixed(4)} °N, ${w.lng.toFixed(4)} °E</div>
        <div class="kv"><b>PHOENIX contribution:</b> ${phx}</div>
        <div class="kv"><b>All corroborators:</b> ${corroborators(w)}</div>
        <div class="kv">${map(w)} · ${provenance(w)}</div>
      </div>
    </div>`;
  }).join('');
}

function renderCompactRow(w){
  return `
    <div class="win">
      <div class="meta">
        <div>${tierBadge(w.verification_tier)} <b>${label(w.representative_source)}</b> at ${fmtTs(w.first_ts)} · ${w.lat.toFixed(3)}°N ${w.lng.toFixed(3)}°E</div>
        <div class="kv">conf ${((w.confidence||0)*100).toFixed(0)}% · FRP ${(w.frp_mw||0).toFixed(2)} MW · ${map(w)}${w.t72h_outcome ? ' · T+72h: '+w.t72h_outcome : ''}</div>
      </div>
    </div>`;
}

function renderProfile(d){
  const sections = [
    ['verified',     'Race-strict<br>leads',    d.verified_wins.length],
    ['codetected',   'Co-detected',             d.verified_external.length],
    ['externalonly', 'Missed by<br>PHOENIX',    d.external_only_wins.length],
    ['unconfirmed',  'Pending<br>T+72h',        d.unconfirmed_phoenix.length],
    ['unverifiable', 'Unverifiable<br>(cloud)', (d.unverifiable_phoenix||[]).length],
    ['belowfloor',   'Below<br>floor',          (d.below_floor_phoenix||[]).length],
    ['refuted',      'Refuted<br>(likely FP)',  d.refuted_phoenix.length],
  ];
  return sections.map(([cls, lab, n]) =>
    `<div class="${cls}"><div class="num">${n}</div><div class="label">${lab}</div></div>`
  ).join('');
}

function renderPrecisionBand(d){
  const s = d.stats || {};
  if(!s.resolved_total){
    return `<b>Honest precision needs ≥1 resolved event.</b> Currently ${s.phoenix_events_total||0} PHOENIX events in 7d; none yet have a T+72h verdict.`;
  }
  const ptStr = pct(s.precision_point);
  const ciStr = ci(s.precision_wilson_lo, s.precision_wilson_hi);
  const anyPt = pct(s.any_win_rate_point);
  const anyCi = ci(s.any_win_rate_wilson_lo, s.any_win_rate_wilson_hi);
  const strictPt = pct(s.strict_win_rate_point);
  const strictCi = ci(s.strict_win_rate_wilson_lo, s.strict_win_rate_wilson_hi);
  return `
    <b>Resolved-set precision:</b> <span class="pct">${ptStr}</span> <span style="color:#95a5a6">${ciStr}, n=${s.resolved_total} resolved of ${s.phoenix_events_total} PHOENIX events</span><br>
    <b>PHOENIX-first wins (any margin):</b> <span class="pct">${s.phoenix_first_count||0}</span> &nbsp; (rate ${anyPt} <span style="color:#95a5a6">${anyCi}</span>) — events where PHOENIX detected before the comparator within its revisit window.<br>
    <b>Race-strict subset:</b> <span class="pct">${s.race_strict_count||0}</span> of ${s.phoenix_first_count||0} <span style="color:#95a5a6">(rate ${strictPt} ${strictCi})</span> — lead &lt;50% of comparator revisit (statistically separable from poll-timing noise).<br>
    <span class="kv" style="color:#95a5a6;font-size:.85em">${s.note || ''}</span>
  `;
}

function renderPerDetector(per){
  if(!per || Object.keys(per).length === 0) return '<div class="empty">No PHOENIX sub-detector activity in 7d.</div>';
  return Object.entries(per).sort((a,b)=>b[1].total - a[1].total).map(([src, d]) => {
    const point = d.precision_point === null ? 'n/a' : pct(d.precision_point);
    const interval = ci(d.precision_wilson_lo, d.precision_wilson_hi);
    return `<div class="detchip">
      <b>${label(src)}</b><br>
      <span style="color:#bdc3c7">total ${d.total} · confirmed ${d.confirmed} · refuted ${d.refuted} · pending ${d.unconfirmed} · unverif ${d.unverifiable} · below-floor ${d.below_floor}</span><br>
      precision <span class="pct">${point}</span> <span class="ci">${interval}</span> <span class="ci" style="font-size:.8em">(n=${d.resolved_n} resolved)</span>
    </div>`;
  }).join('');
}

fetch('/wins').then(r=>r.json()).then(d=>{
  // System profile strip
  document.getElementById('profile-strip').innerHTML = renderProfile(d);

  // Precision band with Wilson CIs (null-bootstrap appended below)
  document.getElementById('precision-band').innerHTML = renderPrecisionBand(d);
  fetch('/api/null_bootstrap').then(r => r.ok ? r.json() : null).then(nb => {
    if (!nb || nb.error) return;
    const obs = nb.observed || {};
    const rs = (nb.null_distribution||{}).race_strict || {};
    const pad = document.getElementById('precision-band');
    const extra = `<br><b>Null-distribution bootstrap</b> (${nb.n_replicates} reps over ${nb.window_days}d, comparator times shuffled ±24h):
      observed race-strict <b>${obs.race_strict}</b>, null mean ${rs.mean?.toFixed(2)}, null p95 ${rs.p95}, p-value ${rs.p_value?.toFixed(3)}.
      <span style="color:#95a5a6">${(nb.interpretation||'').split('.')[0]}.</span>`;
    pad.innerHTML += extra;
  }).catch(() => {});

  // Per-sub-detector chips
  document.getElementById('per-detector').innerHTML = renderPerDetector(d.per_detector);

  // AUTHORITATIVE-FIRST: union of all comparator-confirmed events
  document.getElementById('authoritative-totals').innerHTML =
    `<b>${(d.external_only_wins||[]).length + (d.verified_external||[]).length}</b> authoritatively-confirmed event${((d.external_only_wins||[]).length + (d.verified_external||[]).length)===1?'':'s'} in 7d.`;
  document.getElementById('authoritative-list').innerHTML = renderAuthoritative(d);

  // PHOENIX-first leads — race-strict + race-marginal + first-vs-human + burnscar
  const s = d.stats || {};
  const parts = [];
  if (s.race_strict_count)   parts.push(`<span style="color:#2ecc71">${s.race_strict_count} race-strict</span>`);
  if (s.race_marginal_count) parts.push(`<span style="color:#e67e22">${s.race_marginal_count} race-marginal*</span>`);
  if (s.vs_human_count)      parts.push(`<span style="color:#e67e22">${s.vs_human_count} first vs VVF/news*</span>`);
  if (s.burnscar_count)      parts.push(`<span style="color:#f1c40f">${s.burnscar_count} burn-scar confirmed</span>`);
  document.getElementById('totals').innerHTML =
    `<b>${d.verified_wins.length}</b> PHOENIX-first win${d.verified_wins.length===1?'':'s'} in last 7 days`
    + (parts.length ? ' · ' + parts.join(' · ') : '')
    + ' · refuted events excluded · cross-PHOENIX-family corroboration moved to co-detected';
  const w = document.getElementById('wins-list');
  w.innerHTML = d.verified_wins.length === 0
    ? '<div class="empty">No PHOENIX-first leads in the last 7 days. PHOENIX is an experimental detector built around geostationary SEVIRI/FCI; most events either share detection time with a comparator (co-detected below) or fall in a comparator-blind window.</div>'
    : renderRaceValid(d.verified_wins);

  // Co-detected
  document.getElementById('verified-external-totals').innerHTML =
    `<b>${d.verified_external.length}</b> co-detected fire${d.verified_external.length===1?'':'s'} (≥T1)`;
  const ve = document.getElementById('verified-external-list');
  ve.innerHTML = d.verified_external.length === 0
    ? '<div class="empty">No co-detected fires in the last 7 days.</div>'
    : renderCoDetected(d.verified_external);

  // External-only
  document.getElementById('external-only-totals').innerHTML =
    `<b>${d.external_only_wins.length}</b> external catch${d.external_only_wins.length===1?'':'es'} PHOENIX missed`;
  const eo = document.getElementById('external-only-list');
  eo.innerHTML = d.external_only_wins.length === 0
    ? '<div class="empty">No external-only catches in the last 7 days.</div>'
    : renderExternalOnly(d.external_only_wins);

  // Refuted (OPEN by default)
  document.getElementById('refuted-totals').innerHTML =
    `<b>${d.refuted_phoenix.length}</b> sole-reporter detection${d.refuted_phoenix.length===1?'':'s'} refuted at T+72h. Each one bounds our precision — published openly.`;
  const rf = document.getElementById('refuted-list');
  rf.innerHTML = d.refuted_phoenix.length === 0
    ? '<div class="empty">None refuted yet.</div>'
    : d.refuted_phoenix.map(renderCompactRow).join('');

  // Unconfirmed (T0 awaiting T+72h)
  document.getElementById('unconfirmed-totals').innerHTML =
    `<b>${d.unconfirmed_phoenix.length}</b> sole-reporter PHOENIX detection${d.unconfirmed_phoenix.length===1?'':'s'} awaiting T+72h reconcile.`;
  const u = document.getElementById('unconfirmed-list');
  u.innerHTML = d.unconfirmed_phoenix.length === 0
    ? '<div class="empty">None pending.</div>'
    : d.unconfirmed_phoenix.map(renderCompactRow).join('');

  // Unverifiable (cloud-blocked / no S2)
  const unv = d.unverifiable_phoenix || [];
  document.getElementById('unverifiable-totals').innerHTML =
    `<b>${unv.length}</b> event${unv.length===1?'':'s'} unverifiable — no clear Sentinel-2 pass available in window.`;
  const uv = document.getElementById('unverifiable-list');
  uv.innerHTML = unv.length === 0
    ? '<div class="empty">None unverifiable.</div>'
    : unv.map(renderCompactRow).join('');

  // Below-floor (FRP below detection limit of comparators)
  const bf = d.below_floor_phoenix || [];
  document.getElementById('below-floor-totals').innerHTML =
    `<b>${bf.length}</b> event${bf.length===1?'':'s'} below the physical detection floor of every capable comparator.`;
  const bl = document.getElementById('below-floor-list');
  bl.innerHTML = bf.length === 0
    ? '<div class="empty">None below floor.</div>'
    : bf.map(renderCompactRow).join('');
});
</script>
</body></html>"""


@app.route('/wins.html')
def wins_html():
    from flask import Response
    return Response(_WINS_HTML, mimetype='text/html')


@app.route('/api/event_grades')
def api_event_grades():
    """Full graded event list with all tier + race + reconcile fields.

    Query params:
      days   default 30 — window in days
      tier   optional, one of T0/T1/T2/T3 — filter by verification tier
      led    optional, 'phoenix' or 'external' — filter by who led
    """
    import sqlite3
    from flask import request
    days = request.args.get('days', default=30, type=int)
    tier = request.args.get('tier')
    led = request.args.get('led')
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    con.row_factory = sqlite3.Row
    sql = ("SELECT * FROM event_grades "
           "WHERE first_ts > datetime('now', ?) ")
    params = [f"-{int(days)} days"]
    if tier in ("T0", "T1", "T2", "T3"):
        sql += "AND verification_tier = ? "
        params.append(tier)
    if led == "phoenix":
        sql += "AND is_phoenix_led = 1 "
    elif led == "external":
        sql += "AND is_phoenix_led = 0 "
    sql += "ORDER BY first_ts DESC"
    rows = list(con.execute(sql, params))
    con.close()
    out = []
    for r in rows:
        if r['representative_source'] == 'firms_test':
            continue
        d = dict(r)
        d['corroborator_sources'] = (d.get('corroborator_sources') or '').split(',') if d.get('corroborator_sources') else []
        d['corroborator_families'] = (d.get('corroborator_families') or '').split(',') if d.get('corroborator_families') else []
        out.append(d)
    return jsonify({
        "events": out,
        "count": len(out),
        "window_days": days,
        "filters": {"tier": tier, "led": led},
    })


@app.route('/api/null_bootstrap')
def api_null_bootstrap():
    """Permutation null-distribution result for race-strict wins.
    Generated by scripts/null_bootstrap.py (run nightly)."""
    from flask import jsonify
    import json as _json
    p = Path("/media/mark/AI_DGX/eumetsat_data/null_bootstrap.json")
    if not p.exists():
        return jsonify({"error": "Not computed yet. Run scripts/null_bootstrap.py."}), 404
    try:
        return jsonify(_json.loads(p.read_text()))
    except Exception as _e:
        return jsonify({"error": str(_e)}), 500


@app.route('/data/snapshots/')
def data_snapshots_index():
    """List available reproducibility snapshots (sorted newest-first)."""
    from flask import Response
    base = Path("/home/mark/.openclaw/workspace/eumetsat_wildfire_detection/data/snapshots")
    if not base.exists():
        return Response("(no snapshots yet)", mimetype="text/plain")
    dates = sorted([p.name for p in base.iterdir() if p.is_dir()], reverse=True)
    links = "\n".join(f'  <li><a href="/data/snapshots/{d}/">{d}/</a></li>' for d in dates)
    html = (
        '<!doctype html><html><head><title>PHOENIX reproducibility snapshots</title>'
        '<style>body{font-family:Segoe UI,sans-serif;background:#1a2530;color:#ecf0f1;padding:24px;max-width:900px}'
        'a{color:#3498db}</style></head><body>'
        '<h1>PHOENIX reproducibility snapshots</h1>'
        '<p>Each snapshot is a daily dump of the raw inputs + our official grades for the last 60 days.</p>'
        '<p>Anyone can re-grade with <code>scripts/regrade.py</code> from the '
        '<a href="https://github.com/markl02us/persistent-thermal-sources-sicily">GitHub repo</a>.</p>'
        f'<ul>{links}</ul></body></html>'
    )
    return Response(html, mimetype="text/html")


@app.route('/data/snapshots/<date>/')
def data_snapshots_date(date):
    """List files in a specific snapshot date."""
    from flask import Response, abort
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        abort(400)
    base = Path("/home/mark/.openclaw/workspace/eumetsat_wildfire_detection/data/snapshots") / date
    if not base.exists():
        abort(404)
    files = sorted(p.name for p in base.iterdir() if p.is_file())
    links = "\n".join(f'  <li><a href="/data/snapshots/{date}/{f}">{f}</a></li>' for f in files)
    html = (
        '<!doctype html><html><head><title>PHOENIX snapshot ' + date + '</title>'
        '<style>body{font-family:Segoe UI,sans-serif;background:#1a2530;color:#ecf0f1;padding:24px;max-width:900px}'
        'a{color:#3498db}</style></head><body>'
        f'<h1>PHOENIX snapshot {date}</h1>'
        '<p><a href="/data/snapshots/">← all snapshots</a></p>'
        f'<ul>{links}</ul></body></html>'
    )
    return Response(html, mimetype="text/html")


@app.route('/data/snapshots/<date>/<filename>')
def data_snapshots_file(date, filename):
    """Serve a snapshot file (csv / README / SHA256SUMS)."""
    from flask import send_from_directory, abort
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        abort(400)
    if not re.match(r'^[A-Za-z0-9_.\-]+$', filename):
        abort(400)
    base = Path("/home/mark/.openclaw/workspace/eumetsat_wildfire_detection/data/snapshots") / date
    if not (base / filename).exists():
        abort(404)
    mt = "text/csv" if filename.endswith(".csv") else (
         "text/markdown" if filename.endswith(".md") else "text/plain")
    return send_from_directory(str(base), filename, mimetype=mt)


@app.route('/api/event_grades.csv')
def api_event_grades_csv():
    """CSV export of graded events for researchers."""
    import csv, io, sqlite3
    from flask import request, Response
    days = request.args.get('days', default=30, type=int)
    gt_db = Path(config['storage']['base_path']) / config['storage']['ground_truth_db']
    con = sqlite3.connect(str(gt_db))
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT * FROM event_grades WHERE first_ts > datetime('now', ?) "
        "ORDER BY first_ts DESC",
        (f"-{int(days)} days",)
    ))
    con.close()
    if not rows:
        return Response("event_key,first_ts,verification_tier,is_phoenix_led\n",
                        mimetype="text/csv")
    cols = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        if r['representative_source'] == 'firms_test':
            continue
        w.writerow([r[c] if r[c] is not None else "" for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=phoenix_event_grades.csv"})


_BACKTEST_JSON = Path("/media/mark/AI_DGX/eumetsat_data/backtest_seviri.json")

@app.route('/backtest_seviri')
def backtest_seviri():
    """Return latest SEVIRI backtest results as JSON. 404 if not yet run."""
    if not _BACKTEST_JSON.exists():
        return jsonify({'error': 'Backtest not yet run. Execute scripts/seviri_backtest.py first.'}), 404
    try:
        return jsonify(json.loads(_BACKTEST_JSON.read_text()))
    except Exception as _be:
        return jsonify({'error': str(_be)}), 500


# Known persistent thermal anomalies — suppressed at save_detection so they
# never reach the live map, scoring, or email-alert path. These are NOT
# wildfires; the legacy detector tends to pick them up because they show fire-
# like MIR/TIR signatures persistently. Council can review whether to keep this
# in code or move to a config-driven list.
_KNOWN_ANOMALIES = [
    # Mount Etna summit craters — persistent thermal anomaly from active volcano
    {"name": "etna_summit",      "lat": 37.751, "lon": 14.993, "radius_km": 8.0},
    # Etna flank (broader catchment for hot ash / lower flank activity)
    {"name": "etna_flank",       "lat": 37.700, "lon": 14.950, "radius_km": 10.0},
    # Mount Stromboli — Aeolian Islands volcano, often active
    {"name": "stromboli",        "lat": 38.789, "lon": 15.213, "radius_km": 3.0},
    # Vulcano (Aeolian Islands) — fumarole activity
    {"name": "vulcano",          "lat": 38.404, "lon": 14.962, "radius_km": 3.0},
    # Catania industrial corridor (port + refineries) — 31 PHOENIX detections/wk
    # in audit 2026-05-22. Persistent industrial heat, not wildfires.
    {"name": "catania_indust",   "lat": 37.460, "lon": 14.680, "radius_km": 9.0},
    # Gela petrochemical (ENI Raffineria di Gela) — 28 detections/wk
    {"name": "gela_refinery",    "lat": 37.075, "lon": 14.480, "radius_km": 10.0},
    # Augusta-Priolo-Melilli petrochemical complex (one of Europe's largest)
    {"name": "priolo_petrochem", "lat": 37.180, "lon": 15.220, "radius_km": 9.0},
    # Trapani salt pans / industrial — persistent FP zone from earlier backtest
    {"name": "trapani_salines",  "lat": 37.890, "lon": 12.500, "radius_km": 8.0},
]


def _is_known_anomaly(lat: float, lon: float) -> Optional[str]:
    """Return anomaly name if pixel is within radius_km of a known persistent
    thermal source, else None. Uses equirectangular approximation — good
    enough for the small radii involved (≤10 km).
    """
    for anom in _KNOWN_ANOMALIES:
        # 1° lat ≈ 111 km; 1° lon at this lat ≈ 111 × cos(lat_rad) km
        import math
        dlat_km = abs(lat - anom["lat"]) * 111.0
        cos_lat = max(0.01, abs(math.cos(math.radians(anom["lat"]))))
        dlon_km = abs(lon - anom["lon"]) * 111.0 * cos_lat
        km = math.hypot(dlat_km, dlon_km)
        if km <= anom["radius_km"]:
            return anom["name"]
    return None


def save_detection(detection: Dict, frame_arrays: Optional[dict] = None):
    """Persist detection JSON to disk AND insert into internal_fires so scoring
    + Hermes narration + email-alert path can pick it up.

    Without the insert_internal call below, detections only land on disk JSON and
    the scoreboard never sees them — meaning a real fire would never email Mark.
    Discovered 2026-05-22 while wiring subpixel_v1 alerts.

    Suppresses detections inside `_KNOWN_ANOMALIES` (Etna, Stromboli, Vulcano)
    so the live map and email alerts don't flood with persistent volcanic
    signatures — they aren't wildfires.

    If frame_arrays is provided (dict with bt_mir/bt_tir/lat/lon ndarrays from
    the frame that triggered this detection), a thermal crop PNG is rendered
    and attached as detection['image_url']. Operators can then click the
    detection's map marker and see the actual data that triggered the system.
    """
    # Suppress known persistent thermal anomalies BEFORE any persistence.
    try:
        _lat = float(detection.get('lat', 0.0) or 0.0)
        _lon = float(detection.get('lon', detection.get('lng', 0.0)) or 0.0)
        _anom = _is_known_anomaly(_lat, _lon)
        if _anom is not None:
            logger.info("save_detection: suppressed (known anomaly: %s) at (%.3f, %.3f) source=%s",
                        _anom, _lat, _lon, detection.get('source', 'unk'))
            return
    except (TypeError, ValueError):
        pass

    # Land + coast-buffer filter (Mark 2026-05-23 — map was lit up over water
    # and along entire coastline). Wildfires don't happen at sea; coastal
    # mixed land/water pixels are the dominant FP source at SEVIRI 3 km.
    try:
        from src.land_mask import is_valid_land_pixel, distance_to_coast_km, is_inside_sicily
        if not is_valid_land_pixel(_lat, _lon):
            if not is_inside_sicily(_lat, _lon):
                _why = "off-shore"
            else:
                _why = f"coastal (d={distance_to_coast_km(_lat,_lon):.1f}km < 3km)"
            logger.info("save_detection: suppressed (%s) at (%.3f, %.3f) source=%s",
                        _why, _lat, _lon, detection.get('source', 'unk'))
            return
    except ImportError:
        pass
    except Exception as _e_lm:
        logger.warning("save_detection: land-mask check failed: %s", _e_lm)

    # Confidence floor — drop low-confidence legacy-detector noise that the ML
    # 2nd-stage doesn't fully clean up. Subpixel_v1 detections are emitted
    # with confidence ≥0.55 (lightning-primed floor) or ≥0.7 (strong-signal)
    # so this floor never suppresses them. Adopted 2026-05-22 after observing
    # daytime Sicily legacy-detector flood (19/scan, max conf 0.65, AF-L2
    # confirmed 0 real fires). Council should review whether to remove
    # legacy detector entirely once subpixel_v1 is validated.
    _MIN_CONF = 0.5
    try:
        _conf = float(detection.get('enhanced_confidence',
                                    detection.get('confidence', 0.0)) or 0.0)
        if _conf < _MIN_CONF and detection.get('source') not in ('s2_swir',):
            logger.info("save_detection: suppressed (conf=%.2f < %.2f) source=%s lat=%.3f lon=%.3f",
                        _conf, _MIN_CONF, detection.get('source', 'unk'),
                        _lat, _lon)
            return
    except (TypeError, ValueError):
        pass

    # Thermal crop rendering — only if frame arrays are provided AND the
    # detection survived all filters above. The image_url field is added to
    # the detection dict so it's persisted to JSON + carried in raw_json.
    if frame_arrays is not None:
        try:
            from src.detectors.detection_imagery import save_thermal_crop
            crop_filename = save_thermal_crop(
                detection,
                frame_arrays['bt_mir'], frame_arrays['bt_tir'],
                frame_arrays['lat'], frame_arrays['lon'],
            )
            if crop_filename:
                detection['image_url'] = f'/api/detection-crop/{crop_filename}'
        except Exception as _e_img:
            logger.warning("save_detection: thermal crop render failed: %s", _e_img)

    # GEO-only trigger + async LEO verification (Council Round 1 Seat 2):
    # every PHOENIX detection starts as 'provisional'. confirm_against_external
    # graduates it to 'confirmed' when an LEO comparator (FIRMS/AF-L2) later
    # sees the same fire within 5 km / 2 h. Un-confirmed provisional rows
    # auto-expire after 6 h via the cleanup daemon below.
    detection.setdefault('status', 'provisional')

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    filename  = storage_path / f"wildfire_{detection.get('source','unk')}_{timestamp}.json"
    with open(filename, 'w') as f:
        json.dump(detection, f, indent=2, default=str)

    # SQLite scoring path — required for scoreboard wins/losses + email alerts.
    try:
        lat = float(detection.get('lat', 0.0) or 0.0)
        lng = float(detection.get('lon', detection.get('lng', 0.0)) or 0.0)
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return  # skip malformed detection
        # Resolve AOI from lat/lng using config.aois
        aoi_id = None
        for _aname, _aoi in config.get('aois', {}).items():
            if not _aoi.get('enabled', True):
                continue
            s, w, n, e = _aoi['bbox']
            if s <= lat <= n and w <= lng <= e:
                aoi_id = _aname
                break
        if aoi_id is None:
            return  # detection outside any configured AOI
        ts = detection.get('timestamp') or detection.get('scan_start') or datetime.now(timezone.utc).isoformat()
        insert_internal(
            cfg=config,
            aoi_id=aoi_id,
            lat=lat,
            lng=lng,
            ts=str(ts),
            source=str(detection.get('source', 'adr')),
            confidence=float(detection.get('enhanced_confidence', detection.get('confidence', 0.0)) or 0.0),
            frp_mw=float(detection.get('frp_mw', 0.0) or 0.0),
            temperature_c=float(detection.get('fire_temperature_c', 0.0) or 0.0),
            raw_json=json.dumps(detection, default=str),
        )
    except Exception as _e_save:
        logger.error("save_detection: SQLite insert failed (disk JSON still written): %s", _e_save)




def s2_swir_monitoring_loop():
    """Poll latest S2 scene over AOI every 60 min; run NHI + subpixel_v3."""
    import yaml as _yaml
    poll_min = config.get("sentinel2", {}).get("poll_interval_minutes", 60)
    logger.info("S2 SWIR monitoring loop started (%d-min interval)", poll_min)
    while True:
        try:
            bbox_cfg = config["area_of_interest"]["bbox"]
            aoi_bbox = (
                bbox_cfg["west"], bbox_cfg["south"],
                bbox_cfg["east"], bbox_cfg["north"],
            )
            logger.info("S2 SWIR: fetching latest scene...")
            s2_dets = s2_swir_detector.detect(aoi_bbox=aoi_bbox, time_window=72)
            if s2_dets:
                logger.info("S2 SWIR: %d raw candidates", len(s2_dets))
                for det in s2_dets:
                    det.setdefault("lon", det.get("lng", 0.0))
                    existing = [
                        d for d in detections_list[-500:]
                        if d.get("source") == "s2_swir"
                        and abs(d.get("lat", 0) - det["lat"]) < 0.01
                        and abs(d.get("lon", 0) - det["lon"]) < 0.01
                        and d.get("scene_id") == det.get("scene_id")
                    ]
                    if not existing:
                        detections_list.append(det)
                        save_detection(det)
                        logger.info("S2 SWIR NEW: lat=%.4f lon=%.4f nhi=%.3f b12=%.3f",
                                    det["lat"], det["lon"], det.get("nhi", 0), det.get("b12", 0))
            else:
                logger.info("S2 SWIR: no detections this cycle")
        except Exception as _e:
            logger.error("S2 SWIR loop error: %s", _e)
        time.sleep(poll_min * 60)

def effis_monitoring_loop():
    logger.info("Starting EFFIS monitoring (5-minute interval)")
    while True:
        try:
            effis_fires = effis_client.get_current_fires()
            if effis_fires:
                logger.info(f"EFFIS: {len(effis_fires)} fires detected")
                for fire in effis_fires:
                    is_duplicate = any(
                        abs(d['lat'] - fire['lat']) < 0.001 and
                        abs(d['lon'] - fire['lon']) < 0.001 and
                        d['source'] == 'effis'
                        for d in detections_list[-100:]
                    )
                    if not is_duplicate:
                        # EFFIS detections bypass ML — annotate and publish
                        annotated = _ml_apply_filter(fire, bypass_ml=True)
                        if annotated:
                            detections_list.append(annotated)
                            save_detection(annotated)
                            logger.info("  New EFFIS fire: (%.4f, %.4f)", fire['lat'], fire['lon'])
            else:
                logger.info("EFFIS: No new fires")
            time.sleep(5 * 60)
        except Exception as e:
            logger.error(f"Error in EFFIS loop: {e}")
            time.sleep(60)


def adr_monitoring_loop():
    logger.info("Starting ADR WildFire monitoring (15-minute interval)")
    check_interval = config['eumetsat']['poll_interval'] * 60

    while True:
        try:
            logger.info(f"--- ADR Scan {datetime.now().strftime('%H:%M:%S')} ---")

            # Evidence stream: SEVIRI MSG15-RSS (5-min cadence, ~3 km IR).
            # Falls back to synthetic only if real ingest fails — logged loudly.
            scan_provenance = {"source": "synthetic_fallback", "real": False}
            frame = seviri_rss_client.latest_frame() if seviri_rss_client else None
            if frame is not None:
                bt_mir, bt_tir, lat, lon = frame.bt_mir, frame.bt_tir, frame.lat, frame.lon
                scan_provenance = {
                    "source": "seviri_rss",
                    "product_id": frame.product_id,
                    "scan_start": frame.scan_start.isoformat(),
                    "scan_end": frame.scan_end.isoformat(),
                    "shape": list(bt_mir.shape),
                    "real": True,
                }
                logger.info(
                    "REAL SEVIRI frame: %s scan=%s shape=%s bt_mir[min/mean/max]=%.1f/%.1f/%.1f K",
                    frame.product_id, frame.scan_start.isoformat(), bt_mir.shape,
                    float(np.nanmin(bt_mir)), float(np.nanmean(bt_mir)), float(np.nanmax(bt_mir)),
                )
                try:
                    seviri_rss_client.prune_cache(keep_hours=24)
                except Exception:
                    pass
                # Continuous baseline-corpus growth — every live frame appended
                # to the rolling 30-day corpus so the per-pixel/hour baseline
                # tracks seasonal drift. Detector z-scores stay calibrated to
                # the most recent month, catching small fires earlier.
                try:
                    if seviri_rss_client.append_to_baseline_corpus(frame):
                        logger.debug("baseline corpus: appended %s", frame.product_id)
                    # Prune once per ~20 scans to amortize the directory walk
                    if int(datetime.now().timestamp()) % 1200 < 30:
                        seviri_rss_client.prune_baseline_corpus(retention_days=30)
                except Exception as _e_corpus:
                    logger.warning("baseline corpus append error: %s", _e_corpus)
            else:
                logger.warning("SEVIRI ingest unavailable — FALLING BACK TO SYNTHETIC SCENE")
                bt_mir, bt_tir, lat, lon = fire_detector.generate_synthetic_scene()

            # subpixel_v1 detector — runs on this SEVIRI frame in parallel with
            # the legacy 3-pixel detector. Single-pixel allowed, lightning-primed.
            if SUBPIXEL_V1_AVAILABLE and scan_provenance.get("real"):
                try:
                    _lst_arr_sev = None
                    if mtg_lst_client is not None:
                        try:
                            from pyresample.geometry import SwathDefinition
                            _sev_swath = SwathDefinition(lons=lon, lats=lat)
                            _lst_frame_sev = mtg_lst_client.latest_frame(_sev_swath)
                            if _lst_frame_sev is not None and _lst_frame_sev.lst_k.shape == bt_mir.shape:
                                _lst_arr_sev = _lst_frame_sev.lst_k
                                logger.info("seviri_rss: LST frame loaded (valid=%.1f%%)", float(np.isfinite(_lst_arr_sev).mean())*100)
                        except Exception as _e_lst_sev:
                            logger.warning("mtg_lst fetch for SEVIRI failed: %s", _e_lst_sev)
                    _sp1_dets = detect_subpixel_v1(
                        bt_mir=bt_mir, bt_tir=bt_tir, lat=lat, lon=lon,
                        source_stream="seviri_rss", timestamp=datetime.now(timezone.utc),
                        lst_now=_lst_arr_sev,
                    )
                    _sev_arrays = {"bt_mir": bt_mir, "bt_tir": bt_tir, "lat": lat, "lon": lon}
                    for _d in _sp1_dets:
                        _det_dict = detection_to_dict(_d)
                        save_detection(_det_dict, frame_arrays=_sev_arrays)
                        detections_list.append(_det_dict)
                except Exception as _e_sp1:
                    logger.error("subpixel_v1 (SEVIRI) error: %s", _e_sp1)

            # Wind-advection differencing — subtract upwind-shifted previous
            # frame to expose plume residuals invisible to static thresholds.
            if WIND_DIFF_AVAILABLE and scan_provenance.get("real"):
                try:
                    now_ts = datetime.now(timezone.utc)
                    prev = _prev_frames.get("seviri_rss")
                    if prev is not None:
                        prev_mir, prev_lat, prev_lon, prev_ts = prev
                        if prev_mir.shape == bt_mir.shape:
                            _wd_dets = detect_wind_advection(
                                bt_mir_t=bt_mir, bt_mir_tm1=prev_mir,
                                lat=lat, lon=lon,
                                timestamp_t=now_ts, timestamp_tm1=prev_ts,
                                source_stream="seviri_rss",
                                lst_now=_lst_arr_sev,
                            )
                            for _d in _wd_dets:
                                _det_dict = wind_diff_to_dict(_d)
                                save_detection(_det_dict, frame_arrays={"bt_mir": bt_mir, "bt_tir": bt_tir, "lat": lat, "lon": lon})
                                detections_list.append(_det_dict)
                    # Update the per-stream "previous frame" cache after running
                    _prev_frames["seviri_rss"] = (bt_mir.copy(), lat, lon, now_ts)
                except Exception as _e_wd:
                    logger.error("wind_advection (SEVIRI) error: %s", _e_wd)

# SubPixelV3: run on this cycle's thermal arrays (independent of enhanced_detector)
            try:
                _sp3_cube = (np.stack([bt_mir, bt_tir, bt_mir*0.8, bt_tir*0.9], axis=2)
                             if bt_mir.shape == bt_tir.shape else None)
                _sp3_dets = subpixel_v3_detector.process_frame(
                    bt_mir=bt_mir, bt_tir=bt_tir, lat=lat, lon=lon,
                    spectral_cube=_sp3_cube,
                    timestamp=datetime.now(),
                )
                for _det in _sp3_dets:
                    _det.setdefault("lon", _det.get("lng", 0.0))
                    detections_list.append(_det)
                    save_detection(_det)
                if _sp3_dets:
                    logger.info("SubPixelV3: %d new detections", len(_sp3_dets))
            except Exception as _sp3e:
                logger.error("SubPixelV3 error: %s", _sp3e)



            additional_data = {
                'meteorology': get_current_weather_data(),
                'vegetation':  get_current_vegetation_data(),
                'topography':  get_topography_data(lat, lon)
            }

            enhanced_detections = enhanced_detector.detect_fires_enhanced(
                bt_mir, bt_tir, lat, lon, additional_data
            )

            if len(enhanced_detections) > 0:
                try:
                    if bt_mir.shape == bt_tir.shape:
                        spectral_cube = np.stack([bt_mir, bt_tir, bt_mir * 0.8, bt_tir * 0.9], axis=2)
                        enhancement_results = subpixel_pipeline.process_frame(
                            thermal_data=bt_mir,
                            spectral_cube=spectral_cube,
                            timestamp=datetime.now()
                        )
                        refined_detections = refine_detections_with_enhancement(
                            enhanced_detections, enhancement_results, lat, lon
                        )
                        if refined_detections:
                            logger.info(f"ADR WILDFIRE: {len(refined_detections)} candidates pre-ML")
                            _sev_arrays = {"bt_mir": bt_mir, "bt_tir": bt_tir, "lat": lat, "lon": lon}
                            for det in refined_detections:
                                det['source'] = 'enhanced'
                                # Build pixel window centred on detection
                                pw = _extract_pixel_window(det, bt_mir, bt_tir, lat, lon)
                                annotated = _ml_apply_filter(det, pixel_window=pw)
                                if annotated:
                                    save_detection(annotated, frame_arrays=_sev_arrays)
                                    detections_list.append(annotated)
                        else:
                            logger.info("ADR WildFire: No validated fires after enhancement")
                    else:
                        logger.warning("Shape mismatch: skipping enhancement")
                        for det in enhanced_detections:
                            det['source'] = 'dgx'
                            det['enhanced_confidence'] = 0.7
                            det['subpixel_validated'] = True
                            annotated = _ml_apply_filter(det)
                            if annotated:
                                detections_list.append(annotated)
                                save_detection(annotated)
                except Exception as e:
                    logger.error(f"Error in sub-pixel enhancement: {e}")
                    for det in enhanced_detections:
                        det['source'] = 'dgx'
                        det['enhanced_confidence'] = 0.6
                        det['subpixel_validated'] = False
                        annotated = _ml_apply_filter(det)
                        if annotated:
                            detections_list.append(annotated)
                            save_detection(annotated)
            else:
                logger.info("ADR WildFire: No fires detected")

            # ─── Second evidence stream: FCI L1c (MTG-I1, 10-min, ~2km IR) ───
            # Runs sequentially after SEVIRI on each scan tick. Different chunks of
            # disk, different resolution — independent confirmation/cross-check.
            # Detections tagged source='fci_l1c' so the scoreboard tracks per-stream.
            if fci_l1c_client is not None:
                try:
                    fci_frame = fci_l1c_client.latest_frame()
                    if fci_frame is not None:
                        # PHOENIX 2026-05-24 — append snapshot to FCI baseline corpus
                        try:
                            from src.data_sources.fci_l1c import append_to_baseline_corpus_fci
                            append_to_baseline_corpus_fci(fci_frame)
                        except Exception as _bs_exc:
                            logger.debug("fci baseline snapshot save: %s", _bs_exc)
                        bt_mir_fci = fci_frame.bt_mir
                        bt_tir_fci = fci_frame.bt_tir
                        lat_fci = fci_frame.lat
                        lon_fci = fci_frame.lon
                        logger.info(
                            "REAL FCI frame: %s scan=%s shape=%s chunks=%s bt_mir[min/mean/max]=%.1f/%.1f/%.1f K",
                            fci_frame.product_id, fci_frame.scan_start.isoformat(),
                            bt_mir_fci.shape, fci_frame.chunk_ids,
                            float(np.nanmin(bt_mir_fci)), float(np.nanmean(bt_mir_fci)), float(np.nanmax(bt_mir_fci)),
                        )
                        fci_detections = enhanced_detector.detect_fires_enhanced(
                            bt_mir_fci, bt_tir_fci, lat_fci, lon_fci, additional_data
                        )
                        _fci_arrays = {"bt_mir": bt_mir_fci, "bt_tir": bt_tir_fci,
                                       "lat": lat_fci, "lon": lon_fci}
                        for det in fci_detections:
                            det['source'] = 'fci_l1c'
                            det['platform'] = 'MTG-I1'
                            det['scan_start'] = fci_frame.scan_start.isoformat()
                            annotated = _ml_apply_filter(det)
                            if annotated:
                                save_detection(annotated, frame_arrays=_fci_arrays)
                                detections_list.append(annotated)
                        if fci_detections:
                            logger.info("FCI: %d fire detections (added to scan tick)", len(fci_detections))
                        else:
                            logger.info("FCI: No fires detected")
                        # subpixel_v1 on FCI — same single-pixel-aware logic, better res
                        if SUBPIXEL_V1_AVAILABLE:
                            try:
                                # PHOENIX 2026-05-23 — pull the latest MTG LST and pass
                                # it into the subpixel detector as the context for the
                                # (MIR - LST) delta gate. Best-effort: if LST fetch fails
                                # or returns None we fall back to the absolute-MIR floor.
                                _lst_arr_fci = None
                                if mtg_lst_client is not None:
                                    try:
                                        # Reuse the FCI Sicily area_def so the LST array
                                        # lands on the exact same grid as bt_mir_fci.
                                        _fci_area = fci_l1c_client._sicily_area_def(
                                            fci_l1c_client.bbox[1], fci_l1c_client.bbox[0],
                                            fci_l1c_client.bbox[3], fci_l1c_client.bbox[2],
                                            bt_mir_fci.shape[1], bt_mir_fci.shape[0],
                                        )
                                        _lst_frame = mtg_lst_client.latest_frame(_fci_area)
                                        if _lst_frame is not None and _lst_frame.lst_k.shape == bt_mir_fci.shape:
                                            _lst_arr_fci = _lst_frame.lst_k
                                            logger.info(
                                                "fci_l1c: LST frame loaded (scan=%s, valid=%.1f%%)",
                                                _lst_frame.scan_time.isoformat() if _lst_frame.scan_time else "?",
                                                float(__import__("numpy").isfinite(_lst_arr_fci).mean()) * 100,
                                            )
                                    except Exception as _e_lst_fci:
                                        logger.warning("mtg_lst fetch failed for FCI frame: %s", _e_lst_fci)
                                _sp1_fci = detect_subpixel_v1(
                                    bt_mir=bt_mir_fci, bt_tir=bt_tir_fci,
                                    lat=lat_fci, lon=lon_fci,
                                    source_stream="fci_l1c",
                                    timestamp=datetime.now(timezone.utc),
                                    lst_now=_lst_arr_fci,
                                )
                                for _d in _sp1_fci:
                                    _det_dict = detection_to_dict(_d)
                                    _det_dict['platform'] = 'MTG-I1'
                                    save_detection(_det_dict, frame_arrays=_fci_arrays)
                                    detections_list.append(_det_dict)
                            except Exception as _e_sp1_fci:
                                logger.error("subpixel_v1 (FCI) error: %s", _e_sp1_fci)
                        # Wind-advection differencing on FCI (10-min cadence)
                        if WIND_DIFF_AVAILABLE:
                            try:
                                now_ts_fci = datetime.now(timezone.utc)
                                prev_fci = _prev_frames.get("fci_l1c")
                                if prev_fci is not None:
                                    prev_mir_f, prev_lat_f, prev_lon_f, prev_ts_f = prev_fci
                                    if prev_mir_f.shape == bt_mir_fci.shape:
                                        _wd_fci = detect_wind_advection(
                                            bt_mir_t=bt_mir_fci, bt_mir_tm1=prev_mir_f,
                                            lat=lat_fci, lon=lon_fci,
                                            timestamp_t=now_ts_fci, timestamp_tm1=prev_ts_f,
                                            source_stream="fci_l1c",
                                            lst_now=_lst_arr_fci,
                                        )
                                        for _d in _wd_fci:
                                            _det_dict = wind_diff_to_dict(_d)
                                            _det_dict['platform'] = 'MTG-I1'
                                            save_detection(_det_dict, frame_arrays=_fci_arrays)
                                            detections_list.append(_det_dict)
                                _prev_frames["fci_l1c"] = (bt_mir_fci.copy(), lat_fci, lon_fci, now_ts_fci)
                            except Exception as _e_wd_fci:
                                logger.error("wind_advection (FCI) error: %s", _e_wd_fci)
                        try:
                            fci_l1c_client.prune_cache(keep_hours=24)
                        except Exception:
                            pass
                    else:
                        logger.warning("FCI ingest unavailable this cycle")
                except Exception as _e_fci_scan:
                    logger.error("FCI scan error: %s", _e_fci_scan)

            time.sleep(check_interval)

        except Exception as e:
            logger.error(f"Error in ADR loop: {e}")
            time.sleep(60)


def _extract_pixel_window(detection: Dict, bt_mir, bt_tir, lat_grid, lon_grid,
                           half: int = 3):
    """
    Extract a (2*half+1, 2*half+1, 2) pixel window centred on detection lat/lon.
    Returns None if the grid is too small or the point is out of bounds.
    """
    try:
        ri, ci = _latlon_to_pixel(detection['lat'], detection['lon'], lat_grid, lon_grid)
        h, w = bt_mir.shape
        r0, r1 = max(ri - half, 0), min(ri + half + 1, h)
        c0, c1 = max(ci - half, 0), min(ci + half + 1, w)
        mir_patch = bt_mir[r0:r1, c0:c1]
        tir_patch = bt_tir[r0:r1, c0:c1]
        if mir_patch.size < 4:
            return None
        return np.stack([mir_patch, tir_patch], axis=2)
    except Exception:
        return None


def refine_detections_with_enhancement(base_detections, enhancement_results, lat, lon):
    if not base_detections:
        return []
    refined_detections = []
    confidence_map = enhancement_results.get('confidence_map', np.zeros_like(lat))
    ensemble_detections = enhancement_results.get('ensemble_binary', np.zeros_like(lat, dtype=bool))
    for detection in base_detections:
        lat_idx, lon_idx = _latlon_to_pixel(detection['lat'], detection['lon'], lat, lon)
        if (0 <= lat_idx < confidence_map.shape[0] and
                0 <= lon_idx < confidence_map.shape[1]):
            enhancement_conf = confidence_map[lat_idx, lon_idx]
            is_enhanced = ensemble_detections[lat_idx, lon_idx] if 'ensemble_binary' in enhancement_results else True
            updated = detection.copy()
            updated['enhanced_confidence'] = float(enhancement_conf)
            updated['subpixel_validated']  = bool(is_enhanced)
            if is_enhanced and enhancement_conf > 0.3:
                refined_detections.append(updated)
    return refined_detections


def _latlon_to_pixel(lat_val, lon_val, lat_array, lon_array):
    if lat_array.ndim == 1:
        lat_idx = int(np.argmin(np.abs(lat_array - lat_val)))
        lon_idx = int(np.argmin(np.abs(lon_array - lon_val)))
    else:
        lat_diff = np.abs(lat_array - lat_val)
        lon_diff = np.abs(lon_array - lon_val)
        lat_idx, lon_idx = np.unravel_index(np.argmin(lat_diff + lon_diff), lat_array.shape)
    return int(lat_idx), int(lon_idx)


def get_current_weather_data():
    return {'temperature': 28, 'humidity': 45, 'wind_speed': 8,
            'wind_direction': 180, 'pressure': 1013, 'precipitation': 0}

def get_current_vegetation_data():
    return {'ndvi': 0.4, 'fuel_moisture': 0.25,
            'vegetation_type': 'grassland', 'density': 0.6}

def get_topography_data(lat, lon):
    return {'elevation': 150, 'slope': 12, 'aspect': 180, 'roughness': 0.1}


def download_historical_data():
    logger.info("Downloading EFFIS historical data for validation")
    end_date   = datetime.now()
    start_date = end_date - timedelta(days=730)
    output_dir = Path(config['storage']['base_path']) / 'validation_data'
    try:
        historical_file = effis_client.get_historical_fires(start_date, end_date, str(output_dir))
        if historical_file:
            logger.info(f"Historical data saved to {historical_file}")
            training_file = output_dir / 'validation_dataset.json'
            effis_client.create_training_dataset(historical_file, str(training_file))
            logger.info(f"Validation dataset created: {training_file}")
        else:
            logger.warning("Could not download historical data")
    except Exception as e:
        logger.warning(f"Historical data download failed: {e}")




_BACKTEST_GLOBAL_JSON = Path("/media/mark/AI_DGX/eumetsat_data/backtest_seviri_global.json")

@app.route('/backtest_seviri_global')
def backtest_seviri_global():
    """Return global stratified SEVIRI backtest results as JSON."""
    if not _BACKTEST_GLOBAL_JSON.exists():
        return jsonify({'error': 'Global backtest not yet run. Execute scripts/seviri_backtest_global.py first.'}), 404
    try:
        return jsonify(json.loads(_BACKTEST_GLOBAL_JSON.read_text()))
    except Exception as exc:
        return jsonify({'error': str(exc)}), 500



@app.route("/api/detections_filtered")
def api_detections_filtered():
    """PHOENIX 2026-05-24 — same as /api/detections but suppresses comparator hits
    (fci_l1c, firms_*, mtg_af_l2) that fall inside known FP zones from
    sources.json. Use ?include_known_fp=1 to see everything (audit view)."""
    from flask import request, jsonify
    import urllib.request as _ur, urllib.parse as _up, json as _json
    qs = request.query_string.decode()
    upstream = "http://127.0.0.1:8081/api/detections" + ("?" + qs if qs else "")
    try:
        with _ur.urlopen(upstream, timeout=15) as resp:
            data = _json.loads(resp.read())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    include_fp = request.args.get("include_known_fp", "0") == "1"
    if include_fp:
        return jsonify(data)
    zones = _fp_mask_zones()
    if not zones:
        return jsonify(data)
    COMPARATORS = {"fci_l1c", "mtg_af_l2"}
    def _is_comp(s):
        return s in COMPARATORS or (s and s.startswith("firms_"))
    n_before = len(data.get("detections", []))
    filtered = []
    n_suppressed = 0
    for d in data.get("detections", []):
        if _is_comp(d.get("source")):
            lat = d.get("lat"); lon = d.get("lng") or d.get("lon")
            if lat is not None and lon is not None and _is_in_fp_zone(lat, lon, zones):
                n_suppressed += 1
                continue
        filtered.append(d)
    data["detections"] = filtered
    data["fp_suppression"] = {"suppressed_comparator_hits": n_suppressed,
                               "zone_count": len(zones)}
    return jsonify(data)




if __name__ == "__main__":
    logger.info("="*80)
    logger.info("ADR WildFire Solution — ML second-stage filter active: %s",
                "YES" if _ml_classifier else "NO (fallback passthrough)")
    logger.info("PhoenixClassifier: phoenix_v4_algorithm (5-stage physics pipeline)")
    logger.info("="*80)
    logger.info(f"Monitoring: {config['area_of_interest']['name']}")
    logger.info(f"Center: {config['area_of_interest']['center']}")
    bbox = config['area_of_interest']['bbox']
    logger.info(f"Coverage: lat {bbox['south']:.1f}-{bbox['north']:.1f}, lon {bbox['west']:.1f}-{bbox['east']:.1f}")
    logger.info("")
    logger.info("ML Filter Rules:")
    logger.info("  conf >= ml_accept_threshold (per AOI, default 0.5)  -> PUBLISHED")
    logger.info("  0.3 <= conf < threshold                              -> PROVISIONAL (stored, no alert)")
    logger.info("  conf < 0.3                                           -> SUPPRESSED")
    logger.info("  source == 'effis'                                    -> BYPASSED (ground-truth)")
    logger.info("="*80)

    # Init ground-truth DB and start ingest/scoring daemons
    if GT_AVAILABLE:
        try:
            from pathlib import Path as _P
            init_db(config)
            _cf = _P(config['storage']['base_path']) / config['storage'].get('camera_frames', 'eumetsat_data/camera_frames')
            _cf.mkdir(parents=True, exist_ok=True)
            register_camera_routes(app, config, insert_internal)
            Thread(target=firms_polling_loop, args=(config,), daemon=True).start()
            Thread(target=effis_polling_loop, args=(config,), daemon=True).start()
            Thread(target=nightly_scoring_loop, args=(config,), daemon=True).start()
            # MTG-I1 Active Fire L2 — new lead-time bar. EUMETSAT publishes their
            # own L2 fires ~9 min after each FCI scan ends; PHOENIX wins by
            # getting detections out before that comparator product publishes.
            try:
                from src.data_sources.active_fire_l2 import polling_loop as mtg_af_l2_polling_loop
                Thread(target=mtg_af_l2_polling_loop, args=(config,), daemon=True).start()
                logger.info('mtg_af_l2 polling daemon started (10-min interval)')
            except Exception as _e_af:
                logger.error('mtg_af_l2 daemon init failed: %s', _e_af)

            # --- Phase-2 corroboration sources (2026-05-24) ---
            try:
                from src.data_sources.ansa_rss import polling_loop as ansa_rss_polling_loop
                Thread(target=ansa_rss_polling_loop, args=(config,), daemon=True).start()
                logger.info('ansa_rss polling thread started')
            except Exception as _e:
                logger.warning('ansa_rss thread failed to start: %s', _e)
            try:
                from src.data_sources.arpa_air import polling_loop as arpa_air_polling_loop
                Thread(target=arpa_air_polling_loop, args=(config,), daemon=True).start()
                logger.info('arpa_air polling thread started')
            except Exception as _e:
                logger.warning('arpa_air thread failed to start: %s', _e)
            try:
                from src.data_sources.slstr_frp import polling_loop as slstr_frp_polling_loop
                Thread(target=slstr_frp_polling_loop, args=(config,), daemon=True).start()
                logger.info('slstr_frp polling thread started')
            except Exception as _e:
                logger.warning('slstr_frp thread failed to start: %s', _e)
            try:
                from src.verifiers.sentinel2_burnscar import polling_loop as s2_burnscar_polling_loop
                Thread(target=s2_burnscar_polling_loop, args=(config,), daemon=True).start()
                logger.info('sentinel2_burnscar polling thread started')
            except Exception as _e:
                logger.warning('sentinel2_burnscar thread failed to start: %s', _e)
            # --- end Phase-2 corroboration sources ---
            logger.info('Ground-truth + scoring daemons started')
        except Exception as _gte:
            logger.error('GT/scoring init failed: %s', _gte)

    if SYSTEM_READY:
        Thread(target=download_historical_data, daemon=True).start()

    Thread(target=effis_monitoring_loop, daemon=True).start()
    Thread(target=s2_swir_monitoring_loop, daemon=True).start()
    Thread(target=adr_monitoring_loop, daemon=True).start()

    logger.info("Starting ADR WildFire web interface at http://0.0.0.0:8081")
    try:
        app.run(host='0.0.0.0', port=8081, debug=False)
    except KeyboardInterrupt:
        logger.info("\nShutdown requested")
        logger.info("ADR WildFire Solution stopped")
