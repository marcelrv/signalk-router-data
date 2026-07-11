#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""NOAA CO-OPS tidal current stations -> regional UTCEF databases.

Fetches the harmonic constituents of every NOAA current-prediction REFERENCE
station (type "H") from the CO-OPS metadata API and converts them into UTCEF
`harmonic_constituents_currents` features (u/v amplitude + Greenwich phase),
split into regional `.utcef` files under `regions/`.

Why offline conversion instead of a runtime API source: predictions are purely
astronomical (valid for years), the files are small and static, and boats need
them OFFLINE at sea. See signalk-router-data issue #2.

Data provenance
  Station list : https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=currentpredictions
  Constituents : https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations/{id}/harcon.json
                 (per bin: major/minor ellipse amplitude in cm/s, GREENWICH
                 phase `majorPhaseGMT`/`minorPhaseGMT`, flood-axis azimuth `azi`)

Ellipse -> u/v conversion (exact, per constituent):
  The major-axis current A·cos(θ−g_maj) points along azimuth `azi` (deg true);
  the minor-axis current A_min·cos(θ−g_min) points along `azi+90°`. Projecting
  both onto east (u) and north (v) and summing the two phasors of equal
  frequency gives one cosine per axis:
    u: |A·sin(azi)·e^{i·g_maj} + A_min·cos(azi)·e^{i·g_min}|, arg -> u_phase_g
    v: |A·cos(azi)·e^{i·g_maj} − A_min·sin(azi)·e^{i·g_min}|, arg -> v_phase_g
  A negative projection coefficient is absorbed as a 180° phase shift by the
  phasor sum. Most NOS stations are rectilinear (A_min = 0).

