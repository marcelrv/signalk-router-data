#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
GRIB2 generator for MET Norway NorKyst v3 (800 m) forecast surface currents
along the Norwegian coast, read over OPeNDAP from thredds.met.no.

Pipeline: open the "best estimate" aggregation -> find the latest run from
forecast_reference_time -> per region, read only the grid window covering
the region at the surface level -> regrid onto a regular lat/lon grid with
the DCSM regridder (Delaunay on the source grid, triangles restricted to
grid neighbours, so water is never interpolated across land) -> blank open
sea farther than offshore_km from land -> write one multi-message GRIB2 per
region with the shared grib2_writer tail.

Source facts (checked on the live server, Nov 2026):
  - Dataset norkystv3_800m_m00_be: polar-stereographic ROMS grid, Y x X =
    1148 x 2747 with 2-D lon/lat. The legacy fou-hi/norkyst800m-1h product
    stopped on 2025-10-05.
  - u_eastward / v_northward are already rotated to east/north (m/s), on
    z-levels; depth index 0 is the surface (0 m). Land is _FillValue.
  - One run per day at 00 UTC, hourly to about +120 h. `time` is a long
    best-estimate axis (analysis hours of earlier runs followed by the
    latest forecast), so the hours are selected relative to
    forecast_reference_time.
  - Licence CC-BY-4.0, MET Norway.

Usage:
    python generate_norkyst_gribs.py [--output-dir DIR] [--regions r1,r2]
        [--forecast-hours N]

