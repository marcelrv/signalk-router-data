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

Per-feature color: GitHub's geojson renderer (like most GitHub/Mapbox-family
tools) reads the "simplestyle-spec" properties (`fill`, `fill-opacity`,
`stroke`, `stroke-opacity`) directly off each Feature — without them every
polygon renders in one default color and grib2 vs utcef becomes
indistinguishable by anything other than clicking each one individually.
Colors/opacity come from source_type_labels.style_for(), the same table
render_coverage_map.py uses, so the two maps agree. Feature order in the
output array matters too, for the same reason draw_order matters in the
PNG: GitHub's viewer paints array order back-to-front and exposes no
z-index control, so features are sorted by draw_order before writing —
otherwise the far more numerous utcef/harmonic polygons (which sort
alphabetically after the grib2 sources in tide-current-index.json) would
paint over the forecast regions again, the exact bug the PNG fix
addressed.

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

from source_type_labels import style_for


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


def _style_properties(source_type: str) -> dict:
    style = style_for(source_type)
    return {
        "stroke": style["color"],
        "stroke-width": 1,
        "stroke-opacity": 0.7,
        "fill": style["color"],
        "fill-opacity": style["alpha"],
    }


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
        src_type = src.get("type", "")
        style = style_for(src_type)
        style_props = _style_properties(src_type)
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
                        "category": style["label"],
                        "provider": provider,
                        "description": file_entry.get("description") or src.get("description", ""),
                        "info_url": info_url,
                        **style_props,
                    },
                    "_draw_order": style["draw_order"],
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
                    "category": style["label"],
                    "provider": provider,
                    "description": src.get("description", ""),
                    "info_url": info_url,
                    **style_props,
                },
                "_draw_order": style["draw_order"],
            })

    # Sort by draw_order (background types first) so GitHub's viewer, which
    # paints array order back-to-front with no z-index control, keeps
    # forecast regions visible on top — then strip the sort-only key before
    # writing, since it isn't part of the GeoJSON spec.
    features.sort(key=lambda f: f["_draw_order"])
    for f in features:
        del f["_draw_order"]

    collection = {"type": "FeatureCollection", "features": features}

    with open(args.output, "w") as f:
        json.dump(collection, f, indent=2)
    print(f"  Coverage GeoJSON saved to {args.output} ({len(features)} features)", file=sys.stderr)


if __name__ == "__main__":
    main()
