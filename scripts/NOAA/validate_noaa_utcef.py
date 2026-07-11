#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Validate generated NOAA UTCEF files against NOAA's own predictions.

For a sample of stations from a generated `.utcef` file, this harness:
  1. predicts the current vector from the file's harmonic constituents using a
     Python port of the consuming engine's astronomy (signalk-tidal-currents
     src/astro.ts — same IHO/Schureman catalog, V0 and nodal corrections);
  2. fetches NOAA's official predictions for the SAME station/bin from
     `datagetter?product=currents_predictions` (signed speed along the mean
     flood axis, cm/s);
  3. projects the predicted (u, v) onto NOAA's mean flood direction and
     reports RMSE / bias / correlation, plus the total amplitude of any
     constituents the plugin engine does not (yet) support.

A conversion or phase-convention bug shows up as a large RMSE or a phase
shift (low correlation); missing-constituent error is bounded by the reported
dropped amplitude.

Usage:
  python3 validate_noaa_utcef.py ../../regions/atlantic/noaa_us_east_coast.utcef \
      [--stations 8] [--days 14] [--seed 42] [--station-id NOAA_ACT1616_1]
"""
import argparse
import json
import math
import os
import random
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

from noaa_astro import CONSTITUENTS, predict_uv

DATAGETTER = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
USER_AGENT = "signalk-router-data/noaa-validate (https://github.com/marcelrv/signalk-router-data)"

# ---------------------------------------------------------------------------
# NOAA ground truth
# ---------------------------------------------------------------------------

def fetch_noaa_predictions(station_id: str, nbin: int, begin: datetime, hours: int) -> list:
    params = {
        "product": "currents_predictions",
        "station": station_id,
        "begin_date": begin.strftime("%Y%m%d"),
        "range": str(hours),
        "interval": "60",
        "units": "metric",
        "time_zone": "gmt",
        "format": "json",
        "bin": str(nbin),
    }
    url = f"{DATAGETTER}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            doc = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        # Some stations have constituents but no published predictions
        # (datagetter answers 400) — report as skipped, not a crash.
        return []
    return doc.get("current_predictions", {}).get("cp") or []


def load_utcef(path: str) -> dict:
    with zipfile.ZipFile(path) as z:
        inner = [n for n in z.namelist() if n.endswith(".json")][0]
        return json.loads(z.read(inner))


def validate_station(feature: dict, begin: datetime, days: int) -> dict | None:
    props = feature["properties"]
    noaa = props.get("noaa") or {}
    sid, nbin = noaa.get("station_id"), noaa.get("bin")
    if not sid or not nbin:
        return None
    cp = fetch_noaa_predictions(sid, nbin, begin, days * 24)
    if not cp:
        return None

    hc = props["harmonic_constituents"]
    dropped = sorted(
        (n for n in hc if n not in CONSTITUENTS),
        key=lambda n: -(hc[n]["u_amplitude"] ** 2 + hc[n]["v_amplitude"] ** 2),
    )
    dropped_amp = sum(math.hypot(hc[n]["u_amplitude"], hc[n]["v_amplitude"]) for n in dropped)

    # Subordinate stations get only max/slack EVENT rows from datagetter (the
    # rows carry a "Type"); compare peak values at flood/ebb events and track
    # our residual speed at NOAA's slack instants separately.
    events_mode = "Type" in cp[0]
    errs, ours, theirs, slack_abs = [], [], [], []
    for row in cp:
        if row.get("meanFloodDir") is None:
            continue  # some stations' rows carry no axis — nothing to project onto
        t = datetime.strptime(row["Time"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        flood = math.radians(float(row["meanFloodDir"]))
        u, v = predict_uv(hc, t.timestamp() * 1000.0, props.get("mean_offset"))
        mine = float(u * math.sin(flood) + v * math.cos(flood)) * 100.0  # m/s -> cm/s
        if events_mode and row.get("Type") == "slack":
            slack_abs.append(abs(mine))
            continue
        noaa_v = float(row["Velocity_Major"])
        ours.append(mine)
        theirs.append(noaa_v)
        errs.append(mine - noaa_v)
    if not errs:
        return None

    n = len(errs)
    rmse = math.sqrt(sum(e * e for e in errs) / n)
    bias = sum(errs) / n
    mo, mt = sum(ours) / n, sum(theirs) / n
    cov = sum((a - mo) * (b - mt) for a, b in zip(ours, theirs))
    var = math.sqrt(sum((a - mo) ** 2 for a in ours) * sum((b - mt) ** 2 for b in theirs))
    corr = cov / var if var > 0 else 0.0
    peak = max(abs(x) for x in theirs)
    return {
        "id": feature["id"],
        "name": props.get("station_name", ""),
        "samples": n,
        "peak_cms": peak,
        "rmse_cms": rmse,
        "rmse_pct_of_peak": 100.0 * rmse / peak if peak > 0 else 0.0,
        "bias_cms": bias,
        "corr": corr,
        "dropped_constituents": dropped,
        "dropped_amp_cms": dropped_amp * 100.0,
        "events_mode": events_mode,
        "slack_abs_mean_cms": sum(slack_abs) / len(slack_abs) if slack_abs else None,
    }


def main():
    ap = argparse.ArgumentParser(description="Validate NOAA UTCEF predictions against NOAA datagetter")
    ap.add_argument("utcef", nargs="+", help=".utcef file(s) to validate")
    ap.add_argument("--stations", type=int, default=8, help="random stations per file")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--station-id", action="append", default=[], help="validate specific Feature.id(s)")
    args = ap.parse_args()

    begin = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rng = random.Random(args.seed)
    worst = 0.0
    for path in args.utcef:
        doc = load_utcef(path)
        features = doc["dataset"]["features"]
        if args.station_id:
            sample = [f for f in features if f["id"] in args.station_id]
        else:
            sample = rng.sample(features, min(args.stations, len(features)))
        print(f"\n== {os.path.basename(path)} ({len(features)} stations, validating {len(sample)}, "
              f"{args.days} days hourly from {begin:%Y-%m-%d}) ==")
        for f in sample:
            r = validate_station(f, begin, args.days)
            if not r:
                print(f"  {f['id']}: no NOAA predictions returned — skipped")
                continue
            worst = max(worst, r["rmse_pct_of_peak"])
            drop = f" dropped[{','.join(r['dropped_constituents'])}]={r['dropped_amp_cms']:.1f}cm/s" if r["dropped_constituents"] else ""
            slack = f" slack|v|={r['slack_abs_mean_cms']:.1f}cm/s" if r["slack_abs_mean_cms"] is not None else ""
            mode = "peaks" if r["events_mode"] else "RMSE"
            print(
                f"  {r['id']:<22} peak {r['peak_cms']:6.1f}  {mode} {r['rmse_cms']:5.1f} cm/s "
                f"({r['rmse_pct_of_peak']:4.1f}% of peak)  bias {r['bias_cms']:+5.1f}  corr {r['corr']:.4f}"
                f"{slack}{drop}  — {r['name']}"
            )
    print(f"\nWorst RMSE: {worst:.1f}% of peak "
          f"({'PASS' if worst < 10 else 'CHECK CONVERSION'} — phase/sign bugs typically show >50%)")


if __name__ == "__main__":
    sys.exit(main())
