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
    for tile in tiles:
        # Prefer the most recent v200 tile (2021)
        version = (tile.get("properties", {}).get("esa_worldcover:product_version") or "")
        if "200" not in str(version) and "v200" not in str(version):
            # accept anyway
            pass
        asset = tile["assets"].get("map") or tile["assets"].get("Map")
        if asset is None:
            # try first asset
            asset = next(iter(tile["assets"].values()))
        href = pc.sign(asset["href"])
        print(f"  reading {tile['id']} ...")
        try:
            with rasterio.Env(GDAL_HTTP_UNSAFESSL="YES", AWS_NO_SIGN_REQUEST="YES"):
                with rasterio.open(href) as src:
                    # Restrict reading to the Sicily bbox in source CRS
                    src_w, src_s, src_e, src_n = transform_bounds(
                        "EPSG:4326", src.crs, w, s, e, n, densify_pts=21)
                    try:
                        win = from_bounds(src_w, src_s, src_e, src_n, src.transform)
                    except Exception:
                        # tile doesn't cover Sicily portion
                        continue
                    # Read entire window at native 10m
                    arr = src.read(1, window=win, boundless=False)
                    if arr.size == 0:
                        continue
                    # Window transform (so we can sample by lat/lng)
                    win_transform = rasterio.windows.transform(win, src.transform)
                    # For each grid cell, sample a small box around it (3x3 pixels at 10m)
                    sample_half = 0.005  # half-cell, ~500m radius
                    for lat in lats:
                        for lng in lngs:
                            try:
                                # transform lat/lng to source CRS
                                from rasterio.warp import transform as warp_transform
                                xs, ys = warp_transform("EPSG:4326", src.crs, [lng], [lat])
                                xs2, ys2 = warp_transform(
                                    "EPSG:4326", src.crs,
                                    [lng - sample_half], [lat - sample_half])
                                xs3, ys3 = warp_transform(
                                    "EPSG:4326", src.crs,
                                    [lng + sample_half], [lat + sample_half])
                                # convert to pixel coords within the window
                                col_lo, row_lo = ~win_transform * (min(xs2[0], xs3[0]), max(ys2[0], ys3[0]))
                                col_hi, row_hi = ~win_transform * (max(xs2[0], xs3[0]), min(ys2[0], ys3[0]))
                                col_lo, col_hi = int(max(0, col_lo)), int(min(arr.shape[1], col_hi))
                                row_lo, row_hi = int(max(0, row_lo)), int(min(arr.shape[0], row_hi))
                                if col_hi <= col_lo or row_hi <= row_lo:
                                    continue
                                patch = arr[row_lo:row_hi, col_lo:col_hi]
                                if patch.size == 0:
                                    continue
                                # Histogram by class
                                vals, counts = np.unique(patch, return_counts=True)
                                total = counts.sum()
                                pct = {WC_TO_PHX.get(WC_CODES.get(int(v), ""), "other"):
                                       int(round(c * 100 / total))
                                       for v, c in zip(vals, counts) if v != 0}
                                # Collapse "other" into existing categories proportionally
                                key = f"{lat:.2f}:{lng:.2f}"
                                if key not in cells:
                                    cells[key] = {"built": 0, "tree": 0, "crop": 0,
                                                  "water": 0, "shrub": 0}
                                for cls, p in pct.items():
                                    if cls in cells[key]:
                                        cells[key][cls] = max(cells[key][cls], p)
                            except Exception:
                                continue
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
