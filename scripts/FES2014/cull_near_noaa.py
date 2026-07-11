#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Cull FES2014-derived UTCEF current features near NOAA CO-OPS stations.

The NOAA UTCEF station files (scripts/NOAA/) carry the authoritative harmonic
predictions for US bays, channels and estuaries — exactly where the coarse
FES2014 grid is least reliable (land contamination, unresolved channels).
Keeping both puts two conflicting stations on the same map spot, so this
script removes FES `harmonic_constituents_currents` features within
RADIUS_KM of any NOAA station, POINT-LEVEL: offshore FES coverage between
NOAA stations is untouched (deliberately NOT whole-region removal).

Idempotent: culled files record what was applied in `metadata.culling`
(non-normative, ignored by engines) and are re-culled from their remaining
features on re-run. IMPORTANT: regenerating FES files from scratch
(generate_fes2024_utcef_batch.py) resurrects the culled points — re-run this
script afterwards.

Usage:
  python3 cull_near_noaa.py [--regions-dir ../../regions] [--radius-km 15] [--dry-run]
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
    """0.5°-bucketed point index for radius queries against NOAA stations."""

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


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Remove FES current features near NOAA stations")
    ap.add_argument("--regions-dir", default=os.path.normpath(os.path.join(here, "..", "..", "regions")))
    ap.add_argument("--radius-km", type=float, default=15.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.regions_dir, "**/*.utcef"), recursive=True))

    # Pass 1: index every NOAA station.
    index = StationIndex()
    noaa_count = 0
    for path in files:
        _, payload = load_payload(path)
        if (payload.get("metadata", {}).get("catalog") or {}).get("source") != "noaa":
            continue
        for f in payload["dataset"]["features"]:
            lon, lat = f["geometry"]["coordinates"][:2]
            index.add(lat, lon)
            noaa_count += 1
    if noaa_count == 0:
        print("No NOAA stations found — nothing to cull against.")
        return 1
    print(f"Indexed {noaa_count} NOAA stations; culling radius {args.radius_km} km\n")

    total_removed = 0
    for path in files:
        inner, payload = load_payload(path)
        meta = payload.get("metadata", {})
        if (meta.get("catalog") or {}).get("source") == "noaa":
            continue

        kept, removed = [], 0
        for f in payload["dataset"]["features"]:
            props = f.get("properties", {})
            if props.get("prediction_method") == "harmonic_constituents_currents":
                lon, lat = f["geometry"]["coordinates"][:2]
                if index.any_within(lat, lon, args.radius_km):
                    removed += 1
                    continue
            kept.append(f)

        if removed == 0:
            continue
        total_removed += removed
        rel = os.path.relpath(path, args.regions_dir)
        print(f"  {rel}: removed {removed}, kept {len(kept)} features")
        if args.dry_run:
            continue
        payload["dataset"]["features"] = kept
        meta["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["culling"] = {
            "near_source": "noaa",
            "radius_km": args.radius_km,
            "removed_features": removed + int((meta.get("culling") or {}).get("removed_features") or 0),
        }
        save_payload(path, inner, payload)

    print(f"\n{'Would remove' if args.dry_run else 'Removed'} {total_removed} FES current features near NOAA stations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
