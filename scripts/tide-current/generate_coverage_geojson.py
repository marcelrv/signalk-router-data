#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Emit tide-current-coverage.geojson from tide-current-index.json.

Why this exists: GitHub natively renders any *.geojson file committed to a
repo as an interactive Leaflet map right in the repo web UI — pan, zoom,
and click a feature to see a popup of its properties — with zero extra
hosting or JavaScript of our own. tide-current-coverage.png (rendered by
render_coverage_map.py) is a static snapshot; this is the same coverage
data as a file GitHub itself makes interactive. Click-to-inspect only, not
true mouseover — a hover-tooltip experience would need a custom page (e.g.
GitHub Pages + Leaflet reading tide-current-index.json client-side), which
is a bigger commitment (enabling Pages, maintaining a JS bundle) than this
file, deliberately not attempted here.

Two catalog shapes to handle, confirmed live against tide-current-index.json
rather than assumed from the spec doc: multi-region grib2 sources (BSH,
CMEMS IBI/NWS, NOS OFS) carry a distinct `boundary_geometry` on each
`files[]` entry, one polygon per named region — for these, emit one
feature per file entry, since collapsing e.g. cmems_nws's 12 regions into
one multi-polygon would show only the parent source's properties on
click, hiding exactly the per-region detail (name, size) a sailor would
want. utcef/harmonic sources are the opposite: one source = one region =
one file, geometry lives only at `region.boundary_geometry`, and
`files[]` entries have no geometry of their own at all — for these, emit
one feature per source instead. Properties are deliberately end-user
framed (see source_type_labels.py) rather than exposing internal fields
like `type` or `source` id directly.
"""
import json
import sys

from source_type_labels import label_for


def _is_global(bbox: dict) -> bool:
    """Same heuristic as render_coverage_map.py's _is_global, restated
    against bounding_box span instead of shapely polygon area — this
    script is intentionally stdlib-only, matching the other collector
    scripts' convention, so it doesn't pull in shapely just for this one
    check. A source whose region spans virtually the whole world (e.g. the
    OpenCPN/XTide global harmonic stations, -90..90 / -180..180) would
    render as one giant rectangle dominating the whole interactive map,
    burying every regional feature under it — excluded here for the same
    reason the PNG map lists it as text instead of shading the globe."""
    if not bbox:
        return False
    lat_span = bbox.get("max_lat", 0) - bbox.get("min_lat", 0)
    lon_span = bbox.get("max_lon", 0) - bbox.get("min_lon", 0)
    return lat_span > 150 and lon_span > 300


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="tide-current-index.json")
    parser.add_argument("--output", default="tide-current-coverage.geojson")
    args = parser.parse_args()

    with open(args.index) as f:
        index = json.load(f)

    features = []
    for src in index.get("sources", []):
        category = label_for(src.get("type", ""))
        provider = src.get("contributor", src.get("name", ""))
        info_url = src.get("url", "")

        per_file_geoms = [
            (fe, fe["boundary_geometry"])
            for fe in src.get("files", [])
            if fe.get("boundary_geometry")
        ]
        if per_file_geoms:
            # Multi-region grib2 source (BSH, CMEMS IBI/NWS, NOS OFS, ...):
            # one feature per named region.
            for file_entry, geom in per_file_geoms:
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "region": file_entry.get("name", file_entry.get("region_id", "")),
                        "category": category,
                        "provider": provider,
                        "description": file_entry.get("description") or src.get("description", ""),
                        "info_url": info_url,
                    },
                })
        else:
            # utcef/harmonic source: one region, geometry only at the
            # source level.
            region = src.get("region", {})
            geom = region.get("boundary_geometry")
            if not geom or _is_global(region.get("bounding_box")):
                continue
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "region": src.get("region", {}).get("name", src.get("name", "")),
                    "category": category,
                    "provider": provider,
                    "description": src.get("description", ""),
                    "info_url": info_url,
                },
            })

    collection = {"type": "FeatureCollection", "features": features}

    with open(args.output, "w") as f:
        json.dump(collection, f, indent=2)
    print(f"  Coverage GeoJSON saved to {args.output} ({len(features)} features)", file=sys.stderr)


if __name__ == "__main__":
    main()