Requires: numpy, scipy, netCDF4 (with OPeNDAP support, as in the PyPI
wheels), eccodes.
"""

import argparse
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import netCDF4
import numpy as np
from scipy.spatial import cKDTree

from grib2_writer import encode_grib2, retry
from generate_dcsm_gribs import KM_PER_DEG_LAT, _project, build_regridder, target_axes

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)
import norkyst  # noqa: E402

REGIONS = norkyst.REGIONS
DATASET_URL = norkyst.DATASET_URL

# Source cells this far outside a region box are still read, so the
# triangulation reaches the box edges.
CROP_MARGIN_DEG = 0.03
# Hours per OPeNDAP request: a 1000 x 1000 window is ~2 MB per hour per
# component, keeping each request well below the server's size limit.
HOURS_PER_REQUEST = 12
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _open():
    ds = netCDF4.Dataset(DATASET_URL)
    ds.set_auto_mask(True)
    return ds


def _seconds(var) -> np.ndarray:
    units = getattr(var, "units", "")
    if not units.startswith("seconds since 1970-01-01"):
        raise RuntimeError(f"unexpected time units {units!r}")
    return np.ma.filled(var[:], np.nan).astype(np.float64)


def select_hours(ds, min_hours: int, max_hours: int) -> tuple:
    """(run datetime, [(time index, lead hour), ...]) for the contiguous
    hourly slots from the run time up to +max_hours. Fails if fewer than
    +min_hours are available."""
    ref = float(np.ravel(_seconds(ds["forecast_reference_time"]))[0])
    run = EPOCH + timedelta(seconds=ref)
    t = _seconds(ds["time"])
    # The best-estimate axis is long; the run is at its tail.
    first = int(np.searchsorted(t, ref - 1.0))
    picked = {}
    for i in range(first, t.size):
        lead = (t[i] - ref) / 3600.0
        if 0 <= lead <= max_hours + 1e-6 and abs(lead - round(lead)) < 1e-6:
            picked.setdefault(int(round(lead)), i)
    last = -1
    while last + 1 in picked:
        last += 1
    if last < min_hours:
        raise RuntimeError(f"run {run:%Y-%m-%d %HZ} only reaches +{last} h (need +{min_hours} h)")
    return run, [(picked[h], h) for h in range(last + 1)]


def crop_window(lat, lon, bounds: dict, m_lat: float = CROP_MARGIN_DEG,
                m_lon: float = CROP_MARGIN_DEG) -> tuple:
    inside = ((lon >= bounds["min_lon"] - m_lon) & (lon <= bounds["max_lon"] + m_lon)
              & (lat >= bounds["min_lat"] - m_lat) & (lat <= bounds["max_lat"] + m_lat))
    r, c = np.where(inside)
    if r.size == 0:
        raise RuntimeError("region lies outside the model grid")
    return slice(r.min(), r.max() + 1), slice(c.min(), c.max() + 1)


def read_surface(ds, name, slots, rs, cs) -> np.ndarray:
    """(n_hours, ny, nx) float64 with NaN on land, read in chunks of
    consecutive time indices."""
    out = []
    idx = [ti for ti, _ in slots]
    for k in range(0, len(idx), HOURS_PER_REQUEST):
        chunk = idx[k:k + HOURS_PER_REQUEST]
        lo, hi = chunk[0], chunk[-1] + 1
        if hi - lo != len(chunk):
            raise RuntimeError(f"{name}: time indices not contiguous")
        block = retry(lambda: ds[name][lo:hi, 0, rs, cs], label=f"{name}[{lo}:{hi}]")
        out.append(np.ma.filled(block.astype(np.float64), np.nan))
    return np.concatenate(out, axis=0)


def land_cells(ds, lat, lon, bounds: dict, time_index: int, km: float) -> tuple:
    """lat/lon of the land cells within km of the region box. Read from a
    window padded by km (one hour of u only), so land just outside the box
    still counts when measuring how far a sea cell is from the coast."""
    mid = np.radians(0.5 * (bounds["min_lat"] + bounds["max_lat"]))
    pad_lat = km / KM_PER_DEG_LAT
    pad_lon = km / (KM_PER_DEG_LAT * np.cos(mid))
    rs, cs = crop_window(lat, lon, bounds, pad_lat, pad_lon)
    u = retry(lambda: ds["u_eastward"][time_index, 0, rs, cs], label="land mask")
    land = np.ma.getmaskarray(u)
    return lat[rs, cs][land], lon[rs, cs][land]


def offshore_mask(land_lat, land_lon, lats, lons, km: float) -> np.ndarray:
    """True for target cells within km of a land cell."""
    lon_g, lat_g = np.meshgrid(lons, lats)
    if land_lat.size == 0:
        return np.zeros(lat_g.shape, dtype=bool)
    lat0 = float(np.mean(lats))
    tree = cKDTree(_project(land_lon, land_lat, lat0))
    d, _ = tree.query(_project(lon_g.ravel(), lat_g.ravel(), lat0), distance_upper_bound=km)
    return np.isfinite(d).reshape(lat_g.shape)


def process_region(rid, r, ds, lat, lon, run, slots, out_dir):
    out = os.path.join(out_dir, f"{rid}_currents.grb2")
    rs, cs = crop_window(lat, lon, r["bounds"])
    lat2d, lon2d = lat[rs, cs], lon[rs, cs]
    lats, lons = target_axes(r["bounds"], r["target_res_lat_deg"], r["target_res_lon_deg"])
    nj, ni = len(lats), len(lons)

    u = read_surface(ds, "u_eastward", slots, rs, cs)
    v = read_surface(ds, "v_northward", slots, rs, cs)
    wet = np.isfinite(u[0]) & np.isfinite(v[0])
    if (np.isfinite(u) != wet).any() or (np.isfinite(v) != wet).any():
        raise RuntimeError(f"{rid}: land mask changes between hours")

    mat, valid, st = build_regridder(lat2d, lon2d, wet, lats, lons)
    if r.get("offshore_km"):
        km = float(r["offshore_km"])
        land_lat, land_lon = land_cells(ds, lat, lon, r["bounds"], slots[0][0], km)
        near = offshore_mask(land_lat, land_lon, lats, lons, km)
        valid = valid & near
    print(f"  {rid}: source window {lat2d.shape[0]}x{lat2d.shape[1]}, {int(wet.sum())} wet cells; "
          f"target {ni}x{nj}, {int(valid.sum())} valid after offshore cut "
          f"({st['valid']} before; {st['interp']} interpolated, {st['nearest']} nearest)",
          file=sys.stderr)

    au, av, avu, ah = [], [], [], []
    for k, (_, lead) in enumerate(slots):
        au.append((mat @ u[k][wet]).reshape(nj, ni))
        av.append((mat @ v[k][wet]).reshape(nj, ni))
        avu.append(valid)
        ah.append(lead)

    encode_grib2(
        float(lats[0]), float(lons[0]), float(r["target_res_lat_deg"]), float(r["target_res_lon_deg"]),
        ni, nj, au, av, avu, avu, ah,
        int(f"{run:%Y%m%d}"), run.hour * 100, out,
        bits_per_value=r.get("bits_per_value"),
    )
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/norkyst_gribs")
    parser.add_argument("--regions", default=None,
                        help="Comma-separated region ids (default: all)")
    parser.add_argument("--forecast-hours", type=int, default=None,
                        help="Require and write exactly this horizon (testing only)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    selected = args.regions.split(",") if args.regions else list(REGIONS)
    unknown = [rid for rid in selected if rid not in REGIONS]
    if unknown:
        sys.exit(f"Unknown region id(s): {', '.join(unknown)}")

    try:
        ds = retry(_open, label="open dataset")
        fh = args.forecast_hours
        want = fh if fh is not None else max(REGIONS[rid]["forecast_hours"] for rid in selected)
        run, slots = select_hours(ds, want, want)
        print(f"Run {run:%Y-%m-%d %HZ}, lead hours {slots[0][1]}..{slots[-1][1]} "
              f"({len(slots)} steps)", file=sys.stderr)
        lat = retry(lambda: np.ma.filled(ds["lat"][:].astype(np.float64), np.nan), label="lat")
        lon = retry(lambda: np.ma.filled(ds["lon"][:].astype(np.float64), np.nan), label="lon")
    except Exception as e:
        print(f"Cannot read the dataset: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    generated, failed = 0, []
    try:
        for rid in selected:
            r = REGIONS[rid]
            print(f"\n=== {rid} ({r['name']}) ===", file=sys.stderr)
            h = fh if fh is not None else r["forecast_hours"]
            try:
                path = process_region(rid, r, ds, lat, lon, run,
                                      [s for s in slots if s[1] <= h], args.output_dir)
            except Exception as e:
                print(f"  {rid}: ERROR — {type(e).__name__}: {e!r}", file=sys.stderr)
                traceback.print_exc()
                failed.append(rid)
                continue
            print(f"  {os.path.basename(path)}: {os.path.getsize(path)} bytes", file=sys.stderr)
            generated += 1
    finally:
        ds.close()

    # Same exit codes as the other generators: 1 = nothing produced,
    # 2 = some regions failed (the workflow still uploads the others).
    if generated == 0:
        print("\nNo GRIB2 files generated — failing.", file=sys.stderr)
        sys.exit(1)
    if failed:
        print(f"\n{len(failed)} region(s) failed: {', '.join(failed)} — failing.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
