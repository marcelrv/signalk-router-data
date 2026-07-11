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

DATAGETTER = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
USER_AGENT = "signalk-router-data/noaa-validate (https://github.com/marcelrv/signalk-router-data)"

# ---------------------------------------------------------------------------
# Python port of signalk-tidal-currents src/astro.ts (keep in sync!)
# ---------------------------------------------------------------------------

J2000_MS = 946728000000.0   # 2000-01-01 12:00 UTC
EPOCH_MS = 946684800000.0   # 2000-01-01 00:00 UTC


def astronomical_args(time_ms: float) -> dict:
    T = (time_ms - J2000_MS) / 86400000.0 / 36525.0
    ut_hours = (time_ms - EPOCH_MS) / 3600000.0
    return {
        "T15": 15.0 * ut_hours,
        "s": 218.3164477 + 481267.88123421 * T - 0.0015786 * T * T + (T ** 3) / 538841.0,
        "h": 280.4664567 + 36000.76982779 * T + 0.0003032028 * T * T,
        "p": 83.3532465 + 4069.0137287 * T - 0.0103200 * T * T,
        "N": 125.0445479 - 1934.1362891 * T + 0.0020754 * T * T,
        "p1": 282.9373 + 1.7195366 * T + 0.0004597 * T * T,
    }


#                 speed        T15  s   h   p  p1 const nodal
CONSTITUENTS = {
    "M2":      (28.9841042, 2, -2,  2,  0,  0,   0, "M2"),
    "S2":      (30.0000000, 2,  0,  0,  0,  0,   0, "none"),
    "N2":      (28.4397295, 2, -3,  2,  1,  0,   0, "M2"),
    "K2":      (30.0821373, 2,  0,  2,  0,  0,   0, "K2"),
    "NU2":     (28.5125831, 2, -3,  4, -1,  0,   0, "M2"),
    "MU2":     (27.9682084, 2, -4,  4,  0,  0,   0, "M2"),
    "2N2":     (27.8953548, 2, -4,  2,  2,  0,   0, "M2"),
    "L2":      (29.5284789, 2, -1,  2, -1,  0, 180, "M2"),
    "T2":      (29.9589333, 2,  0, -1,  0,  1,   0, "none"),
    "R2":      (30.0410667, 2,  0,  1,  0, -1, 180, "none"),
    "LAMBDA2": (29.4556253, 2, -1,  0,  1,  0, 180, "M2"),
    "2SM2":    (31.0158958, 2,  2, -2,  0,  0,   0, "M2inv"),
    "K1":      (15.0410686, 1,  0,  1,  0,  0,  90, "K1"),
    "O1":      (13.9430356, 1, -2,  1,  0,  0, -90, "O1"),
    "P1":      (14.9589314, 1,  0, -1,  0,  0, -90, "none"),
    "Q1":      (13.3986609, 1, -3,  1,  1,  0, -90, "O1"),
    "S1":      (15.0000000, 1,  0,  0,  0,  0,   0, "none"),
    "J1":      (15.5854433, 1,  1,  1, -1,  0,  90, "J1"),
    "2Q1":     (12.8542862, 1, -4,  1,  2,  0, -90, "O1"),
    "RHO1":    (13.4715145, 1, -3,  3, -1,  0, -90, "O1"),
    "MM":      (0.5443747,  0,  1,  0, -1,  0,   0, "Mm"),
    "MF":      (1.0980331,  0,  2,  0,  0,  0,   0, "Mf"),
    "SSA":     (0.0821373,  0,  0,  2,  0,  0,   0, "none"),
    "SA":      (0.0410686,  0,  0,  1,  0,  0,   0, "none"),
    "MSF":     (1.0158958,  0,  2, -2,  0,  0,   0, "M2inv"),
    "M3":      (43.4761563, 3, -3,  3,  0,  0,   0, "M2^2"),
    "MK3":     (44.0251729, 3, -2,  3,  0,  0,  90, "MK3"),
    "2MK3":    (42.9271398, 3, -4,  3,  0,  0, -90, "2MK3"),
    "M4":      (57.9682084, 4, -4,  4,  0,  0,   0, "M2^2"),
    "MS4":     (58.9841042, 4, -2,  2,  0,  0,   0, "M2"),
    "MN4":     (57.4238337, 4, -5,  4,  1,  0,   0, "M2^2"),
    "S4":      (60.0000000, 4,  0,  0,  0,  0,   0, "none"),
    "M6":      (86.9523127, 6, -6,  6,  0,  0,   0, "M2^2"),
    "S6":      (90.0000000, 6,  0,  0,  0,  0,   0, "none"),
    "M8":      (115.9364166, 8, -8, 8,  0,  0,   0, "M2^2"),
    # M1 and OO1 stay unsupported (need full Schureman I/ξ/ν nodal theory).
}


