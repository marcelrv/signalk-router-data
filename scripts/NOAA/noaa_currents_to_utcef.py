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

Subordinate ("S") and weak/variable ("W") stations are skipped for now —
subordinates need constituent synthesis from their reference station
(issue #2 phase 3); W stations have no published predictions.

Usage:
  python3 noaa_currents_to_utcef.py [--cache-dir cache] [--out-dir ../../regions] [--limit N]

Stdlib only; NOAA responses are cached in --cache-dir so re-runs are offline.
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
    bbox = [
        round(min(lons) - pad, 2), round(max(min(lats) - pad, -90), 2),
        round(max(lons) + pad, 2), round(min(max(lats) + pad, 90), 2),
    ]
    now = datetime.now(timezone.utc)
    payload = {
        "metadata": {
            "schema_version": "1.0.0",
            "dataset_version": now.strftime("%Y.%m.%d"),
            "title": region["title"],
            "description": (
                f"Harmonic tidal current predictions for {len(features)} NOAA CO-OPS reference "
                "stations, converted from the official NOAA harmonic constituents "
                "(tidesandcurrents.noaa.gov) by signalk-router-data scripts "
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
    args = ap.parse_args()

    stations_doc = fetch_json(
        f"{MDAPI}/stations.json?type=currentpredictions",
        os.path.join(args.cache_dir, "stations_currentpredictions.json"),
    )
    features = build_features(stations_doc["stations"], args.cache_dir, args.limit)

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
