#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
GRIB2 generator for NOAA NOS OFS surface currents.

Fetches surface current data from the NOAA NOS THREDDS DAP server,
extracts surface u/v, subsamples to ~0.05 deg, and encodes as a
multi-message GRIB2 file per model (grid_simple packing, discipline 10
"oceanographic" / category 1 "currents", params 2=u/3=v).

GRIB2 files go to --output-dir. This script only writes files — it does
NOT upload them or touch the catalog. Uploading to the rolling GitHub
Release and updating tide-current-index.json are handled elsewhere (see
.github/workflows/generate-ofs-gribs.yml and sources/nos_ofs.py, which
owns the static catalog entry and is run weekly by generate_index.py).

Usage:
    python generate_ofs_gribs.py [--output-dir DIR] [--models m1,m2] [--max-hour N]

Requires: numpy, xarray, netCDF4, eccodes
(not installed by the weekly index generator — this script runs in a
dedicated daily workflow that supplies these deps.)
"""

import argparse
import os
import sys
import urllib.request
from datetime import date, timedelta

import numpy as np
import xarray as xr

# sources/nos_ofs.py is the single source of truth for the NOS OFS model
# metadata (MODELS) and the shared polygon/bbox helpers — it is stdlib-only
# and lives in the sibling sources/ directory alongside the other weekly
# collectors, so it is reached via sys.path rather than a package import.
_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)
import nos_ofs  # noqa: E402

MODELS = nos_ofs.MODELS

THREDDS_BASE = "https://opendap.co-ops.nos.noaa.gov/thredds"
THREDDS_DODS = f"{THREDDS_BASE}/dodsC"
THREDDS_CAT = f"{THREDDS_BASE}/catalog"
DEFAULT_RES_DEG = 0.05
# Sentinel written at masked/non-finite grid points. eccodes excludes any
# point equal to this value from the packed data section and builds the
# GRIB2 bitmap accordingly (bitmapPresent=1) — the plugin's parser then
# decodes those points as NaN via the bitmap, not as a fake 0.0 current.
MISSING_VALUE = -9999.0
# Max deviation from perfectly uniform spacing we tolerate in a subsampled
# coordinate axis, as a fraction of the computed increment, before treating
# the grid as non-uniform and aborting that model.
UNIFORMITY_TOLERANCE_FRACTION = 0.03


def _dods_url(model_id: str, upper: str, cycle: str, d: date, hour: int) -> str:
    tag = f"f{hour:03d}" if hour >= 0 else f"n{abs(hour):03d}"
    ds = f"{model_id}.t{cycle}z.{d.strftime('%Y%m%d')}.regulargrid.{tag}.nc"
    return f"{THREDDS_DODS}/NOAA/{upper}/MODELS/{d.strftime('%Y/%m/%d')}/{ds}"


def _extract_grid(ds: xr.Dataset) -> tuple:
    """Lat/lon/mask — identical across every forecast-hour file for a given
    model/cycle, so callers should fetch this only once per model (see
    process_model) rather than re-downloading it with every hour's file."""
    lat = ds["Latitude"].values.astype(np.float64)
    lon = ds["Longitude"].values.astype(np.float64)
    if "mask" in ds:
        mask = ds["mask"].values.astype(bool)
    else:
        mask = np.ones(lat.shape, dtype=bool)
    return lat, lon, mask


def _extract_uv(ds: xr.Dataset) -> tuple:
    u = ds["u_eastward"].isel(time=0, Depth=0).values.astype(np.float64)
    v = ds["v_northward"].isel(time=0, Depth=0).values.astype(np.float64)
    return u, v


def _subsample_step(lat: np.ndarray, res: float) -> int:
    ny = lat.shape[0]
    lat_span = abs(float(lat[-1, 0]) - float(lat[0, 0]))
    if lat_span < 1e-10 or ny < 2:
        return 1
    return max(1, int(round(res / lat_span * (ny - 1))))


def _check_uniform(coords_1d: np.ndarray, inc: float, axis_name: str, model_id: str) -> None:
    """Sanity check that a subsampled coordinate axis is (close to)
    uniformly spaced. Encoding a non-uniform axis as a regular_ll grid with
    a single increment would silently mislocate every sample past the first
    non-uniform gap, so we abort the model with a clear error instead."""
    if len(coords_1d) < 2:
        return
    diffs = np.diff(coords_1d)
    max_dev = float(np.max(np.abs(diffs - inc)))
    tol = max(abs(inc) * UNIFORMITY_TOLERANCE_FRACTION, 1e-9)
    if max_dev > tol:
        raise ValueError(
            f"{model_id}: {axis_name} spacing not uniform after subsampling "
            f"(max deviation {max_dev:.6f} deg exceeds tolerance {tol:.6f} deg) — aborting"
        )