Bin choice: NOAA publishes several depth bins per station; the SHALLOWEST bin
is emitted (closest to what a vessel's hull experiences). Station ids are
`NOAA_<id>_<bin>` so other bins can be added later without id collisions.

Subordinate ("S") stations have no published constituents (their harcon is
empty); they are SYNTHESIZED: the reference station's signed major-axis speed
curve is time-shifted and scaled per the station's `currentpredictionoffsets`
(the classic flood/ebb offset algorithm, matching the consuming plugin's own
subordinate path), projected onto the subordinate's flood/ebb axes, and a
year of 30-minute samples is least-squares fitted back to UTCEF u/v
constituents (nodal factors are part of the design matrix, so the fitted
amplitudes are nodal-free like real constituents). The fit residual is kept
in `properties.noaa.fit_rms_cms`; stations whose offsets or reference can't
be resolved are skipped. Weak/variable ("W") stations have no published
predictions at all and are always skipped.

Usage:
  python3 noaa_currents_to_utcef.py [--cache-dir cache] [--out-dir ../../regions]
      [--limit N] [--no-subordinates]

Requires numpy (for the subordinate fit); NOAA responses are cached in
--cache-dir so re-runs are offline.
"""
import argparse
import cmath
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

import numpy as np

from noaa_astro import CONSTITUENTS, astronomical_args, equilibrium_arg, node_factors

MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
USER_AGENT = "signalk-router-data/noaa-currents (https://github.com/marcelrv/signalk-router-data)"

# NOAA constituent spellings -> IHO/engine-canonical names (UTCEF spec §5.0).
NAME_MAP = {"LAM2": "LAMBDA2", "RHO": "RHO1"}

# Regions are matched in order; first hit wins. Folders mirror the FES2014
# basin layout under regions/.
REGIONS = [
    {
        "id": "noaa_pacific_islands",
        "title": "NOAA Tidal Currents — Guam, Samoa & Pacific Islands",
        "folder": "pacific_oceania",
        "match": lambda lat, lon: lat < 0 or (0 <= lat < 25 and (lon > 100 or lon < -165)),
    },
    {
        "id": "noaa_hawaii",
        "title": "NOAA Tidal Currents — Hawaiian Islands",
        "folder": "pacific_oceania",
        "match": lambda lat, lon: 18 <= lat <= 23.5 and -161.5 <= lon <= -154,
    },
    {
        "id": "noaa_alaska",
        "title": "NOAA Tidal Currents — Alaska & Aleutians",
        "folder": "pacific_asia",
        "match": lambda lat, lon: lat >= 51 or (lat >= 49.5 and lon <= -128) or lon > 100,
    },
    {
        "id": "noaa_us_west_coast",
        "title": "NOAA Tidal Currents — US West Coast & Puget Sound",
        "folder": "pacific_asia",
        "match": lambda lat, lon: lon <= -113,
    },
    {
        "id": "noaa_puerto_rico",
        "title": "NOAA Tidal Currents — Puerto Rico & Virgin Islands",
        "folder": "atlantic",
        "match": lambda lat, lon: 16.5 <= lat <= 19.5 and -68.5 <= lon <= -63.5,
    },
    {
        "id": "noaa_gulf_of_mexico",
        "title": "NOAA Tidal Currents — Gulf of Mexico",
        "folder": "atlantic",
        "match": lambda lat, lon: lat <= 31 and lon <= -81.8,
    },
    {
        "id": "noaa_us_east_coast",
        "title": "NOAA Tidal Currents — US East Coast",
        "folder": "atlantic",
        "match": lambda lat, lon: True,
    },
]


def fetch_json(url: str, cache_path: str, delay_s: float = 0.12, retries: int = 4) -> dict:
    """GET a JSON document with an on-disk cache and polite retry/backoff."""
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, cache_path)
            time.sleep(delay_s)
            return data
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def phasor_sum(c1: float, g1_deg: float, c2: float, g2_deg: float):
    """Combine c1·cos(θ−g1) + c2·cos(θ−g2) into (amplitude, phase_deg)."""
    z = c1 * cmath.exp(1j * math.radians(g1_deg)) + c2 * cmath.exp(1j * math.radians(g2_deg))
    amp = abs(z)
    phase = math.degrees(cmath.phase(z)) % 360.0 if amp > 0 else 0.0
    return amp, phase


def mean_offset(rows: list) -> dict:
    """Residual (non-tidal) mean flow of a bin as a UTCEF mean_offset (m/s).

    harcon repeats the bin's mean flow on every constituent row:
    `majorMeanSpeed` along the flood axis `azi`, `minorMeanSpeed` along
    `azi+90°`, both cm/s. Validated against datagetter currents_predictions —
    omitting this shows up as exactly this much bias.
    """
    r = rows[0]
    maj = float(r.get("majorMeanSpeed") or 0.0)
    mnr = float(r.get("minorMeanSpeed") or 0.0)
    azi = math.radians(float(r.get("azi") or 0.0))
    return {
        "u_residual": round((maj * math.sin(azi) + mnr * math.cos(azi)) / 100.0, 5),
        "v_residual": round((maj * math.cos(azi) - mnr * math.sin(azi)) / 100.0, 5),
    }


def convert_bin(rows: list) -> dict:
    """harcon rows of ONE bin -> UTCEF harmonic_constituents dict (m/s, °T)."""
    out = {}
    for r in rows:
        a_maj = float(r.get("majorAmplitude") or 0.0)   # cm/s
        a_min = float(r.get("minorAmplitude") or 0.0)   # cm/s
        if a_maj == 0.0 and a_min == 0.0:
            continue
        g_maj = float(r.get("majorPhaseGMT") or 0.0)    # Greenwich phase, deg
        g_min = float(r.get("minorPhaseGMT") or 0.0)
        azi = math.radians(float(r.get("azi") or 0.0))  # flood axis, deg true

        u_amp, u_g = phasor_sum(a_maj * math.sin(azi), g_maj, a_min * math.cos(azi), g_min)
        v_amp, v_g = phasor_sum(a_maj * math.cos(azi), g_maj, -a_min * math.sin(azi), g_min)
        if u_amp == 0.0 and v_amp == 0.0:
            continue

        name = NAME_MAP.get(r["constituentName"], r["constituentName"])
        out[name] = {
            "u_amplitude": round(u_amp / 100.0, 5),  # cm/s -> m/s
            "u_phase_g": round(u_g, 2),
            "v_amplitude": round(v_amp / 100.0, 5),
            "v_phase_g": round(v_g, 2),
        }
    return out


# --- Subordinate-station synthesis (issue #2 phase 3) ----------------------

FIT_STEP_MIN = 30          # sample step of the synthesized series
MIN_FIT_AMP_CMS = 0.1      # fitted constituents below this (both axes) are dropped


def major_axis_series(rows: list, times_ms: np.ndarray) -> np.ndarray:
    """Reference station's signed speed (cm/s) along its flood axis at `times_ms`."""
    a = astronomical_args(times_ms)
    s = np.full(times_ms.shape, float(rows[0].get("majorMeanSpeed") or 0.0))
    for r in rows:
        amp = float(r.get("majorAmplitude") or 0.0)
        if amp == 0.0:
            continue
        name = NAME_MAP.get(r["constituentName"], r["constituentName"])
        if name not in CONSTITUENTS:
            continue  # M1/OO1: no engine support, ~1-3 cm/s
        f, un = node_factors(a, name)
        v0 = equilibrium_arg(a, name)
        s += f * amp * np.cos(np.radians(v0 + un - float(r.get("majorPhaseGMT") or 0.0)))
    return s


def _synthesize_two_branch(ref_rows: list, off: dict, times_ms: np.ndarray) -> np.ndarray:
    """Classic flood/ebb offset algorithm (mirrors the plugin's predict.ts):
    evaluate the reference at the flood- and ebb-shifted times with their
    amplitude ratios and pick the phase-consistent branch; near slack (the
    branches disagree) take the weaker signal for a continuous transition.
    Ignores the slack time offsets — fallback only."""
    flood = major_axis_series(ref_rows, times_ms - float(off.get("mfcTimeAdjMin") or 0) * 60000.0) \
        * float(off.get("mfcAmpAdj") or 0.0)
    ebb = major_axis_series(ref_rows, times_ms - float(off.get("mecTimeAdjMin") or 0) * 60000.0) \
        * float(off.get("mecAmpAdj") or 0.0)
    return np.where(
        (flood > 0) & (ebb >= 0), flood,
        np.where((ebb < 0) & (flood <= 0), ebb, np.where(np.abs(flood) < np.abs(ebb), flood, ebb)),
    )


def synthesize_subordinate(ref_rows: list, off: dict, times_ms: np.ndarray) -> np.ndarray:
    """Event-warped synthesis matching NOAA's subordinate event semantics.

    NOAA publishes subordinate predictions as max/slack EVENTS only: max
    flood/ebb at the reference max time + mfc/mec offset scaled by the
    flood/ebb ratio, slacks at the reference slack time + sbe/sbf offset.
    This reconstructs a continuous curve with exactly those event times and
    values: locate the reference curve's events on a fine grid, shift each
    event by its own offset, and piecewise-linearly warp time between
    events. Falls back to the two-branch approximation if the shifted event
    times come out non-monotonic (pathological offsets).
    """
    mfc = float(off.get("mfcTimeAdjMin") or 0) * 60000.0
    mec = float(off.get("mecTimeAdjMin") or 0) * 60000.0
    sbe = float(off.get("sbeTimeAdjMin") or 0) * 60000.0
    sbf = float(off.get("sbfTimeAdjMin") or 0) * 60000.0
    rf = float(off.get("mfcAmpAdj") or 0.0)
    re_ = float(off.get("mecAmpAdj") or 0.0)

    # Fine reference curve, padded past both window ends so every fit sample
    # lies strictly between two shifted events.
    pad, step = 86_400_000.0, 6 * 60_000.0
    fine_t = np.arange(times_ms[0] - pad, times_ms[-1] + pad, step)
    s_ref = major_axis_series(ref_rows, fine_t)

    # Slacks: sign changes, linearly interpolated to the crossing instant.
    cross = np.nonzero(np.diff(np.signbit(s_ref)))[0]
    if len(cross) < 4:
        return _synthesize_two_branch(ref_rows, off, times_ms)
    v0, v1 = s_ref[cross], s_ref[cross + 1]
    slack_t = fine_t[cross] - v0 * step / (v1 - v0)
    slack_is_sbe = v0 > 0  # flood -> ebb transition = "slack before ebb"

    # One extremum between consecutive slacks (the dominant one in mixed tides).
    ev_t, ev_shift, ev_scale = [], [], []
    for i in range(len(cross) - 1):
        ev_t.append(slack_t[i])
        ev_shift.append(sbe if slack_is_sbe[i] else sbf)
        ev_scale.append(np.nan)  # slacks have no scale of their own
        seg = slice(cross[i] + 1, cross[i + 1] + 1)
        k = cross[i] + 1 + int(np.argmax(np.abs(s_ref[seg])))
        ev_t.append(fine_t[k])
        is_flood = s_ref[k] > 0
        ev_shift.append(mfc if is_flood else mec)
        ev_scale.append(rf if is_flood else re_)
    ev_t.append(slack_t[-1])
    ev_shift.append(sbe if slack_is_sbe[-1] else sbf)
    ev_scale.append(np.nan)

    ref_ev = np.asarray(ev_t)
    sub_ev = ref_ev + np.asarray(ev_shift)
    if not np.all(np.diff(sub_ev) > 0):
        return _synthesize_two_branch(ref_rows, off, times_ms)

    # A segment's speed ratio is the one of its bounding extremum (each
    # segment runs slack->max or max->slack).
    scales = np.asarray(ev_scale)
    seg_scale = np.where(np.isnan(scales[:-1]), scales[1:], scales[:-1])

    j = np.clip(np.searchsorted(sub_ev, times_ms, side="right") - 1, 0, len(sub_ev) - 2)
    frac = (times_ms - sub_ev[j]) / (sub_ev[j + 1] - sub_ev[j])
    tau = ref_ev[j] + frac * (ref_ev[j + 1] - ref_ev[j])
    return np.interp(tau, fine_t, s_ref) * seg_scale[j]


def fit_constituents(times_ms: np.ndarray, u_cms: np.ndarray, v_cms: np.ndarray, names: list):
    """Least-squares fit of u/v series (cm/s) to UTCEF constituents.

    Design columns are f(t)·cos(Φ) and f(t)·sin(Φ) with Φ = V0 + u_nodal, so a
    fitted term is exactly the engine's f·A·cos(Φ − g): amplitudes come out
    nodal-free. Returns (harmonic_constituents m/s, mean_offset m/s, rms cm/s).
    """
    a = astronomical_args(times_ms)
    cols = [np.ones_like(times_ms)]
    for n in names:
        f, un = node_factors(a, n)
        phi = np.radians(equilibrium_arg(a, n) + un)
        cols.append(f * np.cos(phi))
        cols.append(f * np.sin(phi))
    design = np.column_stack(cols)
    rhs = np.column_stack([u_cms, v_cms])
    coef, *_ = np.linalg.lstsq(design, rhs, rcond=None)
    rms = float(np.sqrt(np.mean((design @ coef - rhs) ** 2)))

    out = {}
    for i, n in enumerate(names):
        cu, su = coef[1 + 2 * i, 0], coef[2 + 2 * i, 0]
        cv, sv = coef[1 + 2 * i, 1], coef[2 + 2 * i, 1]
        u_amp, v_amp = math.hypot(cu, su), math.hypot(cv, sv)
        if u_amp < MIN_FIT_AMP_CMS and v_amp < MIN_FIT_AMP_CMS:
            continue
        out[n] = {
            "u_amplitude": round(u_amp / 100.0, 5),
            "u_phase_g": round(math.degrees(math.atan2(su, cu)) % 360.0, 2),
            "v_amplitude": round(v_amp / 100.0, 5),
            "v_phase_g": round(math.degrees(math.atan2(sv, cv)) % 360.0, 2),
        }
    mean = {
        "u_residual": round(coef[0, 0] / 100.0, 5),
        "v_residual": round(coef[0, 1] / 100.0, 5),
    }
    return out, mean, rms


def build_subordinate_features(stations: list, cache_dir: str, limit: int | None):
    """Fetch offsets per S station, synthesize a year of samples from the
    reference harmonics, fit constituents, and yield (region_id, feature)."""
    # One metadata row per (id, bin); keep the shallowest bin per station.
    def bin_key(e):
        return (e["depth"] if e.get("depth") is not None else float("inf"), e.get("currbin") or 999)
    subs = {}
    for s in stations:
        if s.get("type") != "S":
            continue
        if s["id"] not in subs or bin_key(s) < bin_key(subs[s["id"]]):
            subs[s["id"]] = s
    ids = sorted(subs)
    if limit:
        ids = ids[:limit]
    print(f"[NOAA] {len(ids)} subordinate stations to synthesize")

    year = datetime.now(timezone.utc).year
    t0 = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000.0
    t1 = datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000.0
    times = np.arange(t0, t1, FIT_STEP_MIN * 60000.0)

    features, skipped = [], 0
    for n, sid in enumerate(ids, 1):
        meta = subs[sid]
        b = int(meta.get("currbin") or 1)
        try:
            off = fetch_json(
                f"{MDAPI}/stations/{sid}_{b}/currentpredictionoffsets.json",
                os.path.join(cache_dir, "offsets", f"{sid}_{b}.json"),
            )
            ref_id, ref_bin = off.get("refStationId"), off.get("refStationBin")
            fdir, edir = off.get("meanFloodDir"), off.get("meanEbbDir")
            if not ref_id or fdir is None or edir is None:
                raise ValueError("incomplete offsets")
            if float(off.get("mfcAmpAdj") or 0) <= 0 and float(off.get("mecAmpAdj") or 0) <= 0:
                raise ValueError("zero amplitude adjustments")
            harcon = fetch_json(
                f"{MDAPI}/stations/{ref_id}/harcon.json",
                os.path.join(cache_dir, "harcon", f"{ref_id}.json"),
            )
            ref_rows = [r for r in (harcon.get("HarmonicConstituents") or []) if int(r.get("binNbr") or 1) == int(ref_bin or 1)]
            if not ref_rows:
                raise ValueError(f"reference {ref_id} bin {ref_bin} has no constituents")
        except (RuntimeError, ValueError) as e:
            print(f"  [Warning] {sid}: {e}")
            skipped += 1
            continue

        s = synthesize_subordinate(ref_rows, off, times)
        mag = np.abs(s)
        fdir_r, edir_r = math.radians(float(fdir)), math.radians(float(edir))
        u = np.where(s >= 0, mag * math.sin(fdir_r), mag * math.sin(edir_r))
        v = np.where(s >= 0, mag * math.cos(fdir_r), mag * math.cos(edir_r))

        names = sorted({
            NAME_MAP.get(r["constituentName"], r["constituentName"])
            for r in ref_rows
            if float(r.get("majorAmplitude") or 0.0) != 0.0
        } & set(CONSTITUENTS))
        if not names:
            skipped += 1
            continue
        constituents, mean, rms = fit_constituents(times, u, v, names)
        if not constituents:
            skipped += 1
            continue

        lat, lon = float(meta["lat"]), float(meta["lng"])
        name = meta.get("name") or sid
        depth_m = meta.get("depth")
        region = next(r for r in REGIONS if r["match"](lat, lon))
        features.append((region["id"], {
            "type": "Feature",
            "id": f"NOAA_{sid}_{b}",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {
                "station_name": name,
                "prediction_method": "harmonic_constituents_currents",
                "data_unit_speed": "meters_per_second",
                "mean_offset": mean,
                "harmonic_constituents": constituents,
                # Non-normative provenance extras (ignored by UTCEF engines).
                "noaa": {
                    "station_id": sid,
                    "bin": b,
                    "depth_m": depth_m,
                    "type": "subordinate",
                    "reference": f"{ref_id}_{ref_bin}",
                    "fit_rms_cms": round(rms, 2),
                },
            },
        }))
        if n % 100 == 0:
            print(f"  … {n}/{len(ids)}")
    print(f"[NOAA] synthesized {len(features)} subordinate stations, skipped {skipped}")
    return features


def pick_bin(rows_by_bin: dict) -> int:
    """Shallowest bin (smallest binDepth); ties/missing depth -> lowest binNbr."""
    def depth_of(b):
        depths = [r.get("binDepth") for r in rows_by_bin[b] if r.get("binDepth") is not None]
        return min(depths) if depths else float("inf")
    return min(rows_by_bin, key=lambda b: (depth_of(b), b))


def build_features(stations: list, cache_dir: str, limit: int | None):
    """Fetch harcon per H station and yield (region_id, feature) pairs."""
    # One metadata row per (id, bin); collapse to unique base stations.
    base = {}
    for s in stations:
        if s.get("type") != "H":
            continue
        base.setdefault(s["id"], s)
    ids = sorted(base)
    if limit:
        ids = ids[:limit]
    print(f"[NOAA] {len(ids)} harmonic reference stations to convert")

    skipped, features = 0, []
    for n, sid in enumerate(ids, 1):
        meta = base[sid]
        url = f"{MDAPI}/stations/{sid}/harcon.json"
        try:
            harcon = fetch_json(url, os.path.join(cache_dir, "harcon", f"{sid}.json"))
        except RuntimeError as e:
            print(f"  [Warning] {sid}: {e}")
            skipped += 1
            continue
        rows = harcon.get("HarmonicConstituents") or []
        rows_by_bin = {}
        for r in rows:
            rows_by_bin.setdefault(int(r.get("binNbr") or 1), []).append(r)
        if not rows_by_bin:
            skipped += 1
            continue
        b = pick_bin(rows_by_bin)
        constituents = convert_bin(rows_by_bin[b])
        if not constituents:
            skipped += 1
            continue

        lat, lon = float(meta["lat"]), float(meta["lng"])
        depths = [r.get("binDepth") for r in rows_by_bin[b] if r.get("binDepth") is not None]
        depth_m = round(min(depths), 1) if depths else None
        name = meta.get("name") or sid
        if len(rows_by_bin) > 1 and depth_m is not None:
            name = f"{name} ({depth_m} m)"

        region = next(r for r in REGIONS if r["match"](lat, lon))
        features.append((region["id"], {
            "type": "Feature",
            "id": f"NOAA_{sid}_{b}",
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
            "properties": {
                "station_name": name,
                "prediction_method": "harmonic_constituents_currents",
                "data_unit_speed": "meters_per_second",
                "mean_offset": mean_offset(rows_by_bin[b]),
                "harmonic_constituents": constituents,
                # Non-normative provenance extras (ignored by UTCEF engines).
                "noaa": {"station_id": sid, "bin": b, "depth_m": depth_m},
            },
        }))
        if n % 100 == 0:
            print(f"  … {n}/{len(ids)}")
    print(f"[NOAA] converted {len(features)} stations, skipped {skipped}")
    return features


def write_region(region: dict, features: list, out_dir: str) -> None:
    lats = [f["geometry"]["coordinates"][1] for f in features]
    lons = [f["geometry"]["coordinates"][0] for f in features]
    pad = 0.5
    # Dateline-aware longitude extent: when the naive span exceeds 180° (the
    # Aleutians sit on both sides of ±180°), work in 0..360 space and emit the
    # min_lon > max_lon crossing convention already used by the FES Bering
    # Sea file rather than a whole-world rectangle.
    if max(lons) - min(lons) > 180:
        shifted = [lon + 360 if lon < 0 else lon for lon in lons]
        west, east = min(shifted) - pad, max(shifted) + pad
        west, east = (w - 360 if w > 180 else w for w in (west, east))
    else:
        west, east = min(lons) - pad, max(lons) + pad
    bbox = [
        round(west, 2), round(max(min(lats) - pad, -90), 2),
        round(east, 2), round(min(max(lats) + pad, 90), 2),
    ]
    now = datetime.now(timezone.utc)
    payload = {
        "metadata": {
            "schema_version": "1.0.0",
            "dataset_version": now.strftime("%Y.%m.%d"),
            "title": region["title"],
            "description": (
                f"Harmonic tidal current predictions for {len(features)} NOAA CO-OPS current "
                "stations, converted from the official NOAA harmonic constituents — subordinate "
                "stations are synthesized from their reference station's harmonics and NOAA's "
                "published flood/ebb offsets — by signalk-router-data scripts "
                "(https://github.com/marcelrv/signalk-router-data). Predictions are purely "
                "astronomical; actual currents can deviate with weather and river flow."
            ),
            "last_updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "region": {"name": region["title"], "bbox": bbox},
            "copyright": "NOAA CO-OPS station data: U.S. Government work, public domain. Derived database packaging: see repository license.",
            "license": "Public Domain (U.S. Government work, 17 U.S.C. §105)",
            "data_sources": [
                {"name": "NOAA CO-OPS Tides & Currents", "url": "https://tidesandcurrents.noaa.gov/", "role": "Data Provider"},
            ],
            # Catalog hints consumed by scripts/tide-current/sources/utcef_regions.py.
            "catalog": {
                "source": "noaa",
                "contributor": "NOAA Center for Operational Oceanographic Products and Services (CO-OPS)",
                "url": "https://tidesandcurrents.noaa.gov/currents_info.html",
                "tags": ["utcef", "tidal", "currents", "noaa", "harmonic", "station-based", "regional"],
            },
        },
        "dataset": {"type": "FeatureCollection", "features": features},
    }
    folder = os.path.join(out_dir, region["folder"])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{region['id']}.utcef")
    text = json.dumps(payload, indent=1, ensure_ascii=False)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{region['id']}.json", text)
    print(f"[Success] {path}: {len(features)} stations, bbox {bbox}, {os.path.getsize(path)/1024:.0f} KiB")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="NOAA CO-OPS current stations -> regional UTCEF files")
    ap.add_argument("--cache-dir", default=os.path.join(here, "cache"))
    ap.add_argument("--out-dir", default=os.path.normpath(os.path.join(here, "..", "..", "regions")))
    ap.add_argument("--limit", type=int, default=None, help="convert only the first N stations (debug)")
    ap.add_argument("--no-subordinates", action="store_true", help="reference (H) stations only")
    args = ap.parse_args()

    stations_doc = fetch_json(
        f"{MDAPI}/stations.json?type=currentpredictions",
        os.path.join(args.cache_dir, "stations_currentpredictions.json"),
    )
    features = build_features(stations_doc["stations"], args.cache_dir, args.limit)
    if not args.no_subordinates:
        features += build_subordinate_features(stations_doc["stations"], args.cache_dir, args.limit)

    by_region = {}
    for region_id, feature in features:
        by_region.setdefault(region_id, []).append(feature)
    for region in REGIONS:
        feats = by_region.get(region["id"])
        if feats:
            write_region(region, feats, args.out_dir)
        else:
            print(f"[Note] region {region['id']}: no stations, no file written")


if __name__ == "__main__":
    sys.exit(main())
