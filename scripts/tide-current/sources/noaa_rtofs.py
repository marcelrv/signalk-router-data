#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""
Source collector: NOAA RTOFS Regional Ocean Currents (GRIB2).

Discovers the latest available forecast cycle on NOAA NOMADS via HEAD
requests, dynamically negotiating between RTOFS v2.5 and v3.0 filename formats,
and emits URL templates for each regional GRIB2 product.

Region boundaries are loaded from a cached inspection file.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta, timezone
from typing import Optional, Tuple, List

RTOFS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod"
_BOUNDS_FILE = os.path.join(os.path.dirname(__file__), "rtofs_region_bounds.json")


def _normalize_lon(lon: float) -> float:
    if lon > 180:
        return lon - 360
    return lon


def _load_region_bounds() -> dict:
    """Load cached boundaries from inspection, normalise to ±180 and split at dateline."""
    with open(_BOUNDS_FILE) as f:
        raw = json.load(f)
    result = {}
    for rid, geom in raw.items():
        coords = geom["coordinates"][0][:-1]
        lons = [pt[0] for pt in coords]
        lats = [pt[1] for pt in coords]
        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)

        if min_lon < 180 and max_lon > 180:
            east_min = min_lon
            east_max = 180.0
            west_min = -180.0
            west_max = _normalize_lon(max_lon)
            polygons = []
            if east_min < east_max:
                polygons.append({
                    "type": "Polygon",
                    "coordinates": [[
                        [east_min, min_lat],
                        [east_max, min_lat],
                        [east_max, max_lat],
                        [east_min, max_lat],
                        [east_min, min_lat],
                    ]],
                })
            if west_min < west_max:
                polygons.append({
                    "type": "Polygon",
                    "coordinates": [[
                        [west_min, min_lat],
                        [west_max, min_lat],
                        [west_max, max_lat],
                        [west_min, max_lat],
                        [west_min, min_lat],
                    ]],
                })
            result[rid] = polygons[0] if len(polygons) == 1 else {
                "type": "MultiPolygon",
                "coordinates": [p["coordinates"] for p in polygons],
            }
        else:
            result[rid] = {
                "type": "Polygon",
                "coordinates": [[
                    [_normalize_lon(min_lon), min_lat],
                    [_normalize_lon(max_lon), min_lat],
                    [_normalize_lon(max_lon), max_lat],
                    [_normalize_lon(min_lon), max_lat],
                    [_normalize_lon(min_lon), min_lat],
                ]],
            }
    return result


def _bbox_of_polygons(poly_coords: List) -> dict:
    """Compute the overall lat/lon bounding box spanning a list of polygon coordinate rings."""
    lons = [pt[0] for poly in poly_coords for ring in poly for pt in ring]
    lats = [pt[1] for poly in poly_coords for ring in poly for pt in ring]
    return {
        "min_lat": min(lats),
        "min_lon": min(lons),
        "max_lat": max(lats),
        "max_lon": max(lons),
    }


_REGION_BOUNDS = _load_region_bounds()

