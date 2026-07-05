#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""
Download one representative RTOFS GRIB2 file per region and extract its
true geographic bounds via gdalinfo.

Outputs a JSON object mapping region_id → boundary_geometry that can be
consumed by the NOAA RTOFS collector.

Usage:
    python3 inspect_rtofs_regions.py [--output regions.json]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

RTOFS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod"
REGIONS = [
    {"region_id": "west_atl"},
    {"region_id": "west_conus"},
    {"region_id": "gulf_alaska"},
    {"region_id": "alaska"},
    {"region_id": "bering"},
    {"region_id": "arctic"},
    {"region_id": "hudson_baffin"},
    {"region_id": "trop_paci_lowres"},
    {"region_id": "honolulu"},
    {"region_id": "guam"},
    {"region_id": "samoa"},
]


def find_latest_cycle() -> str | None:
    """Find the most recent available RTOFS cycle."""
    from datetime import date, timedelta

    today = date.today()
    for offset in range(5):
        d = today - timedelta(days=offset)
        ymd = d.strftime("%Y%m%d")
        url = f"{RTOFS_BASE}/rtofs.{ymd}/rtofs_glo.t00z.f024_west_atl_std.grb2"
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=15):
                return ymd
        except Exception:
            continue
    return None


def get_grib2_bounds(grib2_path: str) -> dict:
    """Use gdalinfo to extract corner coordinates from a GRIB2 file."""
    result = subprocess.run(
        ["gdalinfo", "-json", grib2_path],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gdalinfo failed: {result.stderr}")
    info = json.loads(result.stdout)

    # gdalinfo gives cornerCoordinates for the first band
    corners = info.get("cornerCoordinates")
    if not corners:
        raise RuntimeError("No cornerCoordinates in gdalinfo output")

    # corners = {upperLeft, lowerLeft, upperRight, lowerRight}
    ul = corners["upperLeft"]
    lr = corners["lowerRight"]
    ur = corners["upperRight"]
    ll = corners["lowerLeft"]

    min_lon = min(ul[0], ll[0], ur[0], lr[0])
    max_lon = max(ul[0], ll[0], ur[0], lr[0])
    min_lat = min(ul[1], ll[1], ur[1], lr[1])
    max_lat = max(ul[1], ll[1], ur[1], lr[1])

    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def main():
    parser = argparse.ArgumentParser(description="Inspect RTOFS GRIB2 region boundaries")
    parser.add_argument("--output", default="rtofs_region_bounds.json",
                        help="Output JSON file path")
    parser.add_argument("--cycle", default=None,
                        help="Force a specific cycle YYYYMMDD (auto-detected if omitted)")
    args = parser.parse_args()

    cycle = args.cycle or find_latest_cycle()
    if not cycle:
        print("[Error] No RTOFS cycle available.", file=sys.stderr)
        sys.exit(1)

    print(f"Using RTOFS cycle: {cycle}", file=sys.stderr)

    results = {}
    tmpdir = tempfile.mkdtemp(prefix="rtofs_inspect_")

    for region in REGIONS:
        rid = region["region_id"]
        url = f"{RTOFS_BASE}/rtofs.{cycle}/rtofs_glo.t00z.f024_{rid}_std.grb2"
        local = os.path.join(tmpdir, f"{rid}.grb2")

        print(f"  Downloading {rid} ...", file=sys.stderr)
        try:
            data = urllib.request.urlopen(url, timeout=60).read()
            with open(local, "wb") as f:
                f.write(data)
        except Exception as e:
            print(f"  [SKIP] {rid}: download failed — {e}", file=sys.stderr)
            continue

        try:
            bounds = get_grib2_bounds(local)
            results[rid] = bounds
            print(f"  {rid}: bounds={bounds['coordinates'][0][0]} .. {bounds['coordinates'][0][2]}", file=sys.stderr)
        except Exception as e:
            print(f"  [SKIP] {rid}: gdalinfo failed — {e}", file=sys.stderr)

        os.remove(local)

    os.rmdir(tmpdir)

    if not results:
        print("[Error] No regions could be inspected.", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nWrote {len(results)} region bounds to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
