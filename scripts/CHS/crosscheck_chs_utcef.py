#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Cross-check CHS stations against other UTCEF files nearby (NOAA, FES2014).

For each CHS station this finds the nearest current feature of every other
source within --radius-km and predicts both with the plugin-engine port
(scripts/NOAA/noaa_astro.py) over the same window. Projected onto the CHS
station's flood axis, it reports:

  corr   correlation of the two signed axis-speed curves
  lag    time shift (min) that maximises the correlation; positive = the
         neighbour's tide turns LATER than CHS
  ratio  RMS speed ratio neighbour / CHS
  dθ     angle between the two stations' principal axes (deg, 0..90)

Nearby stations need not agree in strength (a rapid vs. the open channel
next to it), but a phase-convention, unit or direction bug shows up
plainly: corr near −1 (flood/ebb swapped), a lag of ~6 h, a ratio of
~0.51 or ~1.94 (knots vs m/s), or a large dθ between co-located stations.
Run it BEFORE cull_near_stations.py, which removes those neighbours.

Usage:
  python3 crosscheck_chs_utcef.py [--regions-dir ../../regions] [--radius-km 15]
      [--start 2027-01-01] [--days 15]
"""
import argparse
import glob
import json
import math
import os
import sys
import zipfile
from datetime import datetime, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "NOAA"))
sys.path.insert(0, os.path.join(HERE, "..", "FES2014"))
from noaa_astro import predict_uv  # noqa: E402
from cull_near_stations import haversine_km, source_of  # noqa: E402

KN_TO_MS = 1852.0 / 3600.0
STEP_MIN = 10
MAX_LAG_MIN = 360


def load(path):
    with zipfile.ZipFile(path) as z:
        inner = [n for n in z.namelist() if n.endswith(".json")][0]
        return json.loads(z.read(inner))


def principal_axis(u, v) -> float:
    """Principal axis of the (u, v) cloud as a bearing in degrees (0..180)."""
    cov = np.cov(np.vstack([u, v]))
    w, vec = np.linalg.eigh(cov)
    e = vec[:, np.argmax(w)]
    return math.degrees(math.atan2(e[0], e[1])) % 180.0


def best_lag(a, b):
    """Lag (min) of b relative to a maximising the correlation, and that corr."""
    steps = MAX_LAG_MIN // STEP_MIN
    best = (0, -2.0)
    for k in range(-steps, steps + 1):
        if k >= 0:
            x, y = a[: len(a) - k], b[k:]
        else:
            x, y = a[-k:], b[: len(b) + k]
        c = float(np.corrcoef(x, y)[0, 1])
        if c > best[1]:
            best = (k * STEP_MIN, c)
    return best


def main():
    ap = argparse.ArgumentParser(description="Cross-check CHS stations against nearby UTCEF features")
    ap.add_argument("--regions-dir", default=os.path.normpath(os.path.join(HERE, "..", "..", "regions")))
    ap.add_argument("--radius-km", type=float, default=15.0)
    ap.add_argument("--start", default="2027-01-01")
    ap.add_argument("--days", type=int, default=15)
    args = ap.parse_args()

    t0 = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000.0
    times = t0 + np.arange(0, args.days * 1440 // STEP_MIN) * STEP_MIN * 60000.0

    chs_feats, others = [], []
    for path in sorted(glob.glob(os.path.join(args.regions_dir, "**/*.utcef"), recursive=True)):
        payload = load(path)
        src = source_of(payload.get("metadata", {}))
        bbox = payload["metadata"]["region"]["bbox"]
        if src != "chs" and not (bbox[1] < 56 and bbox[3] > 43 and (bbox[0] < -55 or bbox[0] > bbox[2])):
            continue  # nowhere near Canada
        for f in payload["dataset"]["features"]:
            if f["properties"].get("prediction_method") != "harmonic_constituents_currents":
                continue
            (chs_feats if src == "chs" else others).append((src, os.path.basename(path), f))

    print(f"{len(chs_feats)} CHS stations, {len(others)} candidate neighbours; window {args.start} + {args.days} d\n")
    hdr = f"{'CHS station':30s} {'src':7s} {'neighbour':32s} {'km':>5s} {'corr':>6s} {'lag':>5s} {'ratio':>6s} {'dθ':>4s}  note"
    print(hdr)
    print("-" * len(hdr))
    suspicious = 0
    for _, _, cf in chs_feats:
        clon, clat = cf["geometry"]["coordinates"]
        p = cf["properties"]
        flood = float(p["chs"].get("flood_dir") or 0.0)
        cu, cv = predict_uv(p["harmonic_constituents"], times, p.get("mean_offset"))
        fr = math.radians(flood)
        ca = (cu * math.sin(fr) + cv * math.cos(fr)) / KN_TO_MS
        c_axis = principal_axis(cu, cv)

        nearest = {}
        for src, fname, f in others:
            lon, lat = f["geometry"]["coordinates"]
            if abs(lat - clat) > 0.3:
                continue
            d = haversine_km(clat, clon, lat, lon)
            if d <= args.radius_km and (src not in nearest or d < nearest[src][0]):
                nearest[src] = (d, fname, f)
        label = f"{p['chs']['station_code']} {p['station_name']}"[:30]
        if not nearest:
            print(f"{label:30s} (no neighbour within {args.radius_km:g} km)")
            continue
        for src, (d, fname, f) in sorted(nearest.items()):
            q = f["properties"]
            nu, nv = predict_uv(q["harmonic_constituents"], times, q.get("mean_offset"))
            na = (nu * math.sin(fr) + nv * math.cos(fr)) / KN_TO_MS
            corr0 = float(np.corrcoef(ca, na)[0, 1])
            lag, corr = best_lag(ca, na)
            ratio = float(np.sqrt(np.mean(na ** 2)) / max(1e-9, np.sqrt(np.mean(ca ** 2))))
            dth = abs(principal_axis(nu, nv) - c_axis) % 180.0
            dth = min(dth, 180.0 - dth)
            notes = []
            if corr0 < -0.5:
                notes.append("ANTI-PHASE (flood/ebb swapped?)")
            if abs(lag) >= 120:
                notes.append("large lag")
            if d <= 3 and (dth > 30 or not 0.6 < ratio < 1.6 or abs(lag) > 45):
                notes.append("co-located but disagree")
            if notes:
                suspicious += 1
            name = (q.get("station_name") or f["id"])[:32]
            print(f"{label:30s} {src:7s} {name:32s} {d:5.1f} {corr:6.2f} {lag:5d} {ratio:6.2f} {dth:4.0f}  {'; '.join(notes)}")
    print(f"\n{suspicious} comparison(s) flagged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
