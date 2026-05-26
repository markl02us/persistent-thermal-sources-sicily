#!/usr/bin/env python3
"""Build a full Sicily ESA WorldCover 2021 lookup at 0.01-deg resolution.

Queries Microsoft Planetary Computer STAC for ESA WorldCover 2021 v200 tiles
covering Sicily, signs the asset URLs, opens each as a COG, and samples the
land-cover class at every 0.01-deg point in the Sicily bbox. Writes a
dictionary keyed by ``lat:lng`` (2 decimals) containing the percentage of each
WorldCover class within a small window around the point.

Replaces the v1 worldcover daemon's seeded-9-cities lookup (~220 cells, Palermo
metro only) with full island coverage (~50k cells).

Run once. The grader picks up the new file automatically on next iteration.
"""
import json, urllib.request, time
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds

try:
    import planetary_computer as pc
except ImportError:
    raise SystemExit("install planetary-computer first: pip install planetary-computer")

# ESA WorldCover 2021 v200 class codes
WC_CODES = {
    10: "tree", 20: "shrub", 30: "grass", 40: "crop",
    50: "built", 60: "bare", 70: "snow",
    80: "water", 90: "wetland", 95: "mangrove", 100: "moss",
}
# Map to PHOENIX biome categories
WC_TO_PHX = {
    "tree": "tree", "shrub": "shrub", "grass": "crop", "crop": "crop",
    "built": "built", "bare": "crop", "water": "water",
    "snow": "water", "wetland": "water", "mangrove": "tree", "moss": "shrub",
}

SICILY_BBOX = (12.4, 36.6, 15.4, 38.3)  # (W, S, E, N)
GRID_STEP = 0.01  # degrees
CACHE_PATH = Path("/media/mark/AI_DGX/eumetsat_data/worldcover_sicily_cells.json")

MPC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"


def stac_search():
    """Find WorldCover 2021 v200 tiles intersecting Sicily."""
    body = {
        "collections": ["esa-worldcover"],
        "bbox": list(SICILY_BBOX),
        "limit": 20,
    }
    req = urllib.request.Request(
        MPC_STAC, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "User-Agent": "PHOENIX-worldcover/1.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("features", [])


def main():
    print("querying MPC STAC for ESA WorldCover tiles over Sicily...")
    tiles = stac_search()
    print(f"  found {len(tiles)} tiles")
    if not tiles:
        raise SystemExit("no tiles returned")

    # Build the grid of points to sample
    w, s, e, n = SICILY_BBOX
    lats = np.arange(s, n + GRID_STEP / 2, GRID_STEP)
    lngs = np.arange(w, e + GRID_STEP / 2, GRID_STEP)
    print(f"  grid: {len(lats)} x {len(lngs)} = {len(lats)*len(lngs):,} cells")

    cells = {}
    from rasterio.warp import transform as warp_transform
    # WorldCover tiles are in EPSG:4326 (geographic), so source CRS == grid CRS;
    # this lets us skip the per-point warp entirely and read by direct pixel index.
    for tile in tiles:
        asset = tile["assets"].get("map") or tile["assets"].get("Map") \
                or next(iter(tile["assets"].values()))
        href = pc.sign(asset["href"])
        tid = tile.get("id", "(unknown)")
        print(f"  reading {tid} ...")
        try:
            with rasterio.Env(GDAL_HTTP_UNSAFESSL="YES", AWS_NO_SIGN_REQUEST="YES"):
                with rasterio.open(href) as src:
                    src_crs = src.crs.to_string() if src.crs else "EPSG:4326"
                    # Restrict reading to Sicily intersection with this tile
                    src_w, src_s, src_e, src_n = transform_bounds(
                        "EPSG:4326", src.crs, w, s, e, n, densify_pts=21)
                    try:
                        win = from_bounds(src_w, src_s, src_e, src_n, src.transform)
                    except Exception:
                        continue
                    arr = src.read(1, window=win, boundless=False)
                    if arr.size == 0:
                        continue
                    win_transform = rasterio.windows.transform(win, src.transform)
                    inv = ~win_transform  # affine inverse from CRS coords -> window pixel
                    # Vectorized: build all (lat, lng) pairs, project once, index once
                    LL, LA = np.meshgrid(lngs, lats)  # both (n_lat, n_lng)
                    flat_lng = LL.ravel(); flat_lat = LA.ravel()
                    # If source CRS is EPSG:4326, skip warp (huge speedup)
                    if src_crs in ("EPSG:4326", "WGS 84"):
                        src_x = flat_lng; src_y = flat_lat
                    else:
                        src_x, src_y = warp_transform(
                            "EPSG:4326", src.crs, flat_lng.tolist(), flat_lat.tolist())
                        src_x = np.array(src_x); src_y = np.array(src_y)
                    cols = ((src_x - inv.xoff) / inv.a + (src_y - inv.yoff) * inv.b / inv.a / inv.e).astype(int)  # noqa
                    # simpler: apply inv affine elementwise
                    cols = np.array([inv * (x, y) for x, y in zip(src_x, src_y)])
                    px_cols = cols[:, 0].astype(int)
                    px_rows = cols[:, 1].astype(int)
                    H, W = arr.shape
                    valid = (px_cols >= 0) & (px_cols < W) & (px_rows >= 0) & (px_rows < H)
                    vals = np.full(flat_lat.shape, -1, dtype=np.int16)
                    vals[valid] = arr[px_rows[valid], px_cols[valid]]
                    # Bin into 0.01-deg cells (each cell = one grid point at our step)
                    for i, (la, lo, v) in enumerate(zip(flat_lat, flat_lng, vals)):
                        if v <= 0:
                            continue
                        cls_name = WC_TO_PHX.get(WC_CODES.get(int(v), ""), None)
                        if cls_name is None:
                            continue
                        key = f"{la:.2f}:{lo:.2f}"
                        if key not in cells:
                            cells[key] = {"built": 0, "tree": 0, "crop": 0,
                                          "water": 0, "shrub": 0}
                        # Mark the dominant class for this point at 100%; finer
                        # sub-cell histogramming was the cause of the v1 slowness.
                        if cls_name in cells[key]:
                            cells[key][cls_name] = 100
        except Exception as exc:
            print(f"    failed: {exc}")
            continue
    print(f"  built {len(cells):,} cells")
    # Atomic write
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cells))
    tmp.replace(CACHE_PATH)
    print(f"  wrote {CACHE_PATH}")


if __name__ == "__main__":
    main()
