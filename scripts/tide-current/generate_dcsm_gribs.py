#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
GRIB2 generator for Rijkswaterstaat DCSM-FM 100 m forecast currents
(Dutch Wadden Sea + Delta), from the open NOOS Matroos server.

Pipeline: pick a run -> download the whole-shelf NetCDF -> crop each region
-> regrid the curvilinear source onto a regular lat/lon grid -> write one
multi-message GRIB2 per region with the shared grib2_writer tail (same wire
format as the CMEMS/CIOPS/OFS files) -> delete the raw file.

Source quirks (checked on real runs, Nov 2026):
  - Matroos cannot subset 2-D maps on the server: one run is a single
    ~4.5 GB NetCDF covering the NW European shelf (ZUNO v4 curvilinear
    grid, dims time x row x col = 307 x 638 x 1449). At ~4 MB/s that is
    ~18 min; it is streamed to disk with curl and read with netCDF4 slices.
  - `time` has 307 slots at 10-min steps from analysis -3 h. The 00Z run of
    21 Nov 2026 filled all of them (to +48 h); runs seen earlier filled only
    ~169 (to +25 h) and padded the rest with time = 0 (not masked). So the
    horizon varies: every full hour from the analysis up to
    max_forecast_hours is kept as long as the hours are contiguous, and a run
    that does not reach forecast_hours (the guaranteed minimum) is rejected.
  - lat/lon (row, col) hold fill values outside the model domain.
  - Land cells are masked (the mask does not change in time). Dry cells are
    NOT masked: their velocity is exactly 0, and stays 0 here (a dry flat
    has no current; it is not missing data).
  - velu/velv are depth-averaged east/north components in m/s.

Regridding: Delaunay triangulation of the wet source cells, keeping only
triangles whose three corners are neighbours in the source grid's own
(row, col) indexing. A triangle that spans a masked (land) cell, a dam or
an island therefore never exists, so linear interpolation cannot smear
water across land. Target points not inside any kept triangle (the outer
half-cell along coasts, channels one cell wide) take the nearest wet
source cell if it is within half a target-cell diagonal; everything else
is missing (GRIB2 bitmap). The interpolation weights depend only on the
geometry, so they are built once per region as a sparse matrix and applied
to every hour.

Usage:
    python generate_dcsm_gribs.py [--output-dir DIR] [--regions r1,r2]
        [--cycles 00,12] [--analysis YYYYMMDDHHMM] [--raw-file FILE]
        [--keep-raw] [--forecast-hours N]