def _write_msg(f, data, valid, lat0, lon0, inc_lat, inc_lon, ni, nj, date_val, time_val, step, is_u):
    import eccodes as ec

    gid = ec.codes_grib_new_from_samples("GRIB2")
    try:
        ec.codes_set(gid, "centre", 7)  # NCEP/US (NOAA-derived data)
        ec.codes_set(gid, "dataDate", date_val)
        ec.codes_set(gid, "dataTime", time_val)
        ec.codes_set(gid, "stepRange", step)
        ec.codes_set(gid, "stepType", "instant")
        ec.codes_set(gid, "typeOfLevel", "surface")
        ec.codes_set(gid, "level", 0)
        ec.codes_set(gid, "gridType", "regular_ll")
        ec.codes_set(gid, "Ni", ni)
        ec.codes_set(gid, "Nj", nj)
        ec.codes_set(gid, "latitudeOfFirstGridPointInDegrees", lat0)
        ec.codes_set(gid, "longitudeOfFirstGridPointInDegrees", lon0)
        # Last grid point is derived from first point + (N-1)*increment so
        # it is exactly consistent with Ni/Nj and the increments, rather
        # than an independently-measured corner that could disagree by a
        # fraction of a cell with the declared increment.
        ec.codes_set(gid, "latitudeOfLastGridPointInDegrees", lat0 + (nj - 1) * inc_lat)
        ec.codes_set(gid, "longitudeOfLastGridPointInDegrees", lon0 + (ni - 1) * inc_lon)
        ec.codes_set(gid, "iDirectionIncrementInDegrees", abs(inc_lon))
        ec.codes_set(gid, "jDirectionIncrementInDegrees", abs(inc_lat))
        # The regulargrid source stores latitude ascending (row 0 = south);
        # process_model flips arrays to ascending before we get here, so
        # this is always south->north / west->east scanning.
        ec.codes_set(gid, "iScansNegatively", 0)
        ec.codes_set(gid, "jScansPositively", 1)
        ec.codes_set(gid, "discipline", 10)
        ec.codes_set(gid, "parameterCategory", 1)
        # 2 = u-component (eastward), 3 = v-component (northward) — the
        # plugin (src/gribcurrents.ts isCurrentField/buildSlots) pairs
        # strictly on params (2,3) or (0,1); u=1/v=2 would be silently
        # misread as something else entirely.
        ec.codes_set(gid, "parameterNumber", 2 if is_u else 3)
        ec.codes_set(gid, "packingType", "grid_simple")
        ec.codes_set(gid, "bitmapPresent", 1)
        ec.codes_set(gid, "missingValue", MISSING_VALUE)
        out = data.astype(np.float64)
        out[~valid] = MISSING_VALUE
        ec.codes_set_values(gid, out.ravel())
        ec.codes_write(gid, f)
    finally:
        ec.codes_release(gid)


def _encode_grib2(lat0, lon0, inc_lat, inc_lon, ni, nj, all_u, all_v, all_mask, hours, bd, bt, path):
    with open(path, "wb") as f:
        for h, u, v, mask in zip(hours, all_u, all_v, all_mask):
            # Land AND non-finite values (even at points the mask calls
            # wet) are both treated as missing — a masked-wet point with a
            # NaN/Inf fill value should not be encoded as a real sample.
            valid_u = mask & np.isfinite(u)
            valid_v = mask & np.isfinite(v)
            _write_msg(f, u, valid_u, lat0, lon0, inc_lat, inc_lon, ni, nj, bd, bt, h, True)
            _write_msg(f, v, valid_v, lat0, lon0, inc_lat, inc_lon, ni, nj, bd, bt, h, False)


