#!/usr/bin/env python3
"""
Generate index.json and coverage-map.png for the signalk-router-data repository.

Scans all .sqlite.gz files under regions/, decompresses each to a temp
location, reads metadata and stats, then produces:
  - index.json        — machine-readable catalog of all available databases
  - coverage-map.png  — world map showing coverage areas for README

Also accepts plain .sqlite files for development convenience.

Usage:
    python3 generate_index.py [--regions-dir ./regions] [--output-dir .]
"""

import os
import sys
import gzip
import json
import glob
import hashlib
import shutil
import argparse
import sqlite3
import tempfile
from datetime import datetime, timezone

COORD_SPACE = 36000000
TYPE_MASK = 648_000_000_000_000


def get_node_type_int(nid: int) -> int:
    return nid // TYPE_MASK


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def is_valid_sqlite(path: str) -> bool:
    """Quick check: first 16 bytes should be 'SQLite format 3\\x00'."""
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        return header == b'SQLite format 3\x00'
    except Exception:
        return False


def read_metadata_from_sqlite(db_path: str) -> dict | None:
    """Read metadata from an uncompressed .sqlite file."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='metadata'")
        if not cur.fetchone():
            conn.close()
            return None

        cur.execute("SELECT * FROM metadata LIMIT 1")
        row = cur.fetchone()
        if not row:
            conn.close()
            return None

        columns = [desc[0] for desc in cur.description]
        md = dict(zip(columns, row))

        try:
            cur.execute("SELECT COUNT(*) FROM nodes")
            node_count = cur.fetchone()[0]
        except Exception:
            node_count = 0

        try:
            cur.execute("SELECT COUNT(*) FROM edges")
            edge_count = cur.fetchone()[0]
        except Exception:
            edge_count = 0

        try:
            cur.execute("SELECT COUNT(*) FROM pois")
            poi_count = cur.fetchone()[0]
        except Exception:
            poi_count = 0

        # Node type counts
        try:
            cur2 = sqlite3.connect(db_path)
            cur2.row_factory = sqlite3.Row
            all_ids = [r[0] for r in cur2.execute("SELECT id FROM nodes").fetchall()]
            cur2.close()
            coastal = sum(1 for nid in all_ids if get_node_type_int(nid) == 0)
            inland = sum(1 for nid in all_ids if get_node_type_int(nid) == 1)
        except Exception:
            coastal = 0
            inland = 0

        conn.close()

        return {
            "country": md.get("country", ""),
            "name": md.get("name", ""),
            "description": md.get("description", ""),
            "last_update": md.get("last_update_date", ""),
            "tags": json.loads(md.get("tags", "[]")) if isinstance(md.get("tags"), str) else [],
            "bounding_box": json.loads(md.get("bounding_box", "null")) if isinstance(md.get("bounding_box"), str) else None,
            "boundary_geometry": json.loads(md.get("boundary_geometry", "null")) if isinstance(md.get("boundary_geometry"), str) else None,
            "schema_version": md.get("schema_version", 1),
            "contributor": md.get("contributor", ""),
            "url": md.get("url", ""),
            "stats": {
                "nodes": node_count,
                "edges": edge_count,
                "pois": poi_count,
                "coastal_nodes": coastal,
                "inland_nodes": inland,
            },
        }
    except Exception as e:
        print(f"  [WARN] Failed to read {db_path}: {e}", file=sys.stderr)
        return None


def read_metadata_from_gz(gz_path: str, inner_filename: str) -> dict | None:
    """Decompress .sqlite.gz to a temp file and read metadata."""
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="sigidx_")
        sqlite_path = os.path.join(tmpdir, inner_filename)

        with gzip.open(gz_path, 'rb') as f_in:
            with open(sqlite_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)

        if not is_valid_sqlite(sqlite_path):
            print(f"  [WARN] {gz_path} — not a valid SQLite database after decompression", file=sys.stderr)
            return None

        return read_metadata_from_sqlite(sqlite_path)
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def generate_index(regions_dir: str) -> list:
    """Walk regions_dir, read all .sqlite.gz databases, return region entries."""
    entries = []

    # Scan for .sqlite.gz files (primary) and plain .sqlite files (fallback)
    gz_files = sorted(glob.glob(os.path.join(regions_dir, "**", "*.sqlite.gz"), recursive=True))
    sqlite_files = sorted(glob.glob(os.path.join(regions_dir, "**", "*.sqlite"), recursive=True))

    # Build a set of paths already covered by .sqlite.gz to avoid duplicates
    gz_base_set = set()
    for gz in gz_files:
        # e.g. /regions/europe/nl/netherlands.sqlite.gz → /regions/europe/nl/netherlands.sqlite
        base = gz[:-3]  # strip .gz
        gz_base_set.add(base)

    # Process .sqlite.gz first, then .sqlite files not already covered
    processed_set = set()

    def process_entry(filepath: str, is_gz: bool):
        nonlocal entries
        if filepath in processed_set:
            return
        processed_set.add(filepath)

        rel = os.path.relpath(filepath, regions_dir)
        if is_gz:
            region_id = rel.replace(os.sep, "_").replace(".sqlite.gz", "").lower()
            inner_name = os.path.basename(filepath)[:-3]  # strip .gz
        else:
            region_id = rel.replace(os.sep, "_").replace(".sqlite", "").lower()
            inner_name = os.path.basename(filepath)

        print(f"  Scanning {rel}...", file=sys.stderr)

        if is_gz:
            md = read_metadata_from_gz(filepath, inner_name)
        else:
            md = read_metadata_from_sqlite(filepath)

        if md is None:
            print(f"  [SKIP] {rel} — no valid metadata", file=sys.stderr)
            return

        file_size = os.path.getsize(filepath)
        sha = sha256_file(filepath)

        record_path = os.path.join("regions", rel).replace(os.sep, "/")

        entry = {
            "id": region_id,
            "file": record_path,
            "inner_filename": inner_name,
            "sha256": sha,
            "size_bytes": file_size,
            "compression": "gzip" if is_gz else "none",
            "country": md.get("country", ""),
            "name": md.get("name", ""),
            "description": md.get("description", ""),
            "last_update": md.get("last_update", ""),
            "schema_version": md.get("schema_version", 1),
            "tags": md.get("tags", []),
            "contributor": md.get("contributor", ""),
            "url": md.get("url", ""),
            "bounding_box": md.get("bounding_box"),
            "boundary_geometry": md.get("boundary_geometry"),
            "stats": md.get("stats", {}),
        }
        entries.append(entry)

    for gz in gz_files:
        process_entry(gz, is_gz=True)

    for sqlite in sqlite_files:
        # Skip if a .sqlite.gz for this file was already processed
        if sqlite in gz_base_set:
            continue
        process_entry(sqlite, is_gz=False)

    return entries


def render_coverage_map(entries: list, output_path: str):
    """Render a world map showing coverage polygons for each region."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPolygon
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

    if not entries:
        print("  [SKIP] coverage map — no entries", file=sys.stderr)
        return

    colors = [
        "#3b8fd4", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6",
        "#14b8a6", "#ec4899", "#f97316", "#06b6d4", "#a855f7",
        "#84cc16", "#eab308", "#64748b",
    ]

    if USE_CARTOPY:
        fig, ax = plt.subplots(figsize=(14, 8), subplot_kw={"projection": ccrs.PlateCarree()})
        ax.set_global()
        ax.add_feature(cfeature.LAND, color="#e8e8e8", edgecolor="#cccccc", linewidth=0.3)
        ax.add_feature(cfeature.OCEAN, color="#f8f8f8")
        ax.add_feature(cfeature.COASTLINE, linewidth=0.4, edgecolor="#999999")
    else:
        fig, ax = plt.subplots(figsize=(14, 8))
        ax.set_facecolor("#f8f8f8")

    for i, entry in enumerate(entries):
        geom_data = entry.get("boundary_geometry")
        if not geom_data:
            bbox = entry.get("bounding_box")
            if bbox:
                coords = [
                    [bbox["min_lon"], bbox["min_lat"]],
                    [bbox["max_lon"], bbox["min_lat"]],
                    [bbox["max_lon"], bbox["max_lat"]],
                    [bbox["min_lon"], bbox["max_lat"]],
                    [bbox["min_lon"], bbox["min_lat"]],
                ]
                poly = MplPolygon(coords, closed=True, facecolor=colors[i % len(colors)],
                                  edgecolor=colors[i % len(colors)], linewidth=1.5, alpha=0.3,
                                  label=entry.get("name", entry["id"]))
                ax.add_patch(poly)
            continue

        try:
            geom = shape(geom_data)
        except Exception:
            continue

        if geom.is_empty:
            continue

        color = colors[i % len(colors)]

        if geom.geom_type == "Polygon":
            xs, ys = geom.exterior.xy
            if USE_CARTOPY:
                ax.fill(xs, ys, color=color, alpha=0.3, edgecolor=color, linewidth=1.5,
                        transform=ccrs.PlateCarree(), label=entry.get("name", entry["id"]))
            else:
                ax.fill(xs, ys, color=color, alpha=0.3, edgecolor=color, linewidth=1.5,
                        label=entry.get("name", entry["id"]))
        elif geom.geom_type == "MultiPolygon":
            for poly in geom.geoms:
                xs, ys = poly.exterior.xy
                if USE_CARTOPY:
                    ax.fill(xs, ys, color=color, alpha=0.3, edgecolor=color, linewidth=1.5,
                            transform=ccrs.PlateCarree())
                else:
                    ax.fill(xs, ys, color=color, alpha=0.3, edgecolor=color, linewidth=1.5)

    from matplotlib.patches import Patch
    legend_patches = []
    seen_names = set()
    for i, entry in enumerate(entries):
        name = entry.get("name", entry["id"])
        if name not in seen_names:
            seen_names.add(name)
            legend_patches.append(Patch(color=colors[i % len(colors)], alpha=0.5, label=name))

    if legend_patches:
        ax.legend(handles=legend_patches, loc="lower left", framealpha=0.85,
                  fontsize=8, ncol=2)

    if USE_CARTOPY:
        ax.set_title("SignalK Routing Data — Coverage", fontsize=14, pad=16)
    else:
        ax.set_xlim(-180, 180)
        ax.set_ylim(-90, 90)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title("SignalK Routing Data — Coverage", fontsize=14, pad=16)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Coverage map saved to {output_path}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Generate index.json and coverage map for the signalk-router-data repository."
    )
    parser.add_argument("--regions-dir", default="./regions",
                        help="Path to regions directory containing .sqlite.gz files (default: ./regions)")
    parser.add_argument("--output-dir", default=".",
                        help="Output directory for index.json and coverage-map.png (default: .)")
    args = parser.parse_args()

    regions_dir = os.path.abspath(args.regions_dir)
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.isdir(regions_dir):
        os.makedirs(regions_dir, exist_ok=True)
        print(f"Created empty regions directory: {regions_dir}", file=sys.stderr)

    os.makedirs(output_dir, exist_ok=True)

    print(f"Scanning {regions_dir} for .sqlite.gz files...", file=sys.stderr)
    entries = generate_index(regions_dir)

    index = {
        "version": 2,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "region_count": len(entries),
        "regions": entries,
    }

    index_path = os.path.join(output_dir, "index.json")
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    print(f"  index.json written with {len(entries)} regions ({index_path})", file=sys.stderr)

    map_path = os.path.join(output_dir, "coverage-map.png")
    render_coverage_map(entries, map_path)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
