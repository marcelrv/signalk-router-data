#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
GRIB2 generator for NOAA NOS OFS surface currents.

Fetches surface current data from the NOAA NOS THREDDS DAP server and
encodes it as a multi-message GRIB2 file per model (grid_simple packing,
discipline 10 "oceanographic" / category 1 "currents", params 2=u/3=v).
Two model shapes, two code paths, same GRIB2 writer at the tail:

  - process_model(): MODELS in sources/nos_ofs.py — models publishing
    NOAA's pre-regridded regulargrid netCDF product. Extracts surface u/v
    and wet-aware block-pools the already-regular native grid down to its
    own target_res_deg (fine inside estuaries/sounds, coarser offshore).
  - process_curvilinear_model(): CURVILINEAR_MODELS in sources/nos_ofs.py
    — NYOFS/SJROFS, which only publish a curvilinear (non-regular) native
    grid. Regrids surface u/v onto a regular lat/lon output grid via
    scipy.interpolate.griddata instead of block-pooling.

GRIB2 files go to --output-dir. This script only writes files — it does
NOT upload them or touch the catalog. Uploading to the rolling GitHub
Release and updating tide-current-index.json are handled elsewhere (see
.github/workflows/generate-ofs-gribs.yml and sources/nos_ofs.py, which
owns the static catalog entry and is run weekly by generate_index.py).

Usage:
    python generate_ofs_gribs.py [--output-dir DIR] [--models m1,m2] [--max-hour N]

