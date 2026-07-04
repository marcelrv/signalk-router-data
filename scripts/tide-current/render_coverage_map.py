#!/usr/bin/env python3
"""Render a coverage map of tide/current data sources with type-based coloring."""
import json
import os
import sys

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

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPolygon, Patch
        from shapely.geometry import shape
    except ImportError as e:
        print(f"  [SKIP] coverage map — missing dependency: {e}", file=sys.stderr)
        return

    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
        USE_CARTOPY = True
    except ImportError:
        USE_CARTOPY = False
        print("  [INFO] cartopy not available, using simple Matplotlib map", file=sys.stderr)

    # Compute combined bounds
    min_lons, max_lons, min_lats, max_lats = [], [], [], []
    for src in sources:
        bbox = src.get("region", {}).get("bounding_box")
        if bbox:
            min_lons.append(bbox["min_lon"])
            max_lons.append(bbox["max_lon"])
            min_lats.append(bbox["min_lat"])
            max_lats.append(bbox["max_lat"])

    if min_lons:
        world_min_lon = min(min_lons)
        world_max_lon = max(max_lons)
        world_min_lat = min(min_lats)
        world_max_lat = max(max_lats)
    else:
        world_min_lon, world_max_lon = -180, 180
        world_min_lat, world_max_lat = -90, 90

    lon_span = world_max_lon - world_min_lon
    lat_span = world_max_lat - world_min_lat
    margin_lon = max(2.0, lon_span * 0.2)
    margin_lat = max(2.0, lat_span * 0.2)
    show_global = (lon_span > 120 or lat_span > 60)

    # Type-based coloring
    type_styles = {
        "harmonic":         {"color": "#3b8fd4", "label": "Harmonic constituents"},
        "harmonic_constituents": {"color": "#3b8fd4", "label": "Harmonic constituents"},
        "grib2":            {"color": "#22c55e", "label": "GRIB2 forecast"},
        "forecast":         {"color": "#22c55e", "label": "Forecast"},
        "utcef":            {"color": "#f59e0b", "label": "UTCEF database"},
        "station":          {"color": "#8b5cf6", "label": "Station data"},
    }

    if USE_CARTOPY:
        fig, ax = plt.subplots(figsize=(14, 8), subplot_kw={"projection": ccrs.PlateCarree()})
        if show_global:
            ax.set_global()
        else:
            ax.set_extent([
                world_min_lon - margin_lon, world_max_lon + margin_lon,
                world_min_lat - margin_lat, world_max_lat + margin_lat,
            ], crs=ccrs.PlateCarree())
        ax.add_feature(cfeature.LAND, color="#e8e8e8", edgecolor="#cccccc", linewidth=0.3)
        ax.add_feature(cfeature.OCEAN, color="#f8f8f8")
        ax.add_feature(cfeature.COASTLINE, linewidth=0.4, edgecolor="#999999")
    else:
        fig, ax = plt.subplots(figsize=(14, 8))
        ax.set_facecolor("#f8f8f8")

    for src in sources:
        style = type_styles.get(src.get("type", ""), {"color": "#64748b", "label": "Other"})
        color = style["color"]
        geom_data = src.get("region", {}).get("boundary_geometry")
        bbox = src.get("region", {}).get("bounding_box")

        if geom_data:
            try:
                geom = shape(geom_data)
            except Exception:
                continue
            if geom.is_empty:
                continue

            if geom.geom_type == "Polygon":
                xs, ys = geom.exterior.xy
                kwargs = {"transform": ccrs.PlateCarree()} if USE_CARTOPY else {}
                ax.fill(xs, ys, color=color, alpha=0.25, edgecolor=color, linewidth=1.0, **kwargs)
            elif geom.geom_type == "MultiPolygon":
                for poly in geom.geoms:
                    xs, ys = poly.exterior.xy
                    kwargs = {"transform": ccrs.PlateCarree()} if USE_CARTOPY else {}
                    ax.fill(xs, ys, color=color, alpha=0.25, edgecolor=color, linewidth=1.0, **kwargs)
        elif bbox:
            coords = [
                [bbox["min_lon"], bbox["min_lat"]],
                [bbox["max_lon"], bbox["min_lat"]],
                [bbox["max_lon"], bbox["max_lat"]],
                [bbox["min_lon"], bbox["max_lat"]],
                [bbox["min_lon"], bbox["min_lat"]],
            ]
            poly = MplPolygon(coords, closed=True, facecolor=color, edgecolor=color,
                              linewidth=1.0, alpha=0.25)
            ax.add_patch(poly)

    # Legend by type
    seen_types = set()
    legend_patches = []
    for src in sources:
        t = src.get("type", "")
        style = type_styles.get(t, {"color": "#64748b", "label": "Other"})
        if t not in seen_types:
            seen_types.add(t)
            legend_patches.append(Patch(color=style["color"], alpha=0.5, label=style["label"]))

    if legend_patches:
        ax.legend(handles=legend_patches, loc="lower left", framealpha=0.85, fontsize=8, ncol=2)

    ax.set_title("Nautical Tidal Streams & Currents — Data Sources", fontsize=14, pad=16)

    if not USE_CARTOPY:
        if show_global:
            ax.set_xlim(-180, 180)
            ax.set_ylim(-90, 90)
        else:
            ax.set_xlim(world_min_lon - margin_lon, world_max_lon + margin_lon)
            ax.set_ylim(world_min_lat - margin_lat, world_max_lat + margin_lat)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(args.output, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Coverage map saved to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    import argparse
    main()