# NOAA RTOFS GRIB2 regional products with metadata parameters
REGIONS = [
    {"region_id": "west_atl",       "name": "Western Atlantic",              "description": "US East Coast / Gulf of Mexico / Caribbean",      "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "west_conus",     "name": "Western CONUS",                 "description": "US West Coast (California/Oregon/Washington)",   "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "gulf_alaska",    "name": "Gulf of Alaska",                "description": "Gulf of Alaska",                                  "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "alaska",         "name": "Alaska",                        "description": "Bering Sea / Aleutian Islands",                   "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "bering",         "name": "Bering Sea",                    "description": "Bering Sea / Bering Strait",                      "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "arctic",         "name": "Arctic",                        "description": "Beaufort / Chukchi Seas",                         "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "hudson_baffin",  "name": "Hudson Bay / Baffin Bay",       "description": "Hudson Bay / Davis Strait / Baffin Bay",          "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "trop_paci_lowres", "name": "Tropical Pacific (low-res)",  "description": "Tropical Pacific Ocean",                          "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "honolulu",       "name": "Hawaii (Honolulu)",             "description": "Hawaiian Islands region",                         "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "guam",           "name": "Guam / Mariana Islands",        "description": "Guam / Mariana Islands region",                   "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
    {"region_id": "samoa",          "name": "American Samoa",                "description": "American Samoa region",                           "cycle_hours": ["00"], "forecast_hours": [24, 48, 72], "has_nowcast": True},
]


def head(url: str, timeout: int = 15) -> bool:
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    except OSError:
        return False


def find_latest_cycle() -> Optional[Tuple[str, str]]:
    """
    Scans the NOMADS server to locate the latest valid cycle date.
    Tries RTOFS v3.0 standard formatting first, falling back to v2.5 formats if needed.
    """
    today = date.today()
    for offset in range(3):
        d = today - timedelta(days=offset)
        ymd = d.strftime("%Y%m%d")
        
        # Try V3 format check (Standard for RTOFS v3.0 mom6/cice6 transition)
        url_v3 = (
            RTOFS_BASE
            + f"/rtofs.{ymd}/rtofs_glo.t00z.f024.west_atl.std.grib2"
        )
        try:
            if head(url_v3):
                return d.strftime("%Y-%m-%dT00:00:00Z"), "v3"
        except Exception:
            pass
            
        # Try V2 fallback format
        url_v2 = (
            RTOFS_BASE
            + f"/rtofs.{ymd}/rtofs_glo.t00z.f024_west_atl_std.grb2"
        )
        try:
            if head(url_v2):
                return d.strftime("%Y-%m-%dT00:00:00Z"), "v2"
        except Exception:
            pass
            
    return None


def build_file_entry(region: dict, var: str, format_version: str):
    """Builds a file entry matching either the v2 or v3 filename standards."""
    entry = {
        "region_id": region["region_id"],
        "name": region["name"],
        "description": region["description"],
        "boundary_geometry": region["boundary_geometry"],
    }
    
    if format_version == "v3":
        if var == "nowcast":
            entry["type"] = "nowcast"
            entry["url_template"] = (
                RTOFS_BASE
                + "/rtofs.{YYYYMMDD}"
                + f"/rtofs_glo.t{{HH}}z.tm000.{region['region_id']}.std.grib2"
            )
            entry["forecast_hours"] = [24]
            entry["cycle_hours"] = region["cycle_hours"]
        else:
            entry["type"] = "forecast"
            entry["url_template"] = (
                RTOFS_BASE
                + "/rtofs.{YYYYMMDD}"
                + f"/rtofs_glo.t{{HH}}z.f{{hour:03d}}.{region['region_id']}.std.grib2"
            )
            entry["forecast_hours"] = region["forecast_hours"]
            entry["cycle_hours"] = region["cycle_hours"]
    else:
        # V2 standard fallback
        if var == "nowcast":
            entry["type"] = "nowcast"
            entry["url_template"] = (
                RTOFS_BASE
                + "/rtofs.{YYYYMMDD}"
                + f"/rtofs_glo.t{{HH}}z.n024_{region['region_id']}_std.grb2"
            )
            entry["forecast_hours"] = [24]
            entry["cycle_hours"] = region["cycle_hours"]
        else:
            entry["type"] = "forecast"
            entry["url_template"] = (
                RTOFS_BASE
                + "/rtofs.{YYYYMMDD}"
                + f"/rtofs_glo.t{{HH}}z.f{{hour:03d}}_{region['region_id']}_std.grb2"
            )
            entry["forecast_hours"] = region["forecast_hours"]
            entry["cycle_hours"] = region["cycle_hours"]

    return entry


def main():
    res = find_latest_cycle()
    if res:
        latest, format_version = res
    else:
        # Graceful default when offline/sandbox limits HTTP connectivity
        latest = (date.today() - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        format_version = "v3"

    files = []
    all_poly_coords = []
    for region in REGIONS:
        boundary = _REGION_BOUNDS[region["region_id"]]
        files.append(build_file_entry({**region, "boundary_geometry": boundary}, "forecast", format_version))
        if region["has_nowcast"]:
            files.append(build_file_entry({**region, "boundary_geometry": boundary}, "nowcast", format_version))
        if boundary["type"] == "MultiPolygon":
            all_poly_coords.extend(boundary["coordinates"])
        else:
            all_poly_coords.append(boundary["coordinates"])

    update_check = {
        "method": "expiry",
        "max_age_hours": 24,
        "last_checked": "",
    }
    if latest:
        update_check["latest_cycle"] = latest

    source = {
        "id": "noaa_rtofs",
        "source": "noaa",
        "type": "grib2",
        "name": "NOAA RTOFS Regional Ocean Currents",
        "description": "Regional ocean current forecasts from NOAA's "
                       "Real-Time Ocean Forecast System (RTOFS) in GRIB2 format",
        "contributor": "NOAA/NCEP",
        "url": "https://www.ncei.noaa.gov/products/weather-climate-models/"
                    "real-time-ocean-forecast",
        "tags": [
            "grib2",
            "regional",
            "gridded",
            "forecast",
            "noaa",
            "ocean-currents",
            "daily",
        ],
        "region": {
            "name": "US coastal waters, Arctic, Tropical Pacific",
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