def process_model(mid, m, d, ch, out_dir, max_hour_override=None):
    upper = mid.upper()
    step = m["forecast_step_hours"]
    max_h = max_hour_override if max_hour_override is not None else m["forecast_hours"]
    first_h = m.get("first_forecast_hour", 0)
    hours = list(range(first_h, max_h + 1, step))
    out = os.path.join(out_dir, f"{mid}_currents.grb2")

    lat_s = lon_s = mask_s = None
    sub_step = None
    flip_j = flip_i = False
    inc_lat = inc_lon = None

    au, av, am, ah = [], [], [], []

    for h in hours:
        url = _dods_url(mid, upper, ch, d, h)
        try:
            ds = xr.open_dataset(url, engine="netcdf4")
        except Exception as e:
            print(f"    SKIP +{h:03d}h: {e}", file=sys.stderr)
            continue
        try:
            if lat_s is None:
                # Grid geometry is identical across every hour of a cycle —
                # fetched exactly once, not re-downloaded per file.
                lat, lon, mask = _extract_grid(ds)
                ny = lat.shape[0]
                nr = abs(lat[-1, 0] - lat[0, 0]) / max(ny - 1, 1)
                res_u = max(DEFAULT_RES_DEG, nr * 4)
                sub_step = _subsample_step(lat, res_u)
                lat_s = lat[::sub_step, ::sub_step]
                lon_s = lon[::sub_step, ::sub_step]
                if lat_s.shape[0] * lat_s.shape[1] > 200_000:
                    res_u = DEFAULT_RES_DEG
                    sub_step = _subsample_step(lat, res_u)
                    lat_s = lat[::sub_step, ::sub_step]
                    lon_s = lon[::sub_step, ::sub_step]
                mask_s = mask[::sub_step, ::sub_step]

                # Defensive: encode as ascending south->north / west->east
                # regardless of the source's storage order (regulargrid
                # files verified ascending in practice, but don't assume it
                # forever — flip the arrays rather than branching flags).
                flip_j = lat_s.shape[0] > 1 and lat_s[-1, 0] < lat_s[0, 0]
                if flip_j:
                    lat_s = lat_s[::-1, :]
                    lon_s = lon_s[::-1, :]
                    mask_s = mask_s[::-1, :]
                flip_i = lat_s.shape[1] > 1 and lon_s[0, -1] < lon_s[0, 0]
                if flip_i:
                    lat_s = lat_s[:, ::-1]
                    lon_s = lon_s[:, ::-1]
                    mask_s = mask_s[:, ::-1]

                nj, ni = lat_s.shape
                inc_lat = (lat_s[-1, 0] - lat_s[0, 0]) / (nj - 1) if nj > 1 else 0.0
                inc_lon = (lon_s[0, -1] - lon_s[0, 0]) / (ni - 1) if ni > 1 else 0.0
                _check_uniform(lat_s[:, 0], inc_lat, "latitude", mid)
                _check_uniform(lon_s[0, :], inc_lon, "longitude", mid)

            u, v = _extract_uv(ds)
        except Exception as e:
            print(f"    SKIP +{h:03d}h: {e}", file=sys.stderr)
            continue
        finally:
            ds.close()

        us = u[::sub_step, ::sub_step]
        vs = v[::sub_step, ::sub_step]
        if flip_j:
            us = us[::-1, :]
            vs = vs[::-1, :]
        if flip_i:
            us = us[:, ::-1]
            vs = vs[:, ::-1]
        au.append(us)
        av.append(vs)
        am.append(mask_s)
        ah.append(h)

    if not au:
        return None
    bd = int(d.strftime("%Y%m%d"))
    bt = int(ch) * 100
    nj, ni = lat_s.shape
    _encode_grib2(
        float(lat_s[0, 0]), float(lon_s[0, 0]), inc_lat, inc_lon, ni, nj,
        au, av, am, ah, bd, bt, out,
    )
    return out


def _find_latest_cycle(upper, cycles, d):
    import re
    url = f"{THREDDS_CAT}/NOAA/{upper}/MODELS/{d.strftime('%Y/%m/%d')}/catalog.xml"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode()
        # Iterate newest-first so a late-day workflow_dispatch packages the
        # most recent cycle instead of always the earliest one available.
        for c in reversed(cycles):
            if re.search(rf'\.t{c}z\..*regulargrid', content):
                return c
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/ofs_gribs")
    parser.add_argument(
        "--models", default=None,
        help="Comma-separated model ids to process (default: all models)",
    )
    parser.add_argument(
        "--max-hour", type=int, default=None,
        help="Override the max forecast hour per model (testing only)",
    )
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    today = date.today()

    selected = args.models.split(",") if args.models else list(MODELS.keys())

    # Processed serially — netCDF4/xarray DAP reads are not thread-safe and
    # a ThreadPoolExecutor here is a known segfault risk. This is a daily
    # batch job with no latency requirement, so there's no reason to risk it.
    generated = 0
    for mid in selected:
        m = MODELS.get(mid)
        if m is None:
            print(f"Unknown model id: {mid}", file=sys.stderr)
            continue

        d = ch = None
        for offset in range(3):
            dd = today - timedelta(days=offset)
            c = _find_latest_cycle(mid.upper(), m["cycles"], dd)
            if c:
                d, ch = dd, c
                break

        if ch is None:
            print(f"\n=== {mid} — No data available (last 3 days) ===", file=sys.stderr)
            continue

        print(f"\n=== {mid} ({m['name']}) — cycle {d} {ch}Z ===", file=sys.stderr)
        try:
            path = process_model(mid, m, d, ch, args.output_dir, args.max_hour)
        except Exception as e:
            print(f"  {mid}: ERROR — {e}", file=sys.stderr)
            path = None

        if not path or not os.path.exists(path):
            print(f"  {mid}: Failed to generate GRIB2", file=sys.stderr)
            continue

        sz = os.path.getsize(path)
        print(f"  {os.path.basename(path)}: {sz} bytes", file=sys.stderr)
        generated += 1

    # Partial failures are tolerated (individual OFS models lag or skip
    # cycles routinely), but producing NOTHING means the pipeline itself is
    # broken (THREDDS outage, schema change) — fail the run so CI goes red
    # instead of silently leaving the release assets to go stale.
    if generated == 0:
        print("\nNo GRIB2 files generated for any model — failing.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
