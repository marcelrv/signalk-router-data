#!/usr/bin/env python3
"""
Source collector: NOAA RTOFS Regional Ocean Currents (GRIB2).

Discovers the latest available forecast cycle on NOAA NOMADS via HEAD
requests, emits URL templates for each regional GRIB2 product.

Standalone:  python3 noaa_rtofs.py  →  JSON to stdout
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta, timezone
from typing import Optional

RTOFS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod"

# RTOFS v2 GRIB2 regional products
REGIONS = [
    {
        "region_id": "west_atl",
        "name": "Western Atlantic",
        "description": "US East Coast / Gulf of Mexico / Caribbean",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-100, 15], [-50, 15], [-50, 48], [-100, 48], [-100, 15]]],
        },
    },
    {
        "region_id": "west_conus",
        "name": "Western CONUS",
        "description": "US West Coast (California/Oregon/Washington)",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-128, 30], [-116, 30], [-116, 50], [-128, 50], [-128, 30]]],
        },
    },
    {
        "region_id": "gulf_alaska",
        "name": "Gulf of Alaska",
        "description": "Gulf of Alaska",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-170, 50], [-130, 50], [-130, 62], [-170, 62], [-170, 50]]],
        },
    },
    {
        "region_id": "alaska",
        "name": "Alaska",
        "description": "Bering Sea / Aleutian Islands",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-180, 48], [-155, 48], [-155, 66], [-180, 66], [-180, 48]]],
        },
    },
    {
        "region_id": "bering",
        "name": "Bering Sea",
        "description": "Bering Sea / Bering Strait",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-180, 52], [-160, 52], [-160, 66], [-180, 66], [-180, 52]]],
        },
    },
    {
        "region_id": "arctic",
        "name": "Arctic",
        "description": "Arctic Ocean",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-180, 60], [180, 60], [180, 90], [-180, 90], [-180, 60]]],
        },
    },
    {
        "region_id": "hudson_baffin",
        "name": "Hudson Bay / Baffin Bay",
        "description": "Hudson Bay / Davis Strait / Baffin Bay",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-100, 50], [-50, 50], [-50, 80], [-100, 80], [-100, 50]]],
        },
    },
    {
        "region_id": "trop_paci_lowres",
        "name": "Tropical Pacific (low-res)",
        "description": "Tropical Pacific Ocean",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-180, -20], [180, -20], [180, 20], [-180, 20], [-180, -20]]],
        },
    },
    {
        "region_id": "honolulu",
        "name": "Hawaii (Honolulu)",
        "description": "Hawaiian Islands region",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-165, 15], [-150, 15], [-150, 26], [-165, 26], [-165, 15]]],
        },
    },
    {
        "region_id": "guam",
        "name": "Guam / Mariana Islands",
        "description": "Guam / Mariana Islands region",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[140, 10], [150, 10], [150, 20], [140, 20], [140, 10]]],
        },
    },
    {
        "region_id": "samoa",
        "name": "American Samoa",
        "description": "American Samoa region",
        "cycle_hours": ["00"],
        "forecast_hours": [24, 48, 72],
        "has_nowcast": True,
        "boundary_geometry": {
            "type": "Polygon",
            "coordinates": [[[-175, -20], [-165, -20], [-165, -10], [-175, -10], [-175, -20]]],
        },
    },
]

ANCHOR = (
    RTOFS_BASE
    + "/rtofs.{YYYYMMDD}"
    + "/rtofs_glo.t{HH}z.f024_west_atl_std.grb2"
)


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


def find_latest_cycle() -> Optional[str]:
    today = date.today()
    for offset in range(3):
        d = today - timedelta(days=offset)
        ymd = d.strftime("%Y%m%d")
        url = ANCHOR.replace("{YYYYMMDD}", ymd).replace("{HH}", "00")
        try:
            if head(url):
                return d.strftime("%Y-%m-%dT00:00:00Z")
        except (urllib.error.URLError, OSError):
            continue
    return None


def build_file_entry(region: dict, var: str):
    """Build a file entry for a forecast (f) or nowcast (n) variant."""
    prefix = "n" if var == "nowcast" else "f"
    entry = {
        "region_id": region["region_id"],
        "name": region["name"],
        "description": region["description"],
        "boundary_geometry": region["boundary_geometry"],
    }

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
    latest = find_latest_cycle()

    files = []
    for region in REGIONS:
        files.append(build_file_entry(region, "forecast"))
        if region["has_nowcast"]:
            files.append(build_file_entry(region, "nowcast"))

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
        "homepage": "https://www.ncei.noaa.gov/products/weather-climate-models/"
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
            "boundary_geometry": {
                "type": "Polygon",
                "coordinates": [[[-180, -20], [180, -20], [180, 90], [-180, 90], [-180, -20]]],
            },
        },
        "update_check": update_check,
        "files": files,
    }

    json.dump(source, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
