#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Shared GRIB2 encoding tail for the daily current-forecast generators
(generate_ofs_gribs.py, generate_ibi_gribs.py). Extracted once a second
generator needed the identical output side: grid_simple packing, discipline
10 "oceanographic" / category 1 "currents", params 2=u/3=v, bitmap-encoded
missing values, regular_ll south->north / west->east scanning. Each caller
owns everything upstream of this (fetching, pooling/regridding, per-model
quirks) — this module only knows how to write already-regular-grid u/v
arrays as GRIB2 messages.

Requires: numpy, eccodes.
"""

import numpy as np

# Sentinel written at masked/non-finite grid points. eccodes excludes any
# point equal to this value from the packed data section and builds the
# GRIB2 bitmap accordingly (bitmapPresent=1) — the plugin's parser then
# decodes those points as NaN via the bitmap, not as a fake 0.0 current.
MISSING_VALUE = -9999.0
# Max deviation from perfectly uniform spacing tolerated in a coordinate
# axis, as a fraction of the computed increment, before treating the grid
# as non-uniform and aborting.
UNIFORMITY_TOLERANCE_FRACTION = 0.03


def check_uniform(coords_1d: np.ndarray, inc: float, axis_name: str, label: str) -> None:
    """Sanity check that a coordinate axis is (close to) uniformly spaced.
    Encoding a non-uniform axis as a regular_ll grid with a single
    increment would silently mislocate every sample past the first
    non-uniform gap, so we abort with a clear error instead."""
    if len(coords_1d) < 2:
        return
    diffs = np.diff(coords_1d)
    max_dev = float(np.max(np.abs(diffs - inc)))
    tol = max(abs(inc) * UNIFORMITY_TOLERANCE_FRACTION, 1e-9)
    if max_dev > tol:
        raise ValueError(
            f"{label}: {axis_name} spacing not uniform "
            f"(max deviation {max_dev:.6f} deg exceeds tolerance {tol:.6f} deg) — aborting"
        )


def write_msg(f, data, valid, lat0, lon0, inc_lat, inc_lon, ni, nj, date_val, time_val, step, is_u):
    import eccodes as ec

    gid = ec.codes_grib_new_from_samples("GRIB2")
    try:
        ec.codes_set(gid, "centre", 7)  # NCEP/US (NOAA-derived data); also used for Copernicus-derived output
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
        # Callers are responsible for flipping their arrays to ascending
        # (south->north / west->east) before calling in — this is always
        # that scan order.
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


def encode_grib2(lat0, lon0, inc_lat, inc_lon, ni, nj, all_u, all_v, all_valid_u, all_valid_v, hours, bd, bt, path):
    with open(path, "wb") as f:
        for h, u, v, valid_u, valid_v in zip(hours, all_u, all_v, all_valid_u, all_valid_v):
            write_msg(f, u, valid_u, lat0, lon0, inc_lat, inc_lon, ni, nj, bd, bt, h, True)
            write_msg(f, v, valid_v, lat0, lon0, inc_lat, inc_lon, ni, nj, bd, bt, h, False)