Requires: numpy, xarray, netCDF4, eccodes, scipy
(not installed by the weekly index generator — this script runs in a
dedicated daily workflow that supplies these deps.)
"""

import argparse
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta

import numpy as np
import xarray as xr
from scipy.interpolate import griddata
from scipy.spatial import cKDTree

from grib2_writer import UNIFORMITY_TOLERANCE_FRACTION, check_uniform, encode_grib2

# sources/nos_ofs.py is the single source of truth for the NOS OFS model
# metadata (MODELS) and the shared polygon/bbox helpers — it is stdlib-only
# and lives in the sibling sources/ directory alongside the other weekly
# collectors, so it is reached via sys.path rather than a package import.
_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)
import nos_ofs  # noqa: E402

MODELS = nos_ofs.MODELS
# Curvilinear-grid models (NYOFS, SJROFS — issue #4 phase 2): fetched and
# regridded by process_curvilinear_model() below, not process_model() —
# see nos_ofs.py's module docstring for why these can't go through the
# regulargrid/block-pooling path the other MODELS entries use.
CURVILINEAR_MODELS = nos_ofs.CURVILINEAR_MODELS

THREDDS_BASE = "https://opendap.co-ops.nos.noaa.gov/thredds"
THREDDS_DODS = f"{THREDDS_BASE}/dodsC"
THREDDS_CAT = f"{THREDDS_BASE}/catalog"
# Fallback target resolution used only if a model in MODELS is missing
# target_res_deg — every current model sets it explicitly (see nos_ofs.py).
DEFAULT_RES_DEG = 0.05


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


def _native_spacing(lat: np.ndarray, lon: np.ndarray, mid: str) -> float:
    """Native grid spacing in degrees, verifying lat and lon spacing agree
    (block pooling below assumes a single uniform step works for both
    axes — see module docstring / spec: "native lat/lon spacing are equal
    in these files")."""
    ny, nx = lat.shape
    nr_lat = abs(float(lat[-1, 0]) - float(lat[0, 0])) / max(ny - 1, 1)
    nr_lon = abs(float(lon[0, -1]) - float(lon[0, 0])) / max(nx - 1, 1)
    if nr_lat > 0 and nr_lon > 0:
        rel_dev = abs(nr_lat - nr_lon) / max(nr_lat, nr_lon)
        if rel_dev > UNIFORMITY_TOLERANCE_FRACTION:
            raise ValueError(
                f"{mid}: native latitude spacing ({nr_lat:.6f} deg) and "
                f"longitude spacing ({nr_lon:.6f} deg) differ by "
                f"{rel_dev:.1%}, exceeding tolerance "
                f"{UNIFORMITY_TOLERANCE_FRACTION:.0%} — cannot pool with a "
                f"single step"
            )
    nr = nr_lat if nr_lat > 0 else nr_lon
    check_uniform(lat[:, 0], nr_lat, "native latitude", mid)
    check_uniform(lon[0, :], nr_lon, "native longitude", mid)
    return nr


def _pool_step(native_spacing_deg: float, target_res_deg: float) -> int:
    """Number of native grid cells per pooled output cell."""
    if native_spacing_deg <= 0:
        return 1
    return max(1, int(round(target_res_deg / native_spacing_deg)))


def _pool_mean(arr: np.ndarray, step: int) -> np.ndarray:
    """Plain (unweighted) step x step block-mean pool, truncating to whole
    blocks. Used for the lat/lon coordinate arrays, which are always
    defined (never masked) on these regulargrid files — the block mean of
    a uniform axis is exactly the block center."""
    ny, nx = arr.shape
    ny2, nx2 = (ny // step) * step, (nx // step) * step
    pny, pnx = ny2 // step, nx2 // step
    return arr[:ny2, :nx2].reshape(pny, step, pnx, step).mean(axis=(1, 3))


def _pool_weighted(arr: np.ndarray, valid: np.ndarray, step: int) -> tuple:
    """Wet-aware step x step block-mean pool: averages only the native
    points where `valid` is True (mask AND per-hour isfinite — see
    caller), truncating to whole blocks. A dry/land native cell in a
    mostly-wet block does not drag the mean toward zero, and a channel
    narrower than one output cell still contributes its wet points to that
    cell's mean instead of vanishing entirely.

    Returns (mean, wet_count) both shaped (ny // step, nx // step); mean is
    NaN wherever wet_count is 0 (the caller must treat those as missing —
    NaN is never itself a valid packed value)."""
    ny, nx = arr.shape
    ny2, nx2 = (ny // step) * step, (nx // step) * step
    pny, pnx = ny2 // step, nx2 // step
    arr_t = arr[:ny2, :nx2].reshape(pny, step, pnx, step)
    valid_t = valid[:ny2, :nx2].reshape(pny, step, pnx, step)
    count = valid_t.sum(axis=(1, 3))
    total = np.where(valid_t, arr_t, 0.0).sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    return mean, count


def process_model(mid, m, d, ch, out_dir, max_hour_override=None):
    upper = mid.upper()
    step = m["forecast_step_hours"]
    max_h = max_hour_override if max_hour_override is not None else m["forecast_hours"]
    first_h = m.get("first_forecast_hour", 0)
    hours = list(range(first_h, max_h + 1, step))
    out = os.path.join(out_dir, f"{mid}_currents.grb2")

    mask = None  # native-resolution, flip-applied land/sea mask
    pool_step = None
    flip_j = flip_i = False
    lat_p = lon_p = None
    inc_lat = inc_lon = None

    au, av, avu, avv, ah = [], [], [], [], []

    for h in hours:
        url = _dods_url(mid, upper, ch, d, h)
        try:
            ds = xr.open_dataset(url, engine="netcdf4")
        except Exception as e:
            print(f"    SKIP +{h:03d}h: {e}", file=sys.stderr)
            continue
        try:
            if mask is None:
                # Grid geometry is identical across every hour of a cycle —
                # fetched exactly once, not re-downloaded per file.
                lat, lon, mask = _extract_grid(ds)
                nr = _native_spacing(lat, lon, mid)

                # Defensive: encode as ascending south->north / west->east
                # regardless of the source's storage order (regulargrid
                # files verified ascending in practice, but don't assume it
                # forever). Flip the NATIVE arrays here, before pooling, so
                # block boundaries fall the same way regardless of source
                # order — flip AFTER pooling would pool mismatched blocks.
                flip_j = lat.shape[0] > 1 and lat[-1, 0] < lat[0, 0]
                if flip_j:
                    lat = lat[::-1, :]
                    lon = lon[::-1, :]
                    mask = mask[::-1, :]
                flip_i = lat.shape[1] > 1 and lon[0, -1] < lon[0, 0]
                if flip_i:
                    lat = lat[:, ::-1]
                    lon = lon[:, ::-1]
                    mask = mask[:, ::-1]

                target_res = m.get("target_res_deg", DEFAULT_RES_DEG)
                pool_step = _pool_step(nr, target_res)
                lat_p = _pool_mean(lat, pool_step)
                lon_p = _pool_mean(lon, pool_step)
                # Safety cap: if the pooled grid is still unreasonably
                # large (e.g. target_res_deg finer than expected relative
                # to native spacing), coarsen further.
                while lat_p.size > 200_000:
                    pool_step *= 2
                    lat_p = _pool_mean(lat, pool_step)
                    lon_p = _pool_mean(lon, pool_step)

                nj, ni = lat_p.shape
                inc_lat = (lat_p[-1, 0] - lat_p[0, 0]) / (nj - 1) if nj > 1 else 0.0
                inc_lon = (lon_p[0, -1] - lon_p[0, 0]) / (ni - 1) if ni > 1 else 0.0
                check_uniform(lat_p[:, 0], inc_lat, "latitude", mid)
                check_uniform(lon_p[0, :], inc_lon, "longitude", mid)

            u, v = _extract_uv(ds)
        except Exception as e:
            print(f"    SKIP +{h:03d}h: {e}", file=sys.stderr)
            continue
        finally:
            ds.close()

        if flip_j:
            u = u[::-1, :]
            v = v[::-1, :]
        if flip_i:
            u = u[:, ::-1]
            v = v[:, ::-1]

        # Land AND non-finite values (even at points the static mask calls
        # wet) are both treated as missing before pooling — a masked-wet
        # native point with a NaN/Inf fill value must not contaminate a
        # block's mean or falsely mark that block wet. Wetness is
        # per-hour: a block is wet in the output iff at least one native
        # point inside it is both mask-wet and finite at that hour.
        valid_u = mask & np.isfinite(u)
        valid_v = mask & np.isfinite(v)
        u_p, count_u = _pool_weighted(u, valid_u, pool_step)
        v_p, count_v = _pool_weighted(v, valid_v, pool_step)

        au.append(u_p)
        av.append(v_p)
        avu.append(count_u > 0)
        avv.append(count_v > 0)
        ah.append(h)

    if not au:
        return None
    bd = int(d.strftime("%Y%m%d"))
    bt = int(ch) * 100
    nj, ni = lat_p.shape
    encode_grib2(
        float(lat_p[0, 0]), float(lon_p[0, 0]), inc_lat, inc_lon, ni, nj,
        au, av, avu, avv, ah, bd, bt, out,
    )
    return out


def _dods_url_curvilinear(model_id: str, upper: str, cycle: str, d: date, kind: str) -> str:
    ds = f"{model_id}.t{cycle}z.{d.strftime('%Y%m%d')}.fields.{kind}.nc"
    return f"{THREDDS_DODS}/NOAA/{upper}/MODELS/{d.strftime('%Y/%m/%d')}/{ds}"


def _target_grid(bounds: dict, target_res_deg: float) -> tuple:
    """Regular south->north / west->east lat/lon meshgrid spanning bounds,
    matching the ascending-order convention _write_msg assumes for every
    other model. Returns (lon_g, lat_g) each shaped (nj, ni), plus the 1-D
    lats/lons axes."""
    ni = max(2, int(round((bounds["max_lon"] - bounds["min_lon"]) / target_res_deg)) + 1)
    nj = max(2, int(round((bounds["max_lat"] - bounds["min_lat"]) / target_res_deg)) + 1)
    lons = np.linspace(bounds["min_lon"], bounds["max_lon"], ni)
    lats = np.linspace(bounds["min_lat"], bounds["max_lat"], nj)
    lon_g, lat_g = np.meshgrid(lons, lats)
    return lon_g, lat_g, lats, lons


def process_curvilinear_model(mid, m, d, ch, out_dir):
    """Curvilinear-grid counterpart to process_model(): fetches the two
    fields.{nowcast,forecast}.nc files (each holding a whole time series,
    unlike the regulargrid path's one-file-per-hour), regrids the native
    curvilinear (2D lon/lat) surface u/v onto a regular lat/lon output grid
    via linear interpolation, and hands off to the same GRIB2 writer. See
    nos_ofs.py's module docstring for the live-verified quirks this
    depends on (fill-padding outside the wet mask, per-model time epoch,
    no rotation needed since u/v are already east/north)."""
    upper = mid.upper()
    sigma_idx = m["sigma_surface_index"]
    bounds = m["bounds"]
    target_res = m.get("target_res_deg", DEFAULT_RES_DEG)
    out = os.path.join(out_dir, f"{mid}_currents.grb2")

    lon_g, lat_g, lats, lons = _target_grid(bounds, target_res)
    nj, ni = lat_g.shape
    inc_lat = float(lats[1] - lats[0]) if nj > 1 else 0.0
    inc_lon = float(lons[1] - lons[0]) if ni > 1 else 0.0

    cycle_ref = datetime.combine(d, datetime.min.time()) + timedelta(hours=int(ch))
    bd = int(d.strftime("%Y%m%d"))
    bt = int(ch) * 100

    au, av, avu, avv, ah = [], [], [], [], []

    for kind in ("nowcast", "forecast"):
        url = _dods_url_curvilinear(mid, upper, ch, d, kind)
        try:
            ds = xr.open_dataset(url, engine="netcdf4")
        except Exception as e:
            print(f"    SKIP {kind}: {e}", file=sys.stderr)
            continue
        try:
            # lon/lat/mask carry no time dimension — fetched once per file.
            # mask==0 also covers the fill-padding cells outside the actual
            # curvilinear domain (verified live on SJROFS: every 0.0/0.0
            # padding point is mask==0), so filtering on mask alone keeps
            # bogus (0,0) points out of the interpolation input.
            lon2d = ds["lon"].values.astype(np.float64)
            lat2d = ds["lat"].values.astype(np.float64)
            wet = ds["mask"].values.astype(bool)
            times = ds["time"].values  # CF-decoded to datetime64[ns] by xarray
            u_all = ds["u"].isel(sigma=sigma_idx).values.astype(np.float64)
            v_all = ds["v"].isel(sigma=sigma_idx).values.astype(np.float64)
        except Exception as e:
            print(f"    SKIP {kind}: {e}", file=sys.stderr)
            continue
        finally:
            ds.close()

        # griddata's linear interpolation fills the ENTIRE convex hull of
        # the wet input points. For a domain where land sits between two
        # wet arms (e.g. NY Harbor wraps around Manhattan on both sides),
        # that hull bridges straight across the landmass and fabricates
        # plausible-looking current values over dry land — confirmed live
        # on NYOFS (a point in the middle of Manhattan, 2.5 km from the
        # nearest actual wet native point, still got a non-null regridded
        # value). Explicitly mask any output point whose nearest wet
        # native point is farther than a few native grid spacings away,
        # regardless of what griddata itself returned. Geometry-only, so
        # computed once per file (wet doesn't vary by timestep) rather
        # than once per hour.
        wet_points = np.column_stack([lon2d[wet], lat2d[wet]])
        wet_tree = cKDTree(wet_points)
        nn_dist, _ = wet_tree.query(wet_points, k=2)
        native_spacing_est = float(np.median(nn_dist[:, 1]))
        out_dist, _ = wet_tree.query(np.column_stack([lon_g.ravel(), lat_g.ravel()]))
        land_bridge = (out_dist > native_spacing_est * 2.5).reshape(lon_g.shape)

        for i in range(len(times)):
            t_py = times[i].astype("datetime64[s]").item()
            step = round((t_py - cycle_ref).total_seconds() / 3600)

            u = u_all[i]
            v = v_all[i]
            valid = wet & np.isfinite(u) & np.isfinite(v)
            if valid.sum() < 3:
                print(f"    SKIP {kind} {step:+d}h: fewer than 3 valid points", file=sys.stderr)
                continue

            points = np.column_stack([lon2d[valid], lat2d[valid]])
            try:
                u_reg = griddata(points, u[valid], (lon_g, lat_g), method="linear")
                v_reg = griddata(points, v[valid], (lon_g, lat_g), method="linear")
            except Exception as e:
                print(f"    SKIP {kind} {step:+d}h: regrid failed — {e}", file=sys.stderr)
                continue

            au.append(u_reg)
            av.append(v_reg)
            avu.append(np.isfinite(u_reg) & ~land_bridge)
            avv.append(np.isfinite(v_reg) & ~land_bridge)
            ah.append(step)

    if not au:
        return None
    encode_grib2(
        float(lats[0]), float(lons[0]), inc_lat, inc_lon, ni, nj,
        au, av, avu, avv, ah, bd, bt, out,
    )
    return out


def _find_latest_cycle(upper, cycles, d, pattern="regulargrid"):
    import re
    url = f"{THREDDS_CAT}/NOAA/{upper}/MODELS/{d.strftime('%Y/%m/%d')}/catalog.xml"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode()
        # Iterate newest-first so a late-day workflow_dispatch packages the
        # most recent cycle instead of always the earliest one available.
        for c in reversed(cycles):
            if re.search(rf'\.t{c}z\..*{pattern}', content):
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

    all_models = {**MODELS, **CURVILINEAR_MODELS}
    selected = args.models.split(",") if args.models else list(all_models.keys())

    # Processed serially — netCDF4/xarray DAP reads are not thread-safe and
    # a ThreadPoolExecutor here is a known segfault risk. This is a daily
    # batch job with no latency requirement, so there's no reason to risk it.
    generated = 0
    for mid in selected:
        is_curvilinear = mid in CURVILINEAR_MODELS
        m = all_models.get(mid)
        if m is None:
            print(f"Unknown model id: {mid}", file=sys.stderr)
            continue

        d = ch = None
        cycle_pattern = "fields\\.forecast" if is_curvilinear else "regulargrid"
        for offset in range(3):
            dd = today - timedelta(days=offset)
            c = _find_latest_cycle(mid.upper(), m["cycles"], dd, cycle_pattern)
            if c:
                d, ch = dd, c
                break

        if ch is None:
            print(f"\n=== {mid} — No data available (last 3 days) ===", file=sys.stderr)
            continue

        print(f"\n=== {mid} ({m['name']}) — cycle {d} {ch}Z ===", file=sys.stderr)
        try:
            if is_curvilinear:
                path = process_curvilinear_model(mid, m, d, ch, args.output_dir)
            else:
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
