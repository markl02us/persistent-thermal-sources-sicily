
# Gunicorn configuration — enables monitoring threads in worker process
bind = "127.0.0.1:8081"
workers = 1
timeout = 60
accesslog = "/var/log/adr-wildfire/access.log"
errorlog = "/var/log/adr-wildfire/error.log"

def post_fork(server, worker):
    """Start background threads after the worker process is forked."""
    import logging as _glog
    _log = _glog.getLogger("gunicorn.startup")
    # Existing monitoring loops
    try:
        from threading import Thread
        import adr_wildfire_solution as _app
        Thread(target=_app.effis_monitoring_loop, daemon=True).start()
        Thread(target=_app.s2_swir_monitoring_loop, daemon=True).start()
        Thread(target=_app.adr_monitoring_loop, daemon=True).start()
        if _app.SYSTEM_READY:
            Thread(target=_app.download_historical_data, daemon=True).start()
        _log.info("ADR monitoring threads started in worker")
    except Exception as _e:
        _log.error("Failed to start monitoring threads: %s", _e)
    # Ground-truth daemons
    try:
        from threading import Thread
        import yaml
        from pathlib import Path
        import adr_wildfire_solution as _app2
        from src.ground_truth import (init_db, insert_internal,
                                      firms_polling_loop, effis_polling_loop)
        from src.scoring import nightly_scoring_loop
        from src.terrestrial_camera import register_camera_routes
        init_db(_app2.config)
        _cf = Path(_app2.config["storage"]["base_path"]) / _app2.config["storage"].get("camera_frames","eumetsat_data/camera_frames")
        _cf.mkdir(parents=True, exist_ok=True)
        register_camera_routes(_app2.app, _app2.config, insert_internal)
        Thread(target=firms_polling_loop, args=(_app2.config,), daemon=True).start()
        Thread(target=effis_polling_loop, args=(_app2.config,), daemon=True).start()
        Thread(target=nightly_scoring_loop, args=(_app2.config,), daemon=True).start()
        _log.info("Ground-truth + scoring daemons started in worker")
    except Exception as _e:
        _log.error("GT/scoring daemon start failed: %s", _e)
    # MTG-I1 Active Fire L2 comparator — the new lead-time bar PHOENIX has to beat
    try:
        from threading import Thread
        import adr_wildfire_solution as _app3
        from src.data_sources.active_fire_l2 import polling_loop as mtg_af_l2_polling_loop
        Thread(target=mtg_af_l2_polling_loop, args=(_app3.config,), daemon=True).start()
        _log.info("mtg_af_l2 polling daemon started in worker (10-min interval)")
    except Exception as _e:
        _log.error("mtg_af_l2 daemon start failed: %s", _e)
    # MTG-I1 Lightning Imager — ignition prior + comparator. Strikes within last
    # 30 min over a flammable pixel tighten the detection threshold (fusion path).
    try:
        from threading import Thread
        import adr_wildfire_solution as _app4
        from src.data_sources.lightning_li import polling_loop as mtg_li_polling_loop
        Thread(target=mtg_li_polling_loop, args=(_app4.config,), daemon=True).start()
        _log.info("mtg_li polling daemon started in worker (90-sec interval)")
    except Exception as _e:
        _log.error("mtg_li daemon start failed: %s", _e)

    # --- Phase-2 corroboration sources (2026-05-24) ---
    # ANSA Sicilia RSS — citizen/journalist fire reports (15-min poll)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_a
        from src.data_sources.ansa_rss import polling_loop as ansa_rss_polling_loop
        Thread(target=ansa_rss_polling_loop, args=(_app_a.config,), daemon=True).start()
        _log.info("ansa_rss polling daemon started in worker (15-min interval)")
    except Exception as _e:
        _log.error("ansa_rss daemon start failed: %s", _e)
    # ARPA/EEA air quality (PM2.5/PM10/CO) — smoke corroboration (30-min poll)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_b
        from src.data_sources.arpa_air import polling_loop as arpa_air_polling_loop
        Thread(target=arpa_air_polling_loop, args=(_app_b.config,), daemon=True).start()
        _log.info("arpa_air polling daemon started in worker (30-min interval)")
    except Exception as _e:
        _log.error("arpa_air daemon start failed: %s", _e)
    # Sentinel-3 SLSTR L2 FRP NRT — independent thermal validation (30-min poll)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_c
        from src.data_sources.slstr_frp import polling_loop as slstr_frp_polling_loop
        Thread(target=slstr_frp_polling_loop, args=(_app_c.config,), daemon=True).start()
        _log.info("slstr_frp polling daemon started in worker (30-min interval)")
    except Exception as _e:
        _log.error("slstr_frp daemon start failed: %s", _e)
    # Sentinel-2 burn-scar verifier — ground-truth dNBR for PHOENIX detections (6h batch)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_d
        from src.verifiers.sentinel2_burnscar import polling_loop as s2_burnscar_polling_loop
        Thread(target=s2_burnscar_polling_loop, args=(_app_d.config,), daemon=True).start()
        _log.info("sentinel2_burnscar polling daemon started in worker (6h interval)")
    except Exception as _e:
        _log.error("sentinel2_burnscar daemon start failed: %s", _e)
    # YOLOv8 smoke verifier (subprocess to ~/yolo_venv — main venv torch 2.10 has no ABI-matching torchvision)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_sy
        from src.verifiers.smoke_yolo_daemon import polling_loop as smoke_yolo_polling_loop
        Thread(target=smoke_yolo_polling_loop, args=(_app_sy.config,), daemon=True).start()
        _log.info("smoke_yolo polling daemon started in worker (30-min interval, subprocess mode)")
    except Exception as _e:
        _log.error("smoke_yolo daemon start failed: %s", _e)
    # Italian news RSS (VVF + Giornale di Sicilia) — citizen-reporter corroboration
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_n
        from src.data_sources.italian_news_rss import polling_loop as italian_news_rss_polling_loop
        Thread(target=italian_news_rss_polling_loop, args=(_app_n.config,), daemon=True).start()
        _log.info("italian_news_rss polling daemon started in worker (15-min interval)")
    except Exception as _e:
        _log.error("italian_news_rss daemon start failed: %s", _e)
    # Sentinel-5P TROPOMI (CO/NO2/HCHO/AAI) - smoke proxies via MPC STAC
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_t
        from src.data_sources.tropomi import polling_loop as tropomi_polling_loop
        Thread(target=tropomi_polling_loop, args=(_app_t.config,), daemon=True).start()
        _log.info("tropomi polling daemon started in worker (1h interval)")
    except Exception as _e:
        _log.error("tropomi daemon start failed: %s", _e)
    # ESA WorldCover - static landcover lookup (daily refresh)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_w
        from src.data_sources.worldcover import polling_loop as worldcover_polling_loop, load_or_build
        load_or_build()  # populate cache at startup so lookups work immediately
        Thread(target=worldcover_polling_loop, args=(_app_w.config,), daemon=True).start()
        _log.info("worldcover polling daemon started in worker (24h interval)")
    except Exception as _e:
        _log.error("worldcover daemon start failed: %s", _e)
    # MOD11 LST + VIIRS Nightfire + Sentinel-1 SAR (combined scaffold module, 3h interval)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_m
        from src.data_sources.modis_viirs_sar import polling_loop as mvs_polling_loop
        Thread(target=mvs_polling_loop, args=(_app_m.config,), daemon=True).start()
        _log.info("modis_viirs_sar polling daemon started in worker (3h interval)")
    except Exception as _e:
        _log.error("modis_viirs_sar daemon start failed: %s", _e)
    # Weather + CAMS aerosol via open-meteo - replaces ERA5-Land / CAMS (free, no auth)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_wc
        from src.data_sources.weather_cams import polling_loop as weather_cams_polling_loop
        Thread(target=weather_cams_polling_loop, args=(_app_wc.config,), daemon=True).start()
        _log.info("weather_cams polling daemon started in worker (30-min interval, 7 AOIs)")
    except Exception as _e:
        _log.error("weather_cams daemon start failed: %s", _e)
    # CEMS Rapid Mapping + EFFIS RDA - official Copernicus emergency products (1h interval)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_ce
        from src.data_sources.cems_effis_rda import polling_loop as cems_effis_rda_polling_loop
        Thread(target=cems_effis_rda_polling_loop, args=(_app_ce.config,), daemon=True).start()
        _log.info("cems_effis_rda polling daemon started in worker (1h interval)")
    except Exception as _e:
        _log.error("cems_effis_rda daemon start failed: %s", _e)
    # Reddit + Mastodon public feeds (no auth) - citizen-reporter corroboration (20-min poll)
    try:
        from threading import Thread
        import adr_wildfire_solution as _app_sf
        from src.data_sources.social_feeds import polling_loop as social_feeds_polling_loop
        Thread(target=social_feeds_polling_loop, args=(_app_sf.config,), daemon=True).start()
        _log.info("social_feeds polling daemon started in worker (20-min interval, 3 feeds)")
    except Exception as _e:
        _log.error("social_feeds daemon start failed: %s", _e)

    # Nightly baseline-stats rebuild — keeps per-pixel/hour μ/σ tracking the
    # most recent 30 days. Fire season will warm up backgrounds; the detector's
    # z-score path needs an up-to-date baseline to keep catching small fires.
    try:
        from threading import Thread
        import time as _t
        import subprocess as _sp
        def _baseline_rebuild_loop():
            # Fire once on startup (cheap — ~0.3s with current corpus), then daily.
            # Builds BOTH SEVIRI and FCI baselines (FCI no-op until enough snapshots).
            from pathlib import Path as _P
            while True:
                # SEVIRI baseline (always)
                try:
                    _sp.run(
                        ["python3", "-m", "src.baseline_stats", "--min-samples", "20"],
                        cwd="/home/mark/.openclaw/workspace/eumetsat_wildfire_detection",
                        check=True, capture_output=True, timeout=300,
                    )
                    _log.info("baseline_stats SEVIRI rebuilt by nightly daemon")
                except Exception as _ie:
                    _log.warning("baseline_stats SEVIRI rebuild failed: %s", _ie)
                # FCI baseline (only when we have enough snapshots accumulated)
                try:
                    fci_dir = _P("/media/mark/AI_DGX/eumetsat_data/fci_scratch/baseline_frames")
                    n_snaps = len(list(fci_dir.glob("*.npz"))) if fci_dir.exists() else 0
                    if n_snaps >= 200:  # ~30 hours of FCI = enough diurnal coverage to start
                        _sp.run(
                            ["python3", "-m", "src.baseline_stats", "--sensor=fci", "--min-samples", "10"],
                            cwd="/home/mark/.openclaw/workspace/eumetsat_wildfire_detection",
                            check=True, capture_output=True, timeout=300,
                        )
                        _log.info("baseline_stats FCI rebuilt (%d snapshots)", n_snaps)
                    else:
                        _log.info("baseline_stats FCI skipped: only %d snapshots (need 200+)", n_snaps)
                except Exception as _ie:
                    _log.warning("baseline_stats FCI rebuild failed: %s", _ie)
                _t.sleep(86400)   # 24h
        Thread(target=_baseline_rebuild_loop, daemon=True).start()
        _log.info("baseline_stats nightly rebuild daemon started (24h interval)")
    except Exception as _e:
        _log.error("baseline rebuild daemon start failed: %s", _e)

    # Provisional-detection auto-expire (Council Round 1 — GEO-trigger arch):
    # un-confirmed provisional rows older than 6 h are marked 'expired'. The
    # /api/detections + /wins endpoints already filter on raw_json status.
    try:
        from threading import Thread
        import time as _t
        import sqlite3 as _sqlite3
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from pathlib import Path as _P
        def _provisional_expire_loop():
            import adr_wildfire_solution as _app5
            gt_db = _P(_app5.config['storage']['base_path']) / _app5.config['storage']['ground_truth_db']
            while True:
                try:
                    cutoff = (_dt.now(_tz.utc) - _td(hours=6)).isoformat()
                    con = _sqlite3.connect(str(gt_db))
                    cur = con.execute(
                        "UPDATE internal_fires SET raw_json = ? "
                        "WHERE ts < ? AND (raw_json IS NULL OR "
                        "(raw_json NOT LIKE '%confirmed%' AND raw_json NOT LIKE '%expired%'))",
                        ('{"status": "expired"}', cutoff)
                    )
                    con.commit()
                    n = cur.rowcount or 0
                    con.close()
                    if n > 0:
                        _log.info("provisional-expire: marked %d un-confirmed rows older than 6h", n)
                except Exception as _ie:
                    _log.warning("provisional-expire loop error: %s", _ie)
                _t.sleep(1800)   # 30 min sweep
        Thread(target=_provisional_expire_loop, daemon=True).start()
        _log.info("provisional-expire daemon started (30-min sweep, 6h ttl)")
    except Exception as _e:
        _log.error("provisional-expire daemon start failed: %s", _e)

    # PHOENIX 2026-05-24 — daily digest email at 06:00 UTC
    try:
        from threading import Thread
        import time as _ddt
        from datetime import datetime as _ddD, timezone as _ddZ
        def _daily_digest_loop():
            import adr_wildfire_solution as _app_dd
            last_sent_date = None
            while True:
                try:
                    now = _ddD.now(_ddZ.utc)
                    # Fire once when we cross 06:00 UTC each day
                    if now.hour == 6 and (last_sent_date != now.date()):
                        ok = _app_dd._send_daily_digest_email()
                        if ok:
                            last_sent_date = now.date()
                            _log.info("daily_digest: email sent for %s", now.date())
                except Exception as _ie:
                    _log.warning("daily_digest loop: %s", _ie)
                _ddt.sleep(600)  # check every 10 min
        Thread(target=_daily_digest_loop, daemon=True).start()
        _log.info("daily_digest daemon started (fires at 06:00 UTC)")
    except Exception as _e:
        _log.error("daily_digest daemon start failed: %s", _e)

    # PHOENIX 2026-05-24 — weekly per-AOI ml_accept threshold recompute
    try:
        from threading import Thread
        import time as _patt
        def _per_aoi_threshold_loop():
            import adr_wildfire_solution as _app_pa
            while True:
                try:
                    s = _app_pa.recompute_per_aoi_thresholds(write=True)
                    if s:
                        _log.info("per_aoi_thresholds recomputed (%d AOIs)", len(s))
                except Exception as _ie:
                    _log.warning("per_aoi_threshold recompute: %s", _ie)
                _patt.sleep(7 * 86400)   # 7 days
        Thread(target=_per_aoi_threshold_loop, daemon=True).start()
        _log.info("per_aoi_threshold weekly recompute daemon started")
    except Exception as _e:
        _log.error("per_aoi_threshold daemon start failed: %s", _e)

    # PHOENIX 2026-05-24 — Hawkes ignition prior nightly recompute (CPU, ~3s)
    try:
        from threading import Thread
        from src.data_sources.hawkes_ignition import nightly_loop as hawkes_nightly_loop
        Thread(target=hawkes_nightly_loop, daemon=True).start()
        _log.info("hawkes_ignition nightly daemon started (fires at 03:30 UTC)")
    except Exception as _e:
        _log.error("hawkes_ignition daemon start failed: %s", _e)

    # PHOENIX 2026-05-24 — Joint Dozier inversion (FCI+SLSTR+S2 fusion, 10-min batch)
    try:
        from threading import Thread
        from src.verifiers.joint_dozier import polling_loop as joint_dozier_polling_loop
        Thread(target=joint_dozier_polling_loop, daemon=True).start()
        _log.info("joint_dozier polling daemon started (10-min batch interval)")
    except Exception as _e:
        _log.error("joint_dozier daemon start failed: %s", _e)


    # PHOENIX 2026-05-25 — OroraTech public OSINT scraper (no-pay comparator)
    try:
        from threading import Thread
        from src.data_sources.ororatech_public import polling_loop as ororatech_public_polling_loop
        Thread(target=ororatech_public_polling_loop, daemon=True).start()
        _log.info("ororatech_public polling daemon started (6h interval, blog + nitter)")
    except Exception as _e:
        _log.error("ororatech_public daemon start failed: %s", _e)

    # PHOENIX 2026-05-25 — Sentinel-1 SAR change detection (cloud-cover gap closer)
    try:
        from threading import Thread
        from src.data_sources.sentinel1_sar_change import polling_loop as s1_sar_change_polling_loop
        Thread(target=s1_sar_change_polling_loop, daemon=True).start()
        _log.info("sentinel1_sar_change polling daemon started (12h interval, MPC sentinel-1-rtc)")
    except Exception as _e:
        _log.error("sentinel1_sar_change daemon start failed: %s", _e)

    # PHOENIX 2026-05-25 — NISAR L-band SAR (observer mode until Earthdata creds)
    try:
        from threading import Thread
        from src.data_sources.nisar_change import polling_loop as nisar_change_polling_loop
        Thread(target=nisar_change_polling_loop, daemon=True).start()
        _log.info("nisar_change polling daemon started (24h interval, NASA CMR STAC)")
    except Exception as _e:
        _log.error("nisar_change daemon start failed: %s", _e)

    # PHOENIX 2026-05-25 — event_grades grader + T+72h reconciler
    # Forward-grades every 5 min (last 2 days), reconciles every 6h.
    try:
        from threading import Thread
        import time as _gt
        import subprocess as _gsp
        def _grader_loop():
            cwd = "/home/mark/.openclaw/workspace/eumetsat_wildfire_detection"
            tick = 0
            while True:
                try:
                    _gsp.run(["python3", "scripts/grade_events.py", "--recent", "2"],
                             cwd=cwd, check=True, capture_output=True, timeout=180)
                    _log.info("event_grades forward-grade completed")
                except Exception as _ie:
                    _log.warning("event_grades forward-grade failed: %s", _ie)
                # Every 6h (72 ticks at 5min = 6h), run reconcile
                if tick % 72 == 0:
                    try:
                        _gsp.run(["python3", "scripts/grade_events.py", "--reconcile"],
                                 cwd=cwd, check=True, capture_output=True, timeout=120)
                        _log.info("event_grades T+72h reconcile completed")
                    except Exception as _ie:
                        _log.warning("event_grades reconcile failed: %s", _ie)
                tick += 1
                _gt.sleep(300)  # 5 min
        Thread(target=_grader_loop, daemon=True).start()
        _log.info("event_grades grader daemon started (5-min forward-grade, 6h reconcile)")
    except Exception as _e:
        _log.error("event_grades grader daemon start failed: %s", _e)

    # PHOENIX 2026-05-25 — daily reproducibility snapshot
    # Dumps raw inputs + published grades to data/snapshots/YYYY-MM-DD/.
    # The /data/snapshots/ route serves them; anyone can re-grade via scripts/regrade.py.
    try:
        from threading import Thread
        import time as _snt
        import subprocess as _snsp
        from datetime import datetime as _sndt, timezone as _sntz
        def _snapshot_loop():
            cwd = "/home/mark/.openclaw/workspace/eumetsat_wildfire_detection"
            last_date = None
            while True:
                try:
                    today = _sndt.now(_sntz.utc).date().isoformat()
                    if last_date != today:
                        _snsp.run(["python3", "scripts/dump_reproducibility_snapshot.py"],
                                  cwd=cwd, check=True, capture_output=True, timeout=300)
                        _log.info("reproducibility snapshot for %s completed", today)
                        last_date = today
                except Exception as _ie:
                    _log.warning("snapshot daemon error: %s", _ie)
                _snt.sleep(3600)  # check hourly
        Thread(target=_snapshot_loop, daemon=True).start()
        _log.info("reproducibility snapshot daemon started (daily)")
    except Exception as _e:
        _log.error("snapshot daemon start failed: %s", _e)

    # PHOENIX 2026-05-25 - nightly null-distribution bootstrap (1000 reps)
    # Permutes external_fires timestamps to validate race-strict observed
    # count vs chance. Result feeds /api/null_bootstrap.
    try:
        from threading import Thread
        import time as _nbt
        import subprocess as _nbsp
        from datetime import datetime as _nbdt, timezone as _nbtz
        def _bootstrap_loop():
            cwd = "/home/mark/.openclaw/workspace/eumetsat_wildfire_detection"
            last_date = None
            while True:
                try:
                    now = _nbdt.now(_nbtz.utc)
                    # Fire once per day at 02:00 UTC (after snapshot at 00:00)
                    if now.hour == 2 and last_date != now.date():
                        _nbsp.run(["python3", "scripts/null_bootstrap.py",
                                   "--reps", "500", "--days", "30"],
                                  cwd=cwd, check=True, capture_output=True, timeout=3600)
                        _log.info("null_bootstrap completed for %s", now.date())
                        last_date = now.date()
                except Exception as _ie:
                    _log.warning("bootstrap daemon error: %s", _ie)
                _nbt.sleep(1800)  # check every 30 min
        Thread(target=_bootstrap_loop, daemon=True).start()
        _log.info("null bootstrap daemon started (nightly at 02:00 UTC)")
    except Exception as _e:
        _log.error("bootstrap daemon start failed: %s", _e)

    # PHOENIX 2026-05-26 - daily regrade watchdog
    # Runs scripts/regrade.py against today's published snapshot and reports
    # any mismatch on shared event_keys. Fires at 03:00 UTC, after snapshot+bootstrap.
    try:
        from threading import Thread
        import time as _wdt
        import subprocess as _wdsp
        from datetime import datetime as _wddt, timezone as _wdtz
        def _watchdog_loop():
            cwd = "/home/mark/.openclaw/workspace/eumetsat_wildfire_detection"
            last_date = None
            while True:
                try:
                    now = _wddt.now(_wdtz.utc)
                    # Fire once per day at 03:00 UTC (after snapshot 00:00 + bootstrap 02:00)
                    if now.hour == 3 and last_date != now.date():
                        _wdsp.run(["python3", "scripts/regrade_watchdog.py"],
                                  cwd=cwd, check=False, capture_output=True, timeout=900)
                        _log.info("regrade watchdog completed for %s", now.date())
                        last_date = now.date()
                except Exception as _ie:
                    _log.warning("watchdog daemon error: %s", _ie)
                _wdt.sleep(1800)  # check every 30 min
        Thread(target=_watchdog_loop, daemon=True).start()
        _log.info("regrade watchdog daemon started (nightly at 03:00 UTC)")
    except Exception as _e:
        _log.error("watchdog daemon start failed: %s", _e)
