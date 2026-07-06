#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""
Source collector: BSH Surface Current Forecasts (GRIB2).

Discovers the latest available forecast cycle on the BSH FTP server and
emits URL templates for each regional GRIB2 product (North Sea, Baltic Sea,
Elbe).  Each VV (forecast day) file contains 24 hours of 15-minute current
data (U/V components) at the stated grid resolution.

Region boundaries are loaded from actual GRIB inspection.
"""

import json
import os
import subprocess
import sys
from datetime import date, timedelta, timezone
from typing import Optional, Tuple

BSH_BASE = "ftp://ftp.bsh.de/Stroemungsvorhersagen/grib2"

BASIN_NORDSEE = "Nordsee"
BASIN_OSTSEE = "Ostsee"
BASIN_ELBE = "Elbe"

REGIONS = [
    # --- North Sea (Nordsee) ---
    {
        "region_id": "north_sea",
        "bsh_code": "no",
        "basin": BASIN_NORDSEE,
        "name": "North Sea",
        "description": "North Sea coarse grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 48.6, "max_lat": 60.65, "min_lon": -3.92, "max_lon": 8.92},
    },
    {
        "region_id": "german_bight",
        "bsh_code": "db",
        "basin": BASIN_NORDSEE,
        "name": "German Bight",
        "description": "German Bight fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 53.225, "max_lat": 56.45, "min_lon": 6.167, "max_lon": 9.5},
    },
    {
        "region_id": "inner_german_bight",
        "bsh_code": "idb",
        "basin": BASIN_NORDSEE,
        "name": "Inner German Bight",
        "description": "Inner German Bight fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 53.225, "max_lat": 54.25, "min_lon": 7.75, "max_lon": 9.0},
    },
    {
        "region_id": "north_frisian_islands",
        "bsh_code": "nfi",
        "basin": BASIN_NORDSEE,
        "name": "North Frisian Islands",
        "description": "Area of North Frisian Islands fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 54.242, "max_lat": 55.25, "min_lon": 7.75, "max_lon": 9.0},
    },
    {
        "region_id": "east_frisian_islands",
        "bsh_code": "ofi",
        "basin": BASIN_NORDSEE,
        "name": "East Frisian Islands",
        "description": "Area of East Frisian Islands fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 53.225, "max_lat": 54.25, "min_lon": 6.167, "max_lon": 7.75},
    },
    # --- Baltic Sea (Ostsee) ---
    {
        "region_id": "baltic_sea",
        "bsh_code": "ba",
        "basin": BASIN_OSTSEE,
        "name": "Baltic Sea",
        "description": "Baltic Sea coarse grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 53.6, "max_lat": 65.85, "min_lon": 8.917, "max_lon": 30.25},
    },
    {
        "region_id": "western_baltic",
        "bsh_code": "wb",
        "basin": BASIN_OSTSEE,
        "name": "Western Baltic Sea",
        "description": "Western Baltic Sea fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 53.225, "max_lat": 56.45, "min_lon": 9.194, "max_lon": 14.917},
    },
    {
        "region_id": "kiel_bight",
        "bsh_code": "kbu",
        "basin": BASIN_OSTSEE,
        "name": "Kiel Bight",
        "description": "Kiel Bight fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 54.3, "max_lat": 54.833, "min_lon": 9.833, "max_lon": 11.333},
    },
    {
        "region_id": "luebeck_bight",
        "bsh_code": "mbu",
        "basin": BASIN_OSTSEE,
        "name": "Lübeck Bight",
        "description": "Lübeck Bight fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 53.9, "max_lat": 54.75, "min_lon": 10.75, "max_lon": 12.528},
    },
    {
        "region_id": "ruegen",
        "bsh_code": "rgn",
        "basin": BASIN_OSTSEE,
        "name": "Area around Rügen",
        "description": "Area around the Isle of Rügen fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 53.225, "max_lat": 55.0, "min_lon": 12.5, "max_lon": 14.5},
    },
    {
        "region_id": "the_sound",
        "bsh_code": "snd",
        "basin": BASIN_OSTSEE,
        "name": "The Sound",
        "description": "The Sound (Öresund) fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 56.1, "max_lat": 56.3, "min_lon": 12.167, "max_lon": 13.097},
    },
    {
        "region_id": "the_belts",
        "bsh_code": "blt",
        "basin": BASIN_OSTSEE,
        "name": "The Belts",
        "description": "The Belts (Storebælt / Lillebælt) fine grid",
        "forecast_days": 3,
        "bounds": {"min_lat": 54.7, "max_lat": 56.0, "min_lon": 9.5, "max_lon": 11.306},
    },
    # --- Elbe ---
    {
        "region_id": "elbe_outer",
        "bsh_code": "AusAlt",
        "basin": BASIN_ELBE,
        "name": "Elbe: Outer Elbe to Altenbruch",
        "description": "Elbe river section from the Outer Elbe to Altenbruch, 90m grid",
        "forecast_days": 2,
        "bounds": {"min_lat": 53.85, "max_lat": 54.04, "min_lon": 8.305, "max_lon": 8.75},
    },
    {
        "region_id": "elbe_cuxhaven_brunsbuettel",
        "bsh_code": "CuxBru",
        "basin": BASIN_ELBE,
        "name": "Elbe: Cuxhaven to Brunsbüttel",
        "description": "Elbe river section from Cuxhaven to Brunsbüttel, 90m grid",
        "forecast_days": 2,
        "bounds": {"min_lat": 53.82, "max_lat": 53.95, "min_lon": 8.68, "max_lon": 9.25},
    },
    {
        "region_id": "elbe_brunsbuettel_pagensand",
        "bsh_code": "BruPag",
        "basin": BASIN_ELBE,
        "name": "Elbe: Brunsbüttel to Pagensand",
        "description": "Elbe river section from Brunsbüttel to Pagensand, 90m grid",
        "forecast_days": 2,
        "bounds": {"min_lat": 53.65, "max_lat": 53.9, "min_lon": 9.2, "max_lon": 9.583},
    },
    {
        "region_id": "elbe_pagensand_hamburg",
        "bsh_code": "PagHam",
        "basin": BASIN_ELBE,
        "name": "Elbe: Pagensand to Hamburg",
        "description": "Elbe river section from Pagensand to Hamburg, 90m grid",
        "forecast_days": 2,
        "bounds": {"min_lat": 53.5, "max_lat": 53.7, "min_lon": 9.3, "max_lon": 9.9},
    },
]


def _polygon_from_bounds(b: dict) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [b["min_lon"], b["min_lat"]],
            [b["max_lon"], b["min_lat"]],
            [b["max_lon"], b["max_lat"]],
            [b["min_lon"], b["max_lat"]],
            [b["min_lon"], b["min_lat"]],
        ]],
    }


def _bbox_of_polygons(poly_coords: list) -> dict:
    lons = [pt[0] for poly in poly_coords for ring in poly for pt in ring]
    lats = [pt[1] for poly in poly_coords for ring in poly for pt in ring]
    return {
        "min_lat": min(lats),
        "min_lon": min(lons),
        "max_lat": max(lats),
        "max_lon": max(lons),
    }


def head_ftp(url: str, timeout: int = 10) -> bool:
    """Check if a file exists on FTP via curl -I (SIZE command)."""
    try:
        result = subprocess.run(
            ["curl", "-sI", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        return "Content-Length:" in result.stdout or "Last-Modified:" in result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return False


def find_latest_cycle() -> Optional[Tuple[str, str]]:
    """Scan the BSH FTP to locate the latest valid cycle."""
    today = date.today()
    for offset in range(3):
        d = today - timedelta(days=offset)
        ymd = d.strftime("%Y%m%d")

        url_00 = f"{BSH_BASE}/{BASIN_NORDSEE}/Current_no_{ymd}00_00.grb2"
        if head_ftp(url_00):
            return d.strftime("%Y-%m-%dT00:00:00Z"), "00"

        url_12 = f"{BSH_BASE}/{BASIN_NORDSEE}/Current_no_{ymd}12_01.grb2"
        if head_ftp(url_12):
            return d.strftime("%Y-%m-%dT12:00:00Z"), "12"

    return None


def build_file_entry(region: dict, vv: int, label: str, cycle_hours: list[str]) -> dict:
    """Build a single file entry for a given VV (forecast day) value."""
    b = region["bounds"]
    return {
        "region_id": region["region_id"],
        "name": region["name"],
        "description": region["description"],
        "boundary_geometry": _polygon_from_bounds(b),
        "type": label,
        "url_template": (
            f"{BSH_BASE}/{region['basin']}"
            f"/Current_{region['bsh_code']}_{{YYYYMMDD}}{{HH}}_{vv:02d}.grb2"
        ),
        "forecast_hours": [vv * 24],
        "cycle_hours": cycle_hours,
    }


def main():
    res = find_latest_cycle()
    latest_cycle = None
    if res:
        latest_cycle, _ = res

    all_poly_coords = []
    files = []

    for region in REGIONS:
        b = region["bounds"]
        poly = _polygon_from_bounds(b)
        all_poly_coords.append(poly["coordinates"])

        # Nowcast: VV=00 (00Z cycle only)
        files.append(build_file_entry(region, 0, "nowcast", ["00"]))

        # Forecast day 1: VV=01 (both cycles)
        files.append(build_file_entry(region, 1, "forecast", ["00", "12"]))

        # Forecast day 2: VV=02 (12Z cycle only)
        files.append(build_file_entry(region, 2, "forecast", ["12"]))

        # Forecast day 3: VV=03 (12Z cycle only, Nordsee/Ostsee only)
        if region["forecast_days"] >= 3:
            files.append(build_file_entry(region, 3, "forecast", ["12"]))

    update_check = {
        "method": "expiry",
        "max_age_hours": 12,
        "last_checked": "",
    }
    if latest_cycle:
        update_check["latest_cycle"] = latest_cycle

    source = {
        "id": "bsh_currents",
        "source": "bsh",
        "type": "grib2",
        "name": "BSH Surface Current Forecasts",
        "description": "Regional surface current forecasts from the German Federal Maritime and "
                       "Hydrographic Agency (BSH) in GRIB2 format. Covers the North Sea, Baltic Sea, "
                       "and Elbe river with up to 3-day forecasts at 15-minute resolution.",
        "contributor": "BSH (Bundesamt für Seeschifffahrt und Hydrographie)",
        "url": "https://www.bsh.de/EN/DATA/Predictions/Currents/"
               "Surface_currents_for_sailors/surface_currents_for_sailors_node.html",
        "tags": [
            "grib2",
            "regional",
            "gridded",
            "forecast",
            "bsh",
            "ocean-currents",
            "north-sea",
            "baltic-sea",
            "twice-daily",
        ],
        "region": {
            "name": "North Sea, Baltic Sea, Elbe river",
            "bounding_box": _bbox_of_polygons(all_poly_coords),
            "boundary_geometry": {
                "type": "MultiPolygon",
                "coordinates": all_poly_coords,
            },
        },
        "update_check": update_check,
        "files": files,
    }

    json.dump(source, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
