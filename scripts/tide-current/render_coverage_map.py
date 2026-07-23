#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Render a coverage map of tide/current data sources with type-based coloring."""
import argparse
import json
import sys

import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon, Patch
from shapely.geometry import shape, box as shapely_box
from shapely.ops import unary_union

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    USE_CARTOPY = True
except ImportError:
    USE_CARTOPY = False


def _polygon_area_deg(geom):
    """Approximate planar area of a shapely geometry in square degrees."""
    if geom.is_empty:
        return 0
    return geom.area


def _is_global(geom):
    """Heuristic: a source whose rendered geometry covers >80% of the world is global."""
    if geom is None or geom.is_empty:
        return False
    return _polygon_area_deg(geom) > 0.8 * 360 * 180


def main():
    parser = argparse.ArgumentParser(description="Render tide/current coverage map.")
    parser.add_argument("--index", default="tide-current-index.json")
    parser.add_argument("--output", default="tide-current-coverage.png")
    args = parser.parse_args()

    with open(args.index) as f:
        index = json.load(f)

    sources = index.get("sources", [])
    if not sources:
        print("  [SKIP] coverage map — no sources", file=sys.stderr)
        return

    type_styles = {
        "harmonic":               {"color": "#3b8fd4", "label": "Harmonic constituents"},
        "harmonic_constituents":  {"color": "#3b8fd4", "label": "Harmonic constituents"},
        "grib2":                  {"color": "#22c55e", "label": "GRIB2 forecast"},
        "forecast":               {"color": "#22c55e", "label": "Forecast"},
        "utcef":                  {"color": "#f59e0b", "label": "UTCEF database"},
        "station":                {"color": "#8b5cf6", "label": "Station data"},
    }

    # Build shapely geometries for each source
    src_geoms = []
    for src in sources:
        gd = src.get("region", {}).get("boundary_geometry")
        bb = src.get("region", {}).get("bounding_box")
        geom = None
        if gd:
            try:
                geom = shape(gd)
            except Exception:
                pass
        if (geom is None or geom.is_empty) and bb:
            geom = shapely_box(bb["min_lon"], bb["min_lat"], bb["max_lon"], bb["max_lat"])
        src_geoms.append(geom)

    bounded, globals_list = [], []
    for src, geom in zip(sources, src_geoms):
        (globals_list if _is_global(geom) else bounded).append(src)

    # Compute extent from bounded sources only
    bounded_ids = {s["id"] for s in bounded}
    bounded_geoms = [
        g for i, g in enumerate(src_geoms)
        if sources[i]["id"] in bounded_ids and g is not None and not g.is_empty
    ]
    if bounded and bounded_geoms:
        union_geom = unary_union(bounded_geoms)
        if not union_geom.is_empty:
            bounds = union_geom.bounds  # (minx, miny, maxx, maxy)
            extent_min_lon, extent_min_lat, extent_max_lon, extent_max_lat = bounds
        else:
            extent_min_lon, extent_max_lon = -180, 180
            extent_min_lat, extent_max_lat = -90, 90
    else:
        extent_min_lon, extent_max_lon = -180, 180
        extent_min_lat, extent_max_lat = -90, 90

    # Clamp longitude to [-180, 180] — cartopy chokes on wider ranges
    extent_min_lon = max(extent_min_lon, -180.0)
    extent_max_lon = min(extent_max_lon, 180.0)
    extent_min_lat = max(extent_min_lat, -90.0)
    extent_max_lat = min(extent_max_lat, 90.0)

    lon_span = extent_max_lon - extent_min_lon
    lat_span = extent_max_lat - extent_min_lat

    # Margin for latitude always; longitude margin only when < full globe
    margin_lat = max(2.0, lat_span * 0.25) if lat_span > 0 else 2.0
    margin_lon = 0.0 if lon_span >= 359.9 else max(2.0, lon_span * 0.25)

    # Final clamp — never exceed cartopy's valid range
    e_min_lon = max(extent_min_lon - margin_lon, -180.0)
    e_max_lon = min(extent_max_lon + margin_lon, 180.0)
    e_min_lat = max(extent_min_lat - margin_lat, -90.0)
    e_max_lat = min(extent_max_lat + margin_lat, 90.0)

    # Heuristic: Determine if map should view full global scope or regional zoom
    show_global = (lon_span > 120 or lat_span > 60)

    if USE_CARTOPY:
        fig, ax = plt.subplots(figsize=(14, 8), subplot_kw={"projection": ccrs.PlateCarree()})
        if show_global:
            ax.set_global()
        else:
            ax.set_extent([e_min_lon, e_max_lon, e_min_lat, e_max_lat], crs=ccrs.PlateCarree())
        ax.add_feature(cfeature.LAND, color="#e8e8e8", edgecolor="#cccccc", linewidth=0.3)
        ax.add_feature(cfeature.OCEAN, color="#f8f8f8")
        ax.add_feature(cfeature.COASTLINE, linewidth=0.4, edgecolor="#999999")
    else:
        fig, ax = plt.subplots(figsize=(14, 8))
        ax.set_facecolor("#f8f8f8")
        if show_global:
            ax.set_xlim(-180, 180)
            ax.set_ylim(-90, 90)
        else:
            ax.set_xlim(extent_min_lon - margin_lon, extent_max_lon + margin_lon)
            ax.set_ylim(extent_min_lat - margin_lat, extent_max_lat + margin_lat)
        ax.grid(True, alpha=0.3)

    # Render each bounded source
    rendered_types = set()
    for src in bounded:
        style = type_styles.get(src.get("type", ""), {"color": "#64748b", "label": "Other"})
        color = style["color"]
        rendered_types.add(src.get("type", ""))
        geom_data = src.get("region", {}).get("boundary_geometry")
        bbox = src.get("region", {}).get("bounding_box")

        if geom_data:
            try:
                geom = shape(geom_data)
            except Exception:
                continue
            if geom.is_empty:
                continue
            
            if USE_CARTOPY:
                # Plot the shapely geometry directly, letting Cartopy handle projection-aware boundary clipping
                ax.add_geometries([geom], crs=ccrs.PlateCarree(),
                                  facecolor=color, edgecolor=color, alpha=0.3, linewidth=0.5)
            else:
                if geom.geom_type == "Polygon":
                    xs, ys = geom.exterior.xy
                    ax.fill(xs, ys, color=color, alpha=0.3, edgecolor=color, linewidth=0.5)
                elif geom.geom_type == "MultiPolygon":
                    for poly in geom.geoms:
                        xs, ys = poly.exterior.xy
                        ax.fill(xs, ys, color=color, alpha=0.3, edgecolor=color, linewidth=0.5)
        elif bbox:
            coords = [
                [bbox["min_lon"], bbox["min_lat"]],
                [bbox["max_lon"], bbox["min_lat"]],
                [bbox["max_lon"], bbox["max_lat"]],
                [bbox["min_lon"], bbox["max_lat"]],
                [bbox["min_lon"], bbox["min_lat"]],
            ]
            if USE_CARTOPY:
                import shapely.geometry as sgeom
                geom = sgeom.Polygon(coords)
                ax.add_geometries([geom], crs=ccrs.PlateCarree(),
                                  facecolor=color, edgecolor=color, alpha=0.3, linewidth=0.5)
            else:
                p = MplPolygon(coords, closed=True, facecolor=color, edgecolor=color,
                               linewidth=0.5, alpha=0.3)
                ax.add_patch(p)

    # Legend
    legend_patches = []
    for t in sorted(rendered_types):
        st = type_styles.get(t, {"color": "#64748b", "label": "Other"})
        legend_patches.append(Patch(color=st["color"], alpha=0.5, label=st["label"]))

    if globals_list:
        names = ", ".join(s["name"] for s in globals_list)
        legend_patches.append(Patch(color="none", label=f"Global: {names}"))

    if legend_patches:
        ax.legend(handles=legend_patches, loc="lower left", framealpha=0.85, fontsize=8, ncol=2)

    ax.set_title("Nautical Tidal Streams & Currents — Data Sources", fontsize=14, pad=16)

    if not USE_CARTOPY:
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")

    plt.tight_layout()
    plt.savefig(args.output, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Coverage map saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