Requires: numpy, scipy, netCDF4, eccodes, curl.
"""

import argparse
import os
import subprocess
import sys
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone

import netCDF4
import numpy as np
from scipy.sparse import csr_matrix
from scipy.spatial import Delaunay, cKDTree

from grib2_writer import encode_grib2, retry

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)
import rws_dcsm  # noqa: E402

REGIONS = rws_dcsm.REGIONS

# Source cells this far outside a region box are still read, so the
# triangulation reaches the box edges.
CROP_MARGIN_DEG = 0.03
# A full run is ~4.5 GB (Nov 2026); anything much smaller is an error page or a
# truncated transfer.
MIN_RAW_BYTES = 500_000_000
DOWNLOAD_TIMEOUT_S = 75 * 60
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
KM_PER_DEG_LAT = 110.57


# ---------------------------------------------------------------------------
# Run selection and download
# ---------------------------------------------------------------------------

def list_runs(days_back: int = 3) -> list:
    now = datetime.now(timezone.utc)
    url = (f"{rws_dcsm.MATROOS_BASE}/get_anal_times.php?database={rws_dcsm.MATROOS_DATABASE}"
           f"&source={rws_dcsm.MATROOS_SOURCE}"
           f"&tstart={(now - timedelta(days=days_back)):%Y%m%d%H%M}&tstop={now:%Y%m%d%H%M}")
    with urllib.request.urlopen(url, timeout=120) as r:
        tokens = r.read().decode().split()
    return sorted(datetime.strptime(s, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                  for s in tokens if s.isdigit() and len(s) == 12)


def pick_run(cycles: list) -> datetime:
    runs = [t for t in retry(list_runs, label="list runs") if f"{t:%H}" in cycles and t.minute == 0]
    if not runs:
        raise RuntimeError(f"no {'/'.join(cycles)}Z run listed on Matroos in the last 3 days")
    return runs[-1]


def download_run(analysis: datetime, dest: str) -> None:
    url = (f"{rws_dcsm.MATROOS_BASE}/get_netcdf.php?database={rws_dcsm.MATROOS_DATABASE}"
           f"&source={rws_dcsm.MATROOS_SOURCE}&analysis={analysis:%Y%m%d%H%M}&timezone=GMT")
    print(f"  downloading {url}", file=sys.stderr)
    tmp = dest + ".part"
    t0 = datetime.now(timezone.utc)

    def _once():
        try:
            subprocess.run(["curl", "-fsS", "--retry", "2", "--retry-delay", "60",
                            "-m", str(DOWNLOAD_TIMEOUT_S), "-o", tmp, url], check=True)
            size = os.path.getsize(tmp)
            if size < MIN_RAW_BYTES:
                raise RuntimeError(f"download only {size} bytes, expected a multi-GB NetCDF")
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    retry(_once, label="download", attempts=3, delay=120)
    dt = (datetime.now(timezone.utc) - t0).total_seconds()
    size = os.path.getsize(dest)
    print(f"  downloaded {size / 1e9:.2f} GB in {dt / 60:.1f} min "
          f"({size / 1e6 / max(dt, 1):.1f} MB/s)", file=sys.stderr)


# ---------------------------------------------------------------------------
# Source file reading
# ---------------------------------------------------------------------------

def _minutes_since_epoch(var) -> np.ndarray:
    """Matroos stores times as 'minutes since 1970-01-01 00:00:00'; read the
    units anyway so a change shows up as an error, not as shifted data."""
    units = getattr(var, "units", "")
    vals = np.ma.filled(var[:], 0).astype(np.float64)
    if units.startswith("minutes since 1970-01-01"):
        return vals
    if units.startswith("seconds since 1970-01-01"):
        return vals / 60.0
    if units.startswith("hours since 1970-01-01"):
        return vals * 60.0
    raise RuntimeError(f"unexpected time units {units!r}")


def select_hours(ds, min_hours: int, max_hours: int) -> tuple:
    """Returns (analysis datetime, [(time index, lead hour), ...]) for the
    contiguous full-hour slots from the analysis up to +max_hours. Padding
    slots (time == 0) fall outside that window and drop out. Fails if the
    contiguous run does not reach +min_hours."""
    anal_min = float(_minutes_since_epoch(ds["analysis_time"])[0])
    analysis = EPOCH + timedelta(minutes=anal_min)
    t = _minutes_since_epoch(ds["time"])
    picked = {}
    for i, m in enumerate(t):
        lead = (m - anal_min) / 60.0
        if 0 <= lead <= max_hours + 1e-6 and abs(lead - round(lead)) < 1e-6:
            picked.setdefault(int(round(lead)), i)
    hours = sorted(picked)
    if not hours or hours[0] != 0:
        raise RuntimeError(f"no analysis-time slot in the file (analysis {analysis:%Y-%m-%d %H:%MZ})")
    # Keep the contiguous hours from 0; the guaranteed minimum must be there,
    # or a truncated run would be published while the notes promise +min_hours.
    last = 0
    while last + 1 in picked:
        last += 1
    if last < min_hours:
        missing = sorted(set(range(min_hours + 1)) - set(hours))
        raise RuntimeError(f"missing lead hours {missing} (need at least 0..{min_hours})")
    return analysis, [(picked[h], h) for h in range(last + 1)]


def read_crop(ds, bounds: dict) -> tuple:
    """Row/col slices of the source grid covering bounds (+ margin), and the
    lat/lon of that block (NaN where the grid is filled)."""
    lat = np.ma.filled(ds["lat"][:].astype(np.float64), np.nan)
    lon = np.ma.filled(ds["lon"][:].astype(np.float64), np.nan)
    m = CROP_MARGIN_DEG
    inside = ((lon >= bounds["min_lon"] - m) & (lon <= bounds["max_lon"] + m)
              & (lat >= bounds["min_lat"] - m) & (lat <= bounds["max_lat"] + m))
    r, c = np.where(inside)
    if r.size == 0:
        raise RuntimeError("region lies outside the model grid")
    rs, cs = slice(r.min(), r.max() + 1), slice(c.min(), c.max() + 1)
    return rs, cs, lat[rs, cs], lon[rs, cs]


# ---------------------------------------------------------------------------
# Regridding
# ---------------------------------------------------------------------------

def _project(lon, lat, lat0):
    """Local equirectangular km, good enough over a ~1 deg region."""
    kx = KM_PER_DEG_LAT * np.cos(np.radians(lat0))
    return np.column_stack([np.asarray(lon) * kx, np.asarray(lat) * KM_PER_DEG_LAT])


def build_regridder(lat2d, lon2d, wet, lats, lons):
    """Sparse (n_target x n_wet) interpolation matrix and the target-valid
    mask. See the module docstring for the method."""
    rows, cols = np.nonzero(wet)
    lat0 = float(np.mean(lats))
    src = _project(lon2d[wet], lat2d[wet], lat0)
    lon_g, lat_g = np.meshgrid(lons, lats)
    tgt = _project(lon_g.ravel(), lat_g.ravel(), lat0)
    n_t, n_s = tgt.shape[0], src.shape[0]

    tri = Delaunay(src)
    simp = tri.simplices
    r, c = rows[simp], cols[simp]
    # Keep a triangle only if its corners are grid neighbours: every pair
    # differs by at most one row and one column.
    ok = ((np.abs(r[:, [0, 0, 1]] - r[:, [1, 2, 2]]) <= 1).all(axis=1)
          & (np.abs(c[:, [0, 0, 1]] - c[:, [1, 2, 2]]) <= 1).all(axis=1))

    s = tri.find_simplex(tgt)
    in_tri = s >= 0
    in_tri[in_tri] = ok[s[in_tri]]
    idx_t = np.nonzero(in_tri)[0]
    st = s[idx_t]
    # Barycentric weights from scipy's stored affine transform.
    T = tri.transform[st]
    b = np.einsum("nij,nj->ni", T[:, :2, :], tgt[idx_t] - T[:, 2, :])
    w = np.column_stack([b, 1.0 - b.sum(axis=1)])
    w = np.clip(w, 0.0, 1.0)
    w /= w.sum(axis=1, keepdims=True)

    # Fallback: nearest wet cell within half a target-cell diagonal.
    dx = abs(lons[1] - lons[0]) * KM_PER_DEG_LAT * np.cos(np.radians(lat0))
    dy = abs(lats[1] - lats[0]) * KM_PER_DEG_LAT
    radius = 0.5 * float(np.hypot(dx, dy))
    rest = np.nonzero(~in_tri)[0]
    d, nn = cKDTree(src).query(tgt[rest], distance_upper_bound=radius)
    near = np.isfinite(d)
    idx_n = rest[near]

    mat = csr_matrix(
        (np.concatenate([w.ravel(), np.ones(idx_n.size)]),
         (np.concatenate([np.repeat(idx_t, 3), idx_n]),
          np.concatenate([simp[st].ravel(), nn[near]]))),
        shape=(n_t, n_s))
    valid = np.zeros(n_t, dtype=bool)
    valid[idx_t] = True
    valid[idx_n] = True
    stats = {"triangles_kept": float(ok.mean()), "interp": idx_t.size,
             "nearest": idx_n.size, "valid": int(valid.sum()), "total": n_t}
    return mat, valid.reshape(lat_g.shape), stats


def target_axes(bounds: dict, res_lat: float, res_lon: float) -> tuple:
    ni = int(round((bounds["max_lon"] - bounds["min_lon"]) / res_lon)) + 1
    nj = int(round((bounds["max_lat"] - bounds["min_lat"]) / res_lat)) + 1
    lons = bounds["min_lon"] + res_lon * np.arange(ni)
    lats = bounds["min_lat"] + res_lat * np.arange(nj)
    return lats, lons


# ---------------------------------------------------------------------------
# Per region
# ---------------------------------------------------------------------------

def process_region(rid, r, ds, analysis, slots, out_dir):
    out = os.path.join(out_dir, f"{rid}_currents.grb2")
    rs, cs, lat2d, lon2d = read_crop(ds, r["bounds"])
    lats, lons = target_axes(r["bounds"], r["target_res_lat_deg"], r["target_res_lon_deg"])
    nj, ni = len(lats), len(lons)

    # Land mask from the analysis-time slot; it is static, which is checked
    # below on every hour.
    u0 = ds["velu"][slots[0][0], rs, cs]
    wet = ~np.ma.getmaskarray(u0) & np.isfinite(lat2d) & np.isfinite(lon2d)
    mat, valid, st = build_regridder(lat2d, lon2d, wet, lats, lons)
    print(f"  {rid}: source block {lat2d.shape[0]}x{lat2d.shape[1]}, {int(wet.sum())} wet cells; "
          f"target {ni}x{nj}, {st['valid']} valid ({100 * st['valid'] / st['total']:.0f}%: "
          f"{st['interp']} interpolated, {st['nearest']} nearest); "
          f"{100 * st['triangles_kept']:.1f}% of triangles kept", file=sys.stderr)

    au, av, avu, ah = [], [], [], []
    for ti, lead in slots:
        u = ds["velu"][ti, rs, cs]
        v = ds["velv"][ti, rs, cs]
        if np.ma.getmaskarray(u)[wet].any() or np.ma.getmaskarray(v)[wet].any():
            raise RuntimeError(f"{rid}: a wet cell is masked at +{lead} h (land mask not static?)")
        uw = np.ma.filled(u, np.nan)[wet].astype(np.float64)
        vw = np.ma.filled(v, np.nan)[wet].astype(np.float64)
        if not (np.isfinite(uw).all() and np.isfinite(vw).all()):
            raise RuntimeError(f"{rid}: non-finite velocity in a wet cell at +{lead} h")
        au.append((mat @ uw).reshape(nj, ni))
        av.append((mat @ vw).reshape(nj, ni))
        avu.append(valid)
        ah.append(lead)

    encode_grib2(
        float(lats[0]), float(lons[0]), float(r["target_res_lat_deg"]), float(r["target_res_lon_deg"]),
        ni, nj, au, av, avu, avu, ah,
        int(f"{analysis:%Y%m%d}"), analysis.hour * 100, out,
        bits_per_value=r.get("bits_per_value"),
    )
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="/tmp/dcsm_gribs")
    parser.add_argument("--regions", default=None,
                        help="Comma-separated region ids (default: all)")
    parser.add_argument("--cycles", default=",".join(rws_dcsm.CYCLES),
                        help="Analysis hours to choose from, latest wins (default: %(default)s)")
    parser.add_argument("--analysis", default=None,
                        help="Use this run (YYYYMMDDHHMM) instead of the latest")
    parser.add_argument("--raw-file", default=None,
                        help="Use an already-downloaded Matroos NetCDF (not deleted)")
    parser.add_argument("--keep-raw", action="store_true",
                        help="Keep the downloaded NetCDF")
    parser.add_argument("--forecast-hours", type=int, default=None,
                        help="Require and write exactly this horizon (testing only)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    selected = args.regions.split(",") if args.regions else list(REGIONS)
    unknown = [rid for rid in selected if rid not in REGIONS]
    if unknown:
        sys.exit(f"Unknown region id(s): {', '.join(unknown)}")

    raw = args.raw_file
    delete_raw = False
    if raw is None:
        if args.analysis:
            analysis = datetime.strptime(args.analysis, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        else:
            analysis = pick_run([c.strip() for c in args.cycles.split(",")])
        print(f"Run: {analysis:%Y-%m-%d %H:%MZ}", file=sys.stderr)
        raw = os.path.join(args.output_dir, f"_dcsm_{analysis:%Y%m%d%H%M}.nc")
        delete_raw = not args.keep_raw
        try:
            download_run(analysis, raw)
        except Exception as e:
            print(f"Download failed: {type(e).__name__}: {e}", file=sys.stderr)
            sys.exit(1)

    generated, failed = 0, []
    try:
        with netCDF4.Dataset(raw) as ds:
            ds.set_auto_mask(True)
            fh = args.forecast_hours
            min_h = fh or max(REGIONS[rid]["forecast_hours"] for rid in selected)
            max_h = fh or max(REGIONS[rid].get("max_forecast_hours", REGIONS[rid]["forecast_hours"])
                              for rid in selected)
            analysis, slots = select_hours(ds, min_h, max_h)
            print(f"Analysis {analysis:%Y-%m-%d %H:%MZ}, lead hours "
                  f"{slots[0][1]}..{slots[-1][1]} ({len(slots)} steps)", file=sys.stderr)
            if args.analysis and f"{analysis:%Y%m%d%H%M}" != args.analysis:
                sys.exit(f"Server returned analysis {analysis:%Y%m%d%H%M}, wanted {args.analysis}")
            for rid in selected:
                r = REGIONS[rid]
                print(f"\n=== {rid} ({r['name']}) ===", file=sys.stderr)
                want = fh or r.get("max_forecast_hours", r["forecast_hours"])
                try:
                    path = process_region(rid, r, ds, analysis,
                                          [s for s in slots if s[1] <= want], args.output_dir)
                except Exception as e:
                    print(f"  {rid}: ERROR — {type(e).__name__}: {e!r}", file=sys.stderr)
                    traceback.print_exc()
                    failed.append(rid)
                    continue
                print(f"  {os.path.basename(path)}: {os.path.getsize(path)} bytes", file=sys.stderr)
                generated += 1
    finally:
        if delete_raw and os.path.exists(raw):
            os.remove(raw)

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
