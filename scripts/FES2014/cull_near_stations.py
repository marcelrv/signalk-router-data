#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Cull UTCEF current features near authoritative station files.

The NOAA and CHS UTCEF station files (scripts/NOAA/, scripts/CHS/) carry
official harmonic predictions for bays, channels, passes and rapids —
exactly where the coarse FES2014 grid is least reliable (land contamination,
unresolved channels). Keeping both puts two conflicting stations on the same
map spot, so this script removes `harmonic_constituents_currents` features
of the TARGET sources within RADIUS_KM of any station of the NEAR sources,
POINT-LEVEL: coverage between stations is untouched (deliberately NOT
whole-region removal).

A file's source is its `metadata.catalog.source`; files without a catalog
block are FES2014 (`fes2014`), as in scripts/tide-current/sources/utcef_regions.py.

Typical runs:
  # FES grid points near NOAA stations (15 km). Not near CHS: CHS has only
  # ~30 sparse point stations, and culling around them would leave holes in
  # the FES area coverage.
  python3 cull_near_stations.py --near noaa
  # NOAA copies of Canadian stations: CHS is the authority in Canadian waters
  python3 cull_near_stations.py --near chs --targets noaa --radius-km 2

Idempotent: culled files record what was applied in `metadata.culling`
(non-normative, ignored by engines) and are re-culled from their remaining
features on re-run. IMPORTANT: regenerating FES or NOAA files from scratch
resurrects the culled points — re-run this script afterwards.

Usage:
  python3 cull_near_stations.py [--regions-dir ../../regions] [--near noaa]
      [--targets fes2014] [--radius-km 15] [--dry-run]
"""
import argparse
import glob
import json
import math
import os
import sys
import zipfile
from datetime import datetime, timezone

EARTH_R_KM = 6371.0


def haversine_km(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(min(1.0, a)))


def load_payload(path):
    with zipfile.ZipFile(path) as z:
        inner = [n for n in z.namelist() if n.endswith(".json")][0]
        return inner, json.loads(z.read(inner))


def save_payload(path, inner, payload):
    text = json.dumps(payload, indent=1, ensure_ascii=False)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(inner, text)


class StationIndex:
    """0.5°-bucketed point index for radius queries against station points."""

    BUCKET_DEG = 0.5

    def __init__(self):
        self.buckets = {}

    def add(self, lat, lon):
        key = (int(lat // self.BUCKET_DEG), int(lon // self.BUCKET_DEG))
        self.buckets.setdefault(key, []).append((lat, lon))

    def any_within(self, lat, lon, radius_km):
        # Radius in bucket units (generous: 1° lat ≈ 111 km, lon shrinks with cos).
        span = int(radius_km / (111.0 * self.BUCKET_DEG)) + 1
        bi, bj = int(lat // self.BUCKET_DEG), int(lon // self.BUCKET_DEG)
        for i in range(bi - span, bi + span + 1):
            for j in range(bj - span, bj + span + 1):
                for slat, slon in self.buckets.get((i, j), ()):
                    if haversine_km(lat, lon, slat, slon) <= radius_km:
                        return True
        return False


def source_of(meta: dict) -> str:
    return (meta.get("catalog") or {}).get("source") or "fes2014"


def merge_culling(prev: dict | None, near: list, radius_km: float, removed: int) -> dict:
    """Fold a new culling pass into the file's existing metadata.culling."""
    prev = prev or {}
    sources = [s for s in str(prev.get("near_source") or "").split(",") if s]
    for s in near:
        if s not in sources:
            sources.append(s)
    return {
        "near_source": ",".join(sources),
        "radius_km": max(radius_km, float(prev.get("radius_km") or 0.0)),
        "removed_features": removed + int(prev.get("removed_features") or 0),
    }


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Remove current features near authoritative stations")
    ap.add_argument("--regions-dir", default=os.path.normpath(os.path.join(here, "..", "..", "regions")))
    ap.add_argument("--near", default="noaa", help="comma-separated station sources to cull around")
    ap.add_argument("--targets", default="fes2014", help="comma-separated sources whose features are culled")
    ap.add_argument("--radius-km", type=float, default=15.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    near = [s.strip() for s in args.near.split(",") if s.strip()]
    targets = {s.strip() for s in args.targets.split(",") if s.strip()}
    if targets & set(near):
        print("A source cannot be both --near and --targets.")
        return 2

    files = sorted(glob.glob(os.path.join(args.regions_dir, "**/*.utcef"), recursive=True))

    # Pass 1: index every station of the near sources.
    index = StationIndex()
    station_count = 0
    for path in files:
        _, payload = load_payload(path)
        if source_of(payload.get("metadata", {})) not in near:
            continue
        for f in payload["dataset"]["features"]:
            lon, lat = f["geometry"]["coordinates"][:2]
            index.add(lat, lon)
            station_count += 1
    if station_count == 0:
        print(f"No {','.join(near)} stations found — nothing to cull against.")
        return 1
    print(f"Indexed {station_count} {','.join(near)} stations; culling {','.join(sorted(targets))} within {args.radius_km} km\n")

    total_removed = 0
    for path in files:
        inner, payload = load_payload(path)
        meta = payload.get("metadata", {})
        if source_of(meta) not in targets:
            continue

        kept, removed = [], []
        for f in payload["dataset"]["features"]:
            props = f.get("properties", {})
            if props.get("prediction_method") == "harmonic_constituents_currents":
                lon, lat = f["geometry"]["coordinates"][:2]
                if index.any_within(lat, lon, args.radius_km):
                    removed.append(f.get("id"))
                    continue
            kept.append(f)

        if not removed:
            continue
        total_removed += len(removed)
        rel = os.path.relpath(path, args.regions_dir)
        detail = f" ({', '.join(map(str, removed))})" if len(removed) <= 5 else ""
        print(f"  {rel}: removed {len(removed)}, kept {len(kept)} features{detail}")
        if args.dry_run:
            continue
        payload["dataset"]["features"] = kept
        meta["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["culling"] = merge_culling(meta.get("culling"), near, args.radius_km, len(removed))
        save_payload(path, inner, payload)

    print(f"\n{'Would remove' if args.dry_run else 'Removed'} {total_removed} features near {','.join(near)} stations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