def equilibrium_arg(a: dict, name: str) -> float:
    _, t15, s, h, p, p1, konst, _ = CONSTITUENTS[name]
    v = t15 * a["T15"] + s * a["s"] + h * a["h"] + p * a["p"] + p1 * a["p1"] + konst
    return v % 360.0


def node_factors(a: dict, name: str):
    family = CONSTITUENTS[name][7]
    N = math.radians(a["N"])
    cN, c2N, c3N = math.cos(N), math.cos(2 * N), math.cos(3 * N)
    sN, s2N, s3N = math.sin(N), math.sin(2 * N), math.sin(3 * N)
    if family == "none":
        return 1.0, 0.0
    if family == "Mm":
        return 1.0 - 0.1300 * cN + 0.0013 * c2N, 0.0
    if family == "Mf":
        return 1.0429 + 0.4135 * cN - 0.0040 * c2N, -23.74 * sN + 2.68 * s2N - 0.38 * s3N
    if family == "O1":
        return (1.0089 + 0.1871 * cN - 0.0147 * c2N + 0.0014 * c3N,
                10.80 * sN - 1.34 * s2N + 0.19 * s3N)
    if family == "K1":
        return (1.0060 + 0.1150 * cN - 0.0088 * c2N + 0.0006 * c3N,
                -8.86 * sN + 0.68 * s2N - 0.07 * s3N)
    if family == "J1":
        return (1.0129 + 0.1676 * cN - 0.0170 * c2N + 0.0016 * c3N,
                -12.94 * sN + 1.34 * s2N - 0.19 * s3N)
    if family == "M2":
        return 1.0004 - 0.0373 * cN + 0.0002 * c2N, -2.14 * sN
    if family == "K2":
        return (1.0241 + 0.2863 * cN + 0.0083 * c2N - 0.0015 * c3N,
                -17.74 * sN + 0.68 * s2N - 0.04 * s3N)
    if family == "M2^2":
        f_m2 = 1.0004 - 0.0373 * cN + 0.0002 * c2N
        u_m2 = -2.14 * sN
        order = CONSTITUENTS[name][1] / 2.0
        return f_m2 ** order, order * u_m2
    if family == "MK3":
        f_m2, u_m2 = 1.0004 - 0.0373 * cN + 0.0002 * c2N, -2.14 * sN
        f_k1 = 1.0060 + 0.1150 * cN - 0.0088 * c2N + 0.0006 * c3N
        u_k1 = -8.86 * sN + 0.68 * s2N - 0.07 * s3N
        return f_m2 * f_k1, u_m2 + u_k1
    if family == "2MK3":
        f_m2, u_m2 = 1.0004 - 0.0373 * cN + 0.0002 * c2N, -2.14 * sN
        f_k1 = 1.0060 + 0.1150 * cN - 0.0088 * c2N + 0.0006 * c3N
        u_k1 = -8.86 * sN + 0.68 * s2N - 0.07 * s3N
        return f_m2 * f_m2 * f_k1, 2 * u_m2 - u_k1
    if family == "M2inv":
        return 1.0004 - 0.0373 * cN + 0.0002 * c2N, 2.14 * sN
    raise ValueError(family)


def predict_uv(constituents: dict, time_ms: float, mean: dict | None = None):
    """(u, v) in m/s from UTCEF harmonic_constituents at a UTC instant."""
    a = astronomical_args(time_ms)
    u = float((mean or {}).get("u_residual") or 0.0)
    v = float((mean or {}).get("v_residual") or 0.0)
    for name, c in constituents.items():
        if name not in CONSTITUENTS:
            continue
        f, un = node_factors(a, name)
        v0 = equilibrium_arg(a, name)
        u += f * c["u_amplitude"] * math.cos(math.radians(v0 + un - c["u_phase_g"]))
        v += f * c["v_amplitude"] * math.cos(math.radians(v0 + un - c["v_phase_g"]))
    return u, v


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

    errs, ours, theirs = [], [], []
    for row in cp:
        t = datetime.strptime(row["Time"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        flood = math.radians(float(row["meanFloodDir"]))
        u, v = predict_uv(hc, t.timestamp() * 1000.0, props.get("mean_offset"))
        mine = (u * math.sin(flood) + v * math.cos(flood)) * 100.0  # m/s -> cm/s
        noaa_v = float(row["Velocity_Major"])
        ours.append(mine)
        theirs.append(noaa_v)
        errs.append(mine - noaa_v)

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
            print(
                f"  {r['id']:<22} peak {r['peak_cms']:6.1f}  RMSE {r['rmse_cms']:5.1f} cm/s "
                f"({r['rmse_pct_of_peak']:4.1f}% of peak)  bias {r['bias_cms']:+5.1f}  corr {r['corr']:.4f}"
                f"{drop}  — {r['name']}"
            )
    print(f"\nWorst RMSE: {worst:.1f}% of peak "
          f"({'PASS' if worst < 10 else 'CHECK CONVERSION'} — phase/sign bugs typically show >50%)")


if __name__ == "__main__":
    sys.exit(main())
