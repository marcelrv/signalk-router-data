#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
GRIB2 generator for ECCC CIOPS surface currents (Canada).

Downloads the 0.5 m surface velocity files (SeaWaterVelocityX/Y) of the
latest complete CIOPS-East / CIOPS-West / CIOPS-Salish Sea run from the MSC
Datamart, cuts each into the regions in sources/eccc_ciops.py, and encodes
one multi-message GRIB2 file per region with the shared grib2_writer tail
(same wire format as the OFS/IBI generators — zero plugin changes).

Like IBI, the source is already a regular lat/lon grid with true
east/north components (see sources/eccc_ciops.py for the live checks), so
there is no regridding or vector rotation. Differences from IBI:

  - The Datamart only has whole-domain files, one file per variable per
    forecast hour (CIOPS-East is ~1.9 MB per file), so every hour is
    downloaded, cut into all of its domain's regions in memory and dropped.
  - Coordinate axes are rebuilt as first + k * increment from a linear fit
    of the file's own axis: the Salish Sea axes are stored as float32 and
    jitter by ~0.4% of a cell, and an end latitude has been seen masked in
    a CIOPS-East file. A fit-residual check (check_uniform's tolerance)
    still aborts on a genuinely non-uniform grid.
  - Optional wet-aware block pooling per region (pool_step), shared with
    the OFS generator via grib2_writer.pool_weighted.

GRIB2 files go to --output-dir. This script only writes files — uploading to
the rolling GitHub Release is done by .github/workflows/generate-ciops-gribs.yml
and the catalog entry comes from sources/eccc_ciops.py.

Usage:
    python generate_ciops_gribs.py [--output-dir DIR] [--regions r1,r2]
                                   [--forecast-hours N] [--run YYYYMMDDHH]

Requires: numpy, netCDF4, eccodes (not installed by the weekly index
generator — this script runs in its own workflow).
"""

import argparse
import os
import sys
import traceback
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import netCDF4
import numpy as np

from grib2_writer import UNIFORMITY_TOLERANCE_FRACTION, encode_grib2, pool_weighted, retry

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)
import eccc_ciops  # noqa: E402

REGIONS = eccc_ciops.REGIONS
DOMAINS = eccc_ciops.DOMAINS

USER_AGENT = "signalk-router-data-ciops/1.0"
HTTP_TIMEOUT = 60
DOWNLOAD_WORKERS = 8
# Velocities beyond this (m/s) are treated as missing — guards against an
# unmasked fill value slipping through (CIOPS uses 1e20).
MAX_ABS_VELOCITY = 20.0
# How many past days of runs to probe when looking for a complete run.
SEARCH_DAYS = 2


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})


def _exists(url: str) -> bool:
    try:
        with urllib.request.urlopen(_request(url, "HEAD"), timeout=HTTP_TIMEOUT) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(_request(url), timeout=HTTP_TIMEOUT) as r:
        return r.read()


def find_latest_run(domains: list, last_hour: int, now: datetime) -> tuple:
    """Newest (YYYYMMDD, HH) whose last forecast hour exists for every
    needed domain and both components. Files of a run appear in hour order,
    so the last hour being present means the run is complete."""
    for back in range(SEARCH_DAYS):
        d = (now - timedelta(days=back)).strftime("%Y%m%d")
        for hh in reversed(eccc_ciops.RUN_HOURS):
            run_time = datetime.strptime(d + hh, "%Y%m%d%H").replace(tzinfo=timezone.utc)
            if run_time > now:
                continue
            ok = all(
                retry(_exists, label=f"probe {dom} {d}T{hh}Z", attempts=3, delay=10,
                      url=eccc_ciops.file_url(dom, d, hh, comp, last_hour))
                for dom in domains for comp in ("X", "Y")
            )
            print(f"  run {d}T{hh}Z: {'complete' if ok else 'incomplete/missing'}", file=sys.stderr)
            if ok:
                return d, hh
    return None


def _regular_axis(values, name: str, label: str) -> tuple:
    """Fit first + k * inc to a 1-D coordinate axis (ignoring masked/NaN
    entries), returning (first, inc, n). Raises if the fit residual shows
    a genuinely non-uniform axis."""
    a = np.ma.filled(np.ma.masked_invalid(np.ma.asarray(values, dtype=np.float64)), np.nan)
    k = np.arange(a.size, dtype=np.float64)
    ok = np.isfinite(a)
    if ok.sum() < 2:
        raise ValueError(f"{label}: {name} axis has fewer than 2 valid values")
    inc, first = np.polyfit(k[ok], a[ok], 1)
    # Same tolerance as check_uniform, but measured against the fitted line
    # so a masked entry (a gap in a[ok]) doesn't read as a 2-cell step.
    max_dev = float(np.max(np.abs(a[ok] - (first + inc * k[ok]))))
    if max_dev > abs(inc) * UNIFORMITY_TOLERANCE_FRACTION:
        raise ValueError(f"{label}: {name} axis not uniform (max deviation {max_dev:.6f} deg "
                         f"from a regular {inc:.6f} deg axis) — aborting")
    return float(first), float(inc), a.size


def _region_slices(lat0, inc_lat, nlat, lon0, inc_lon, nlon, b: dict) -> tuple:
    lat = lat0 + inc_lat * np.arange(nlat)
    lon = lon0 + inc_lon * np.arange(nlon)
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    j = np.nonzero((lat >= b["min_lat"]) & (lat <= b["max_lat"]))[0]
    i = np.nonzero((lon >= b["min_lon"]) & (lon <= b["max_lon"]))[0]
    if j.size == 0 or i.size == 0:
        raise ValueError(f"region bounds {b} outside the domain grid")
    return slice(j[0], j[-1] + 1), slice(i[0], i[-1] + 1), float(lat[j[0]]), float(lon[i[0]])


def _read_component(data: bytes, var: str, label: str) -> tuple:
    ds = netCDF4.Dataset("inmemory.nc", memory=data)
    try:
        ds.set_auto_mask(True)
        lat = ds["latitude"][:]
        lon = ds["longitude"][:]
        arr = np.ma.filled(ds[var][0].astype(np.float64), np.nan)
    finally:
        ds.close()
    arr[~np.isfinite(arr) | (np.abs(arr) > MAX_ABS_VELOCITY)] = np.nan
    return lat, lon, arr


def process_domain(domain: str, rids: list, run_date: str, run_hour: str,
                   hours: list, out_dir: str) -> dict:
    """Download every hour of one domain and encode all of its regions.
    Returns {region_id: output path or None}."""
    label = f"{domain} {run_date}T{run_hour}Z"
    grid = {}       # domain-wide axis description, fixed by the first file
    slabs = {rid: {"u": [], "v": [], "valid": [], "hours": []} for rid in rids}
    meta = {}       # per-region (lat0, lon0, inc_lat, inc_lon, ni, nj)

    def fetch_hour(h):
        ux = retry(_fetch, label=f"{label} X+{h:03d}",
                   url=eccc_ciops.file_url(domain, run_date, run_hour, "X", h))
        vy = retry(_fetch, label=f"{label} Y+{h:03d}",
                   url=eccc_ciops.file_url(domain, run_date, run_hour, "Y", h))
        return h, ux, vy

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        # map() yields in submission order, so hours stay ascending while
        # downloads run ahead in parallel; each pair is dropped once cut.
        for h, xb, yb in pool.map(fetch_hour, hours):
            lat, lon, u = _read_component(xb, "vozocrtx", label)
            lat_v, lon_v, v = _read_component(yb, "vomecrty", label)
            del xb, yb
            if not grid:
                lat0, inc_lat, nlat = _regular_axis(lat, "latitude", label)
                lon0, inc_lon, nlon = _regular_axis(lon, "longitude", label)
                if inc_lat <= 0 or inc_lon <= 0:
                    raise ValueError(f"{label}: expected ascending lat/lon axes")
                grid.update(lat0=lat0, inc_lat=inc_lat, nlat=nlat,
                            lon0=lon0, inc_lon=inc_lon, nlon=nlon)
                for rid in rids:
                    js, is_, rlat0, rlon0 = _region_slices(
                        lat0, inc_lat, nlat, lon0, inc_lon, nlon, REGIONS[rid]["bounds"])
                    meta[rid] = (js, is_, rlat0, rlon0)
            if u.shape != (grid["nlat"], grid["nlon"]) or v.shape != u.shape \
                    or len(lat_v) != grid["nlat"] or len(lon_v) != grid["nlon"]:
                raise ValueError(f"{label}: grid shape changed at +{h:03d}h")

            valid = np.isfinite(u) & np.isfinite(v)
            for rid in rids:
                js, is_, _, _ = meta[rid]
                step = REGIONS[rid].get("pool_step", 1)
                ru, rv, rvalid = u[js, is_], v[js, is_], valid[js, is_]
                if step > 1:
                    ru, cnt = pool_weighted(ru, rvalid, step)
                    rv, _ = pool_weighted(rv, rvalid, step)
                    rvalid = cnt > 0
                s = slabs[rid]
                s["u"].append(ru)
                s["v"].append(rv)
                s["valid"].append(rvalid)
                s["hours"].append(h)

    bd = int(run_date)
    bt = int(run_hour) * 100
    results = {}
    for rid in rids:
        s = slabs[rid]
        js, is_, rlat0, rlon0 = meta[rid]
        step = REGIONS[rid].get("pool_step", 1)
        inc_lat = grid["inc_lat"] * step
        inc_lon = grid["inc_lon"] * step
        # A pooled cell's centre sits (step-1)/2 native cells in from its
        # first native cell.
        lat0 = rlat0 + grid["inc_lat"] * (step - 1) / 2.0
        lon0 = rlon0 + grid["inc_lon"] * (step - 1) / 2.0
        nj, ni = s["u"][0].shape
        out = os.path.join(out_dir, f"{rid}_currents.grb2")
        # Round the grid geometry to what GRIB2 stores (micro-degrees) so the
        # declared last grid point is consistent with Ni/Nj x increment.
        encode_grib2(
            round(lat0, 6), round(lon0, 6), round(inc_lat, 6), round(inc_lon, 6), ni, nj,
            s["u"], s["v"], s["valid"], s["valid"], s["hours"], bd, bt, out,
        )
        wet = int(s["valid"][0].sum())
        print(f"  {rid}: {ni}x{nj} cells, {wet} wet, {len(s['hours'])} steps, "
              f"{os.path.getsize(out)} bytes", file=sys.stderr)
        results[rid] = out
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/ciops_gribs")
    parser.add_argument("--regions", default=None,
                        help="Comma-separated region ids to process (default: all regions)")
    parser.add_argument("--forecast-hours", type=int, default=None,
                        help="Override the forecast horizon for every region (testing only)")
    parser.add_argument("--run", default=None,
                        help="Use this run (YYYYMMDDHH) instead of the latest complete one")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    selected = args.regions.split(",") if args.regions else list(REGIONS.keys())
    unknown = [r for r in selected if r not in REGIONS]
    for r in unknown:
        print(f"Unknown region id: {r}", file=sys.stderr)
    selected = [r for r in selected if r in REGIONS]
    if not selected:
        sys.exit(1)

    by_domain = {}
    for rid in selected:
        by_domain.setdefault(REGIONS[rid]["domain"], []).append(rid)
    max_hours = max(args.forecast_hours or REGIONS[r]["forecast_hours"] for r in selected)

    if args.run:
        run_date, run_hour = args.run[:8], args.run[8:10]
    else:
        found = find_latest_run(sorted(by_domain), max_hours, datetime.now(timezone.utc))
        if not found:
            print("No complete CIOPS run found on the Datamart — failing.", file=sys.stderr)
            sys.exit(1)
        run_date, run_hour = found
    print(f"Using CIOPS run {run_date}T{run_hour}Z", file=sys.stderr)

    generated, failed = 0, list(unknown)
    for domain, rids in by_domain.items():
        hours = list(range(1, max(args.forecast_hours or REGIONS[r]["forecast_hours"]
                                  for r in rids) + 1))
        print(f"\n=== {domain}: {', '.join(rids)} ({len(hours)} h) ===", file=sys.stderr)
        try:
            results = process_domain(domain, rids, run_date, run_hour, hours, args.output_dir)
        except Exception as e:
            print(f"  {domain}: ERROR — {type(e).__name__}: {e!r}", file=sys.stderr)
            traceback.print_exc()
            results = {}
        for rid in rids:
            path = results.get(rid)
            if path and os.path.exists(path):
                generated += 1
            else:
                print(f"  {rid}: Failed to generate GRIB2", file=sys.stderr)
                failed.append(rid)

    # Same exit-code contract as generate_ibi_gribs.py: 1 = nothing produced,
    # 2 = some regions failed (the workflow still uploads the others).
    if generated == 0:
        print("\nNo GRIB2 files generated for any region — failing.", file=sys.stderr)
        sys.exit(1)
    if failed:
        print(f"\n{len(failed)} region(s) failed to generate: {', '.join(failed)} — failing.",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
