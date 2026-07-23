#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
GRIB2 generator for Copernicus Marine (CMEMS) IBI surface currents.

Fetches surface u/v from the Copernicus Marine `anfc` (analysis+forecast)
dataset via server-side subsetting (copernicusmarine.subset — bounding box +
time range + variables only, not whole-domain files) and encodes as a
multi-message GRIB2 file per region using the shared grib2_writer tail (same
wire format as generate_ofs_gribs.py: grid_simple packing, discipline 10 /
category 1, params 2=u/3=v — zero plugin changes).

Much simpler than the NOS OFS pipeline: the IBI dataset is already a plain
regular lat/lon grid (1-D latitude/longitude coordinates, confirmed live
2026-07-23 to ~1e-15 deg uniform spacing), already true east/north
(`eastward_/northward_sea_water_velocity`), has no depth dimension to
select (genuine 2D surface product), and is one continuous time series
with no nowcast/forecast file split — so no pooling, no curvilinear
regridding, no sigma-level handling, no nowcast+forecast concatenation.
See sources/cmems_ibi.py's module docstring for the live-verification
details and the cross-validation against an independent regional model.

GRIB2 files go to --output-dir. This script only writes files — it does
NOT upload them or touch the catalog. Uploading to the rolling GitHub
Release and updating tide-current-index.json are handled elsewhere (see
.github/workflows/generate-ibi-gribs.yml and sources/cmems_ibi.py, which
owns the static catalog entry and is run weekly by generate_index.py).

Usage:
    python generate_ibi_gribs.py [--output-dir DIR] [--regions r1,r2] [--forecast-hours N]

Requires: copernicusmarine, numpy, xarray, netCDF4, eccodes
(not installed by the weekly index generator — this script runs in a
dedicated daily workflow that supplies these deps.)

Auth: copernicusmarine reads COPERNICUSMARINE_SERVICE_USERNAME/_PASSWORD
env vars automatically, or falls back to a locally saved `copernicusmarine
login` session — no explicit login call needed here either way.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import xarray as xr
import copernicusmarine

from grib2_writer import check_uniform, encode_grib2

# sources/cmems_ibi.py is the single source of truth for the CMEMS IBI
# region metadata (REGIONS) and dataset id — it is stdlib-only and lives in
# the sibling sources/ directory alongside the other weekly collectors, so
# it is reached via sys.path rather than a package import (same convention
# as generate_ofs_gribs.py / sources/nos_ofs.py).
_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)
import cmems_ibi  # noqa: E402

REGIONS = cmems_ibi.REGIONS
DATASET_ID = cmems_ibi.DATASET_ID


def process_region(rid, r, out_dir, forecast_hours_override=None):
    out = os.path.join(out_dir, f"{rid}_currents.grb2")
    b = r["bounds"]
    forecast_hours = forecast_hours_override if forecast_hours_override is not None else r["forecast_hours"]

    # Reference time = now, floored to the hour. The anfc dataset is one
    # continuous time series (analysis + forecast merged, confirmed live —
    # no nowcast/forecast file split to concatenate), so every requested
    # hour is a forward step from this reference; out-of-range requests
    # clip gracefully with a warning rather than erroring.
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(hours=forecast_hours)

    nc_path = os.path.join(out_dir, f"_{rid}_raw.nc")
    copernicusmarine.subset(
        dataset_id=DATASET_ID,
        variables=["uo", "vo"],
        minimum_longitude=b["min_lon"],
        maximum_longitude=b["max_lon"],
        minimum_latitude=b["min_lat"],
        maximum_latitude=b["max_lat"],
        start_datetime=now.strftime("%Y-%m-%dT%H:%M:%S"),
        end_datetime=end.strftime("%Y-%m-%dT%H:%M:%S"),
        output_filename=os.path.basename(nc_path),
        output_directory=out_dir,
        overwrite=True,
        disable_progress_bar=True,
    )

    au, av, avu, avv, ah = [], [], [], [], []
    ds = xr.open_dataset(nc_path)
    try:
        lat = ds["latitude"].values.astype(np.float64)
        lon = ds["longitude"].values.astype(np.float64)
        times = ds["time"].values
        if len(times) == 0:
            return None

        # Defensive ascending-order check, matching process_model's
        # convention in generate_ofs_gribs.py — confirmed ascending in
        # practice, but don't assume it forever.
        flip_lat = lat.shape[0] > 1 and lat[-1] < lat[0]
        if flip_lat:
            lat = lat[::-1]
        flip_lon = lon.shape[0] > 1 and lon[-1] < lon[0]
        if flip_lon:
            lon = lon[::-1]

        ni, nj = len(lon), len(lat)
        inc_lat = (lat[-1] - lat[0]) / (nj - 1) if nj > 1 else 0.0
        inc_lon = (lon[-1] - lon[0]) / (ni - 1) if ni > 1 else 0.0
        check_uniform(lat, inc_lat, "latitude", rid)
        check_uniform(lon, inc_lon, "longitude", rid)

        ref_time = times[0]
        bd = int(np.datetime_as_string(ref_time, unit="D").replace("-", ""))
        bt = int(np.datetime_as_string(ref_time, unit="h")[-2:]) * 100

        for i in range(len(times)):
            u = ds["uo"].isel(time=i).values.astype(np.float64)
            v = ds["vo"].isel(time=i).values.astype(np.float64)
            if flip_lat:
                u = u[::-1, :]
                v = v[::-1, :]
            if flip_lon:
                u = u[:, ::-1]
                v = v[:, ::-1]
            # Land is already NaN via the source's own CF _FillValue —
            # confirmed live, no separate mask variable to fetch.
            valid = np.isfinite(u) & np.isfinite(v)
            step = round((times[i] - ref_time) / np.timedelta64(1, "h"))

            au.append(u)
            av.append(v)
            avu.append(valid)
            avv.append(valid)
            ah.append(step)
    finally:
        ds.close()
        os.remove(nc_path)

    if not au:
        return None
    encode_grib2(
        float(lat[0]), float(lon[0]), inc_lat, inc_lon, ni, nj,
        au, av, avu, avv, ah, bd, bt, out,
    )
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/ibi_gribs")
    parser.add_argument(
        "--regions", default=None,
        help="Comma-separated region ids to process (default: all regions)",
    )
    parser.add_argument(
        "--forecast-hours", type=int, default=None,
        help="Override the forecast horizon per region (testing only)",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    selected = args.regions.split(",") if args.regions else list(REGIONS.keys())

    generated = 0
    for rid in selected:
        r = REGIONS.get(rid)
        if r is None:
            print(f"Unknown region id: {rid}", file=sys.stderr)
            continue

        print(f"\n=== {rid} ({r['name']}) ===", file=sys.stderr)
        try:
            path = process_region(rid, r, args.output_dir, args.forecast_hours)
        except Exception as e:
            print(f"  {rid}: ERROR — {e}", file=sys.stderr)
            path = None

        if not path or not os.path.exists(path):
            print(f"  {rid}: Failed to generate GRIB2", file=sys.stderr)
            continue

        sz = os.path.getsize(path)
        print(f"  {os.path.basename(path)}: {sz} bytes", file=sys.stderr)
        generated += 1

    # Partial failures are tolerated, but producing NOTHING means the
    # pipeline itself is broken (auth failure, API outage, schema change) —
    # fail the run so CI goes red instead of silently leaving the release
    # assets to go stale.
    if generated == 0:
        print("\nNo GRIB2 files generated for any region — failing.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
