#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""
Generate index.json and coverage-map.png for the signalk-router-data repository.

Merges two kinds of input under regions/ and produces:
  - routing-index.json — machine-readable catalog of all available databases
  - coverage-map.png   — world map showing coverage areas for README

Inputs, in precedence order:

  1. *.index.json descriptors — the normal case. The databases themselves are
     published as assets on the rolling `routing-databases-latest` GitHub
     Release (same pattern as the tide/current GRIB releases), so the repo
     holds only these few-KB descriptors and git history never grows by a
     10 MB blob per rebuild. Written by the pipeline's deploy_to_data_repo.py.

  2. *.sqlite.gz / *.sqlite files physically present under regions/ — a
     development convenience, and the path used before release hosting.
     Decompressed to a temp location so metadata and stats can be read.

A descriptor always wins over a local file for the same region id: the
descriptor describes the asset that consumers will actually download.

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

# Rolling release that carries the routing database assets. Assets are
# overwritten in place (gh release upload --clobber), so republishing a region
# costs no repo growth and no new git objects — see specs/routing-database-catalog.md.
RELEASE_TAG = "routing-databases-latest"
RELEASE_DOWNLOAD_BASE = (
    f"https://github.com/marcelrv/signalk-router-data/releases/download/{RELEASE_TAG}"
)

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


def generate_index(regions_dir: str, skip_ids: set | None = None) -> list:
    """Walk regions_dir, read all .sqlite.gz databases, return region entries.

    Ids in skip_ids are already covered by a descriptor; skipping them avoids
    decompressing and hashing a multi-hundred-MB database whose entry is about
    to be discarded in favour of the descriptor's.
    """
    skip_ids = skip_ids or set()
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

        if region_id in skip_ids:
            print(f"  [SKIP] {rel} — superseded by its .index.json descriptor", file=sys.stderr)
            return

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
        asset_name = os.path.basename(filepath)

        entry = {
            "id": region_id,
            "filename": asset_name,
            # No download_url: this database exists only as a file in the
            # repository (dev checkout, or a region not yet published to the
            # release). Consumers resolve `file` against the catalog's own
            # directory, as they always have.
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


DESCRIPTOR_REQUIRED_FIELDS = ("id", "filename", "sha256", "size_bytes", "name")


def load_descriptors(regions_dir: str) -> list:
    """Read every *.index.json descriptor under regions_dir into catalog entries.

    A descriptor is the region's catalog entry as written by the pipeline at
    deploy time — it already carries the metadata, stats and checksums read
    from the database, so the database itself never has to be present here.
    """
    entries = []
    paths = sorted(glob.glob(os.path.join(regions_dir, "**", "*.index.json"), recursive=True))

    for path in paths:
        rel = os.path.relpath(path, regions_dir)
        try:
            with open(path) as f:
                entry = json.load(f)
        except Exception as e:
            print(f"  [WARN] {rel} — unreadable descriptor: {e}", file=sys.stderr)
            continue

        missing = [f for f in DESCRIPTOR_REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            print(f"  [SKIP] {rel} — descriptor missing {', '.join(missing)}", file=sys.stderr)
            continue

        # The descriptor records which release tag holds the asset; the URL is
        # derived here so that moving the release only changes this script.
        tag = entry.pop("release_tag", RELEASE_TAG)
        base = (
            RELEASE_DOWNLOAD_BASE if tag == RELEASE_TAG
            else f"https://github.com/marcelrv/signalk-router-data/releases/download/{tag}"
        )
        entry["download_url"] = f"{base}/{entry['filename']}"
        # `file` would be a dangling repo path for a release-hosted database —
        # a consumer resolving it against the catalog directory gets a 404.
        entry.pop("file", None)

        print(f"  Descriptor {rel} -> {entry['download_url']}", file=sys.stderr)
        entries.append(entry)

    return entries


def merge_entries(descriptors: list, scanned: list) -> list:
    """Combine descriptor and scanned entries, descriptors winning on id."""
    merged = {e["id"]: e for e in scanned}
    merged.update({e["id"]: e for e in descriptors})
    return [merged[k] for k in sorted(merged)]


def render_coverage_map(entries: list, output_path: str):
    """Render a coverage map with automatic zoom to the data extent."""
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

    # Compute combined bounds of all entries
    min_lons, max_lons, min_lats, max_lats = [], [], [], []
    for entry in entries:
        bbox = entry.get("bounding_box")
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

    # Add margin — at least 2°, otherwise 20% of span
    margin_lon = max(2.0, lon_span * 0.2)
    margin_lat = max(2.0, lat_span * 0.2)

    show_global = (lon_span > 120 or lat_span > 60)

    colors = [
        "#3b8fd4", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6",
        "#14b8a6", "#ec4899", "#f97316", "#06b6d4", "#a855f7",
        "#84cc16", "#eab308", "#64748b",
    ]

    if USE_CARTOPY:
        fig, ax = plt.subplots(figsize=(14, 8), subplot_kw={"projection": ccrs.PlateCarree()})
        if show_global:
            ax.set_global()
        else:
            ax.set_extent([
                world_min_lon - margin_lon,
                world_max_lon + margin_lon,
                world_min_lat - margin_lat,
                world_max_lat + margin_lat,
            ], crs=ccrs.PlateCarree())
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

    ax.set_title("SignalK Routing Data — Coverage", fontsize=14, pad=16)

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

    print(f"Scanning {regions_dir} for descriptors and database files...", file=sys.stderr)
    descriptors = load_descriptors(regions_dir)
    entries = merge_entries(
        descriptors,
        generate_index(regions_dir, skip_ids={d["id"] for d in descriptors}),
    )

    index = {
        "catalog_schema_version": "1.1.0",
        "version": 2,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "region_count": len(entries),
        "regions": entries,
    }

    index_path = os.path.join(output_dir, "routing-index.json")
    
    needs_update = True
    if os.path.exists(index_path):
        with open(index_path) as f:
            existing = json.load(f)
        existing_data = {k: v for k, v in existing.items() if k != "generated"}
        new_data = {k: v for k, v in index.items() if k != "generated"}
        if existing_data == new_data:
            needs_update = False

    if needs_update:
        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)
        print(f"  routing-index.json written with {len(entries)} regions ({index_path})", file=sys.stderr)
    else:
        print(f"  routing-index.json unchanged", file=sys.stderr)

    map_path = os.path.join(output_dir, "coverage-map.png")
    render_coverage_map(entries, map_path)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
