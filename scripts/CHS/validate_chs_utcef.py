#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Validate CHS UTCEF files against CHS's own predictions, out of sample.

For every station in a generated `chs_*.utcef` file this harness:
  1. predicts (u, v) from the file's constituents with the Python port of the
     plugin engine (scripts/NOAA/noaa_astro.py);
  2. fetches CHS 15-minute predictions (wcsp1/wcdp1) and slack/max events
     (wcp1-events) for a window AFTER the fit window, so the check is out of
     sample (cached in the converter's cache dir);
  3. reports, per station: RMSE and bias along the flood axis and the vector
     RMSE (kn), the mean |slack-time error| (min) and the mean |max-speed
     error| (kn) against the CHS events.

Stations whose vector RMSE exceeds --flag-pct of their peak speed are
flagged; the exit status is 1 if any station is flagged.

Usage:
  python3 validate_chs_utcef.py ../../regions/north-america/canada/chs_bc_coast.utcef \
      [--start 2027-11-17] [--days 28] [--flag-pct 10]
"""
import argparse
import json
import os
import sys
import zipfile
from datetime import timedelta

import numpy as np

import chs_currents_to_utcef as chs
from noaa_astro import predict_uv

SEARCH_MIN = 90  # half-width of the window around a CHS event to look for the predicted one


def load_features(path: str) -> list:
    with zipfile.ZipFile(path) as z:
        inner = [n for n in z.namelist() if n.endswith(".json")][0]
        return json.loads(z.read(inner))["dataset"]["features"]


def _interior_events(times_ms, events, qualifiers):
    """Event times of the given qualifiers whose ±SEARCH_MIN window lies inside the
    prediction series. Events nearer the ends are left out entirely: their predicted
    counterpart may fall outside the series, so they say nothing about the fit."""
    margin = SEARCH_MIN * 60000.0
    for e in events:
        if e.get("qualifier") not in qualifiers:
            continue
        t = chs.parse_time_ms(e["eventDate"])
        if times_ms[0] + margin <= t <= times_ms[-1] - margin:
            yield t, e


def slack_errors_min(times_ms, axis_kn, events) -> tuple:
    """|Δt| between each CHS SLACK and the nearest predicted zero crossing.

    Returns (errors, misses): errors for events with a predicted crossing within
    SEARCH_MIN, and the number of events without one. A miss is not necessarily
    a bad fit: a rotary or weak current need not cross zero on the flood axis.
    """
    sign = np.sign(axis_kn)
    idx = np.nonzero(sign[:-1] * sign[1:] < 0)[0]
    # Linear interpolation of the crossing time.
    t0, t1 = times_ms[idx], times_ms[idx + 1]
    a0, a1 = axis_kn[idx], axis_kn[idx + 1]
    crossings = t0 + (t1 - t0) * (a0 / (a0 - a1))
    out, misses = [], 0
    for t, _ in _interior_events(times_ms, events, ("SLACK",)):
        d = np.min(np.abs(crossings - t)) / 60000.0 if len(crossings) else np.inf
        if d <= SEARCH_MIN:
            out.append(float(d))
        else:
            misses += 1
    return out, misses


def max_errors_kn(times_ms, speed_kn, events) -> tuple:
    """|Δspeed| between each CHS max flood/ebb and the predicted local max.

    Returns (errors, misses): misses are events with no predicted sample within
    SEARCH_MIN (a gap in the series)."""
    out, misses = [], 0
    for t, e in _interior_events(times_ms, events, ("EXTREMA_FLOOD", "EXTREMA_EBB")):
        m = np.abs(times_ms - t) <= SEARCH_MIN * 60000.0
        if m.any():
            # Compare magnitudes: CHS reports ebb maxima as positive knots today,
            # but a signed (negative) ebb value must not count as a doubled error.
            out.append(abs(float(np.max(speed_kn[m])) - abs(float(e["value"]))))
        else:
            misses += 1
    return out, misses


def validate_feature(f: dict, start, end, cache_dir: str) -> dict:
    c = f["properties"]["chs"]
    sid = c["station_id"]
    times, spd, drn = chs.merge_series(
        chs.fetch_series(sid, "wcsp1", start, end, cache_dir),
        chs.fetch_series(sid, "wcdp1", start, end, cache_dir),
    )
    if len(times) == 0:
        raise ValueError("no CHS predictions in the validation window")
    events = chs.fetch_events(sid, start, end, cache_dir)
    obs_u, obs_v = chs.to_uv(spd, drn)
    u, v = predict_uv(f["properties"]["harmonic_constituents"], times, f["properties"].get("mean_offset"))

    flood = float(c.get("flood_dir") or 0.0)
    obs_ax, pred_ax = chs.axis_speed_kn(obs_u, obs_v, flood), chs.axis_speed_kn(u, v, flood)
    vec_err = np.hypot(u - obs_u, v - obs_v) / chs.KN_TO_MS
    pred_speed = np.hypot(u, v) / chs.KN_TO_MS
    slack, slack_miss = slack_errors_min(times, pred_ax, events)
    maxes, max_miss = max_errors_kn(times, pred_speed, events)
    return {
        "n": len(times),
        "peak_kn": float(np.max(spd)),
        "rmse_axis_kn": float(np.sqrt(np.mean((pred_ax - obs_ax) ** 2))),
        "bias_axis_kn": float(np.mean(pred_ax - obs_ax)),
        "rmse_vec_kn": float(np.sqrt(np.mean(vec_err ** 2))),
        "slack_err_min": float(np.mean(slack)) if slack else None,
        "n_slack": len(slack),
        "slack_miss": slack_miss,
        "max_err_kn": float(np.mean(maxes)) if maxes else None,
        "n_max": len(maxes),
        "max_miss": max_miss,
    }


def fmt_events(mean, n, misses, fmt: str) -> str:
    """'mean/n' over matched events, plus '+k miss' for unmatched ones."""
    s = (fmt.format(mean) + f"/{n}") if mean is not None else "-"
    return s + (f" +{misses} miss" if misses else "")


def main():
    ap = argparse.ArgumentParser(
        description="Validate CHS UTCEF files out of sample",
        epilog=f"slack/max columns: mean error over matched events / matched count; "
               f"'+N miss' counts CHS events with no predicted match within {SEARCH_MIN} min "
               f"(reported only, not flagged).")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--cache-dir", default=os.path.join(chs.HERE, "cache"))
    ap.add_argument("--start", default=None, help="validation start (default: day after the fit window)")
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--flag-pct", type=float, default=10.0)
    ap.add_argument("--allow-skipped", action="store_true",
                    help="exit 0 even if some stations could not be validated")
    args = ap.parse_args()

    flagged = validated = skipped = misses = 0
    for path in args.files:
        print(f"\n== {path}")
        print(f"{'station':34s} {'peak':>6s} {'rmseAx':>7s} {'bias':>7s} {'rmseV':>7s} {'slack':>17s} {'max':>17s}")
        for f in load_features(path):
            c = f["properties"]["chs"]
            if args.start:
                start = chs.parse_day(args.start)
            else:  # fit_window[1] is the last fitted sample's date: start the day after
                start = chs.parse_day(c["fit_window"][1]) + timedelta(days=1)
            end = start + timedelta(days=args.days)
            label = f"{c['station_code']} {f['properties']['station_name']}"[:34]
            try:
                r = validate_feature(f, start, end, args.cache_dir)
            except (RuntimeError, ValueError) as e:
                print(f"{label:34s} [skipped: {e}]")
                skipped += 1
                continue
            validated += 1
            bad = r["rmse_vec_kn"] > args.flag_pct / 100.0 * r["peak_kn"]
            flagged += bad
            slack = fmt_events(r["slack_err_min"], r["n_slack"], r["slack_miss"], "{:.1f}m")
            mx = fmt_events(r["max_err_kn"], r["n_max"], r["max_miss"], "{:.2f}kn")
            misses += r["slack_miss"] + r["max_miss"]
            print(f"{label:34s} {r['peak_kn']:6.2f} {r['rmse_axis_kn']:7.3f} {r['bias_axis_kn']:7.3f} "
                  f"{r['rmse_vec_kn']:7.3f} {slack:>17s} {mx:>17s}{'  <-- FLAG' if bad else ''}")
    print(f"\n{validated} validated, {skipped} skipped; "
          f"{flagged} flagged (vector RMSE > {args.flag_pct}% of peak).")
    if misses:
        print(f"{misses} CHS slack/max event(s) had no predicted match within {SEARCH_MIN} min "
              f"(not counted in the means; not flagged).")
    if skipped and not args.allow_skipped:
        print(f"{skipped} station(s) could not be validated (use --allow-skipped to accept).")
        return 1
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
