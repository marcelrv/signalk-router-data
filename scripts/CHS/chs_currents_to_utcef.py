#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Canadian Hydrographic Service (CHS) current predictions -> UTCEF.

CHS publishes tidal current predictions for ~30 stations (BC passes and
rapids, a few Atlantic channels) through the IWLS public API — but only as
TIME SERIES (speed in knots + direction, at most 7 days per request, about
13 months ahead). It publishes no harmonic constituents. UTCEF has no
time-series method, so this script fits UTCEF `harmonic_constituents_currents`
to one year of CHS predictions per station, the same least-squares fit the
NOAA converter uses for subordinate stations (nodal factors are in the design
matrix, so amplitudes are nodal-free and the file stays valid for years).

Why not GRIB: these are 30 point stations, rapids 1–5 km apart. A grid fine
enough to separate them is almost all missing values, the consumer would
interpolate a 15 kn rapid into neighbouring channels, and fixed time steps
would need a rolling refresh for purely astronomical data. See issue #9.

Data provenance
  API       : https://api-iwls.dfo-mpo.gc.ca/api/v1
              (the host api.iwls-sine.azure.cloud-nuage.dfo-mpo.gc.ca no
              longer answers)
  Stations  : /stations — kept when timeSeries has both `wcsp1` and `wcdp1`
  Metadata  : /stations/{id}/metadata — floodDirection / ebbDirection
  Series    : /stations/{id}/data?time-series-code=wcsp1|wcdp1
              &resolution=FIFTEEN_MINUTES&from=..&to=..   (≤ 7 days, both
              ends inclusive). wcsp1 is in knots (phenomenon `wcs`, unit kn);
              wcdp1 is the direction the current flows TOWARD, degrees true
              — the UTCEF "set" convention.

Licence: CHS data is NOT under the Open Government Licence. It is covered
by the CHS Licence Agreement (https://www.tides.gc.ca/en/licence-agreement):
no commercial derivative products, not for navigation, and derivative
products must carry the clause-10 notice, which write_region() embeds.

Usage:
  python3 chs_currents_to_utcef.py [--cache-dir cache] [--out-dir ../../regions]
      [--start 2026-11-17] [--days 365] [--limit N] [--station 08108]

Responses are cached in --cache-dir, so re-runs with the same --start are
offline. Requires numpy.
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "NOAA"))
from noaa_astro import CONSTITUENTS  # noqa: E402
from noaa_currents_to_utcef import fit_constituents  # noqa: E402

API = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
USER_AGENT = "signalk-router-data/chs-currents (https://github.com/marcelrv/signalk-router-data)"

KN_TO_MS = 1852.0 / 3600.0
MAX_CHUNK_DAYS = 7          # IWLS: "date interval should not be bigger than 7 days"
STEP_MIN = 15
MIN_SPAN_DAYS = 200         # skip stations with less joint speed+direction data
MIN_COVERAGE = 0.9          # of the samples expected within that span
FIT_NAMES = sorted(CONSTITUENTS)  # only what the plugin engine can predict
# Need about a year of data to separate from their neighbours (Rayleigh):
# SA from SSA, S1 from K1/P1, T2 and R2 from S2. Dropped for shorter series.
YEAR_ONLY = {"SA", "S1", "T2", "R2"}


def fit_names_for(span_days: float) -> list:
    return FIT_NAMES if span_days >= 355 else [n for n in FIT_NAMES if n not in YEAR_ONLY]

LICENCE_URL = "https://www.tides.gc.ca/en/licence-agreement"
LICENSEE = "Marcel Verpaalen (signalk-router-data)"
# CHS Licence Agreement clause 10, verbatim, placeholder filled in.
CHS_NOTICE = (
    "This product is not to be used for navigation.\n"
    f"This product was made by or for the User {LICENSEE} and contains intellectual property "
    "(Data) of the Canadian Hydrographic Service of the Department of Fisheries and Oceans.\n"
    "The copyrights in the Data are and remain the property of His Majesty the King in Right "
    "of Canada and shall not be sold, licensed, leased, assigned or given to a third party.\n"
    "The incorporation of the Data in this product does not constitute an endorsement or an "
    "approval of this product by the Canadian Hydrographic Service, the Department of "
    "Fisheries and Oceans or His Majesty the King in Right of Canada"
)

REGIONS = [
    {
        "id": "chs_bc_coast",
        "title": "CHS Tidal Currents — British Columbia",
        "folder": "north-america/canada",
        "match": lambda lat, lon: lon <= -110,
    },
    {
        "id": "chs_atlantic_canada",
        "title": "CHS Tidal Currents — Atlantic Canada",
        "folder": "north-america/canada",
        "match": lambda lat, lon: True,
    },
]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_json(url: str, cache_path: str, delay_s: float = 0.15, retries: int = 4):
    """GET JSON with an on-disk cache. HTTP 4xx is not retried."""
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            tmp = f"{cache_path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, cache_path)
            time.sleep(delay_s)
            return data
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code} for {url}") from e
            last_err = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
        time.sleep(3.0 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_err}")


def select_current_stations(stations: list) -> list:
    """Stations publishing both current speed and direction predictions."""
    out = []
    for s in stations:
        codes = {t.get("code") for t in s.get("timeSeries") or []}
        if {"wcsp1", "wcdp1"} <= codes:
            out.append(s)
    return sorted(out, key=lambda s: s["code"])


def date_chunks(start: datetime, end: datetime, days: int = MAX_CHUNK_DAYS) -> list:
    """Split [start, end) into request windows of at most `days` days.

    IWLS treats both ends as inclusive, so the last sample of one window and
    the first of the next coincide; merge_series() de-duplicates by time.
    """
    out, t = [], start
    step = timedelta(days=days)
    while t < end:
        t2 = min(t + step, end)
        out.append((t, t2))
        t = t2
    return out


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_series(station_id: str, code: str, start: datetime, end: datetime, cache_dir: str) -> list:
    rows = []
    for a, b in date_chunks(start, end):
        q = urllib.parse.urlencode({
            "time-series-code": code, "from": _iso(a), "to": _iso(b),
            "resolution": "FIFTEEN_MINUTES",
        })
        cache = os.path.join(cache_dir, "data", station_id, f"{code}_{a:%Y%m%d}_{b:%Y%m%d}.json")
        data = fetch_json(f"{API}/stations/{station_id}/data?{q}", cache)
        rows.extend(data if isinstance(data, list) else [])
    return rows


def fetch_events(station_id: str, start: datetime, end: datetime, cache_dir: str) -> list:
    rows = []
    for a, b in date_chunks(start, end):
        q = urllib.parse.urlencode({"time-series-code": "wcp1-events", "from": _iso(a), "to": _iso(b)})
        cache = os.path.join(cache_dir, "data", station_id, f"events_{a:%Y%m%d}_{b:%Y%m%d}.json")
        data = fetch_json(f"{API}/stations/{station_id}/data?{q}", cache)
        rows.extend(data if isinstance(data, list) else [])
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["eventDate"]):
        if r["eventDate"] not in seen:
            seen.add(r["eventDate"])
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def parse_time_ms(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000.0


def merge_series(speed_rows: list, dir_rows: list):
    """Join speed and direction rows on time -> (times_ms, speed_kn, dir_deg)."""
    spd = {r["eventDate"]: r["value"] for r in speed_rows if r.get("value") is not None}
    drn = {r["eventDate"]: r["value"] for r in dir_rows if r.get("value") is not None}
    keys = sorted(spd.keys() & drn.keys())
    times = np.array([parse_time_ms(k) for k in keys], dtype=np.float64)
    return times, np.array([spd[k] for k in keys], dtype=np.float64), np.array([drn[k] for k in keys], dtype=np.float64)


def to_uv(speed_kn, dir_deg):
    """Speed (kn) + set (deg true, toward) -> east/north components in m/s."""
    r = np.radians(dir_deg)
    s = np.asarray(speed_kn, dtype=np.float64) * KN_TO_MS
    return s * np.sin(r), s * np.cos(r)


def axis_speed_kn(u, v, flood_dir_deg: float):
    """Signed speed along the flood axis in knots (+ flood, − ebb)."""
    r = math.radians(flood_dir_deg)
    return (np.asarray(u) * math.sin(r) + np.asarray(v) * math.cos(r)) / KN_TO_MS


def build_feature(station: dict, meta: dict, times, speed_kn, dir_deg, window: tuple, names=None) -> dict:
    u, v = to_uv(speed_kn, dir_deg)
    constituents, mean, rms_cms = fit_constituents(times, u * 100.0, v * 100.0, names or FIT_NAMES)
    lat, lon = float(station["latitude"]), float(station["longitude"])
    chs = {
        "station_code": station["code"],
        "station_id": station["id"],
        "flood_dir": meta.get("floodDirection"),
        "ebb_dir": meta.get("ebbDirection"),
        "peak_kn": round(float(np.max(speed_kn)), 2),
        "fit_rms_kn": round(rms_cms / 100.0 / KN_TO_MS, 3),
        "fit_window": [window[0].strftime("%Y-%m-%d"), window[1].strftime("%Y-%m-%d")],
    }
    return {
        "type": "Feature",
        "id": f"CHS_{station['code']}",
        "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
        "properties": {
            "station_name": (station.get("officialName") or station["code"]).strip(),
            "prediction_method": "harmonic_constituents_currents",
            "data_unit_speed": "meters_per_second",
            "mean_offset": mean,
            "harmonic_constituents": constituents,
            # Non-normative provenance extras (ignored by UTCEF engines).
            "chs": chs,
        },
    }


def convert_station(station: dict, start: datetime, end: datetime, cache_dir: str):
    sid = station["id"]
    meta = fetch_json(f"{API}/stations/{sid}/metadata", os.path.join(cache_dir, "metadata", f"{sid}.json"))
    times, spd, drn = merge_series(
        fetch_series(sid, "wcsp1", start, end, cache_dir),
        fetch_series(sid, "wcdp1", start, end, cache_dir),
    )
    if len(times) == 0:
        raise ValueError("no joint speed+direction predictions in the window")
    span_days = (times[-1] - times[0]) / 86400000.0
    expected = span_days * 1440.0 / STEP_MIN
    if span_days < MIN_SPAN_DAYS or len(times) < MIN_COVERAGE * expected:
        raise ValueError(f"only {len(times)} samples over {span_days:.0f} days")
    first = datetime.fromtimestamp(times[0] / 1000.0, timezone.utc)
    last = datetime.fromtimestamp(times[-1] / 1000.0, timezone.utc)
    return build_feature(station, meta, times, spd, drn, (first, last), names=fit_names_for(span_days))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def region_for(lat: float, lon: float) -> dict:
    return next(r for r in REGIONS if r["match"](lat, lon))


def write_region(region: dict, features: list, out_dir: str) -> str:
    lats = [f["geometry"]["coordinates"][1] for f in features]
    lons = [f["geometry"]["coordinates"][0] for f in features]
    pad = 0.5
    bbox = [round(min(lons) - pad, 2), round(min(lats) - pad, 2), round(max(lons) + pad, 2), round(max(lats) + pad, 2)]
    now = datetime.now(timezone.utc)
    window = features[0]["properties"]["chs"]["fit_window"]
    payload = {
        "metadata": {
            "schema_version": "1.0.0",
            "dataset_version": now.strftime("%Y.%m.%d"),
            "title": region["title"],
            "description": (
                f"Harmonic tidal current predictions for {len(features)} Canadian Hydrographic "
                "Service (CHS) current stations. CHS publishes predictions only as time series, so "
                f"constituents were least-squares fitted to one year ({window[0]} to {window[1]}) "
                "of CHS 15-minute predictions from the IWLS API by signalk-router-data scripts "
                "(https://github.com/marcelrv/signalk-router-data); the fit residual is in each "
                "station's properties.chs.fit_rms_kn. Predictions are purely astronomical; actual "
                "currents can deviate with weather and river flow.\n\n" + CHS_NOTICE
            ),
            "notice": CHS_NOTICE,
            "last_updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "region": {"name": region["title"], "bbox": bbox},
            "copyright": (
                "Contains intellectual property (Data) of the Canadian Hydrographic Service of "
                "the Department of Fisheries and Oceans. © His Majesty the King in Right of Canada."
            ),
            "license": "CHS Licence Agreement — non-commercial use only, not for navigation",
            "license_url": LICENCE_URL,
            "data_sources": [
                {"name": "Canadian Hydrographic Service — IWLS API", "url": "https://api-iwls.dfo-mpo.gc.ca/", "role": "Data Provider"},
            ],
            # Catalog hints consumed by scripts/tide-current/sources/utcef_regions.py.
            "catalog": {
                "source": "chs",
                "contributor": "Canadian Hydrographic Service (CHS), Fisheries and Oceans Canada",
                "url": "https://www.tides.gc.ca/en",
                "tags": ["utcef", "tidal", "currents", "chs", "harmonic", "station-based", "regional", "non-commercial"],
            },
        },
        "dataset": {"type": "FeatureCollection", "features": sorted(features, key=lambda f: f["id"])},
    }
    folder = os.path.join(out_dir, region["folder"])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{region['id']}.utcef")
    text = json.dumps(payload, indent=1, ensure_ascii=False)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{region['id']}.json", text)
    print(f"[Success] {path}: {len(features)} stations, bbox {bbox}, {os.path.getsize(path)/1024:.0f} KiB")
    return path


def parse_day(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main():
    ap = argparse.ArgumentParser(description="CHS IWLS current predictions -> regional UTCEF files")
    ap.add_argument("--cache-dir", default=os.path.join(HERE, "cache"))
    ap.add_argument("--out-dir", default=os.path.normpath(os.path.join(HERE, "..", "..", "regions")))
    ap.add_argument("--start", default="2026-11-17", help="first day of the fit window (UTC, YYYY-MM-DD)")
    ap.add_argument("--days", type=int, default=365, help="length of the fit window in days")
    ap.add_argument("--limit", type=int, default=None, help="convert only the first N stations (debug)")
    ap.add_argument("--station", action="append", default=[], help="only this CHS station code (repeatable)")
    ap.add_argument("--workers", type=int, default=3, help="stations fetched in parallel")
    args = ap.parse_args()

    start = parse_day(args.start)
    end = start + timedelta(days=args.days)
    stations = select_current_stations(fetch_json(f"{API}/stations", os.path.join(args.cache_dir, "stations.json")))
    if args.station:
        stations = [s for s in stations if s["code"] in set(args.station)]
    if args.limit:
        stations = stations[:args.limit]
    print(f"[CHS] {len(stations)} current-prediction stations, window {start:%Y-%m-%d} → {end:%Y-%m-%d}")

    def work(s):
        try:
            return s, convert_station(s, start, end, args.cache_dir), None
        except (RuntimeError, ValueError) as e:
            return s, None, e

    by_region, skipped = {}, 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for s, feat, err in pool.map(work, stations):
            name = (s.get("officialName") or "").strip()
            if err:
                print(f"  [Warning] {s['code']} {name}: {err}")
                skipped += 1
                continue
            c = feat["properties"]["chs"]
            print(f"  {s['code']} {name:28s} peak {c['peak_kn']:5.2f} kn  fit rms {c['fit_rms_kn']:.3f} kn")
            lon, lat = feat["geometry"]["coordinates"]
            by_region.setdefault(region_for(lat, lon)["id"], []).append(feat)
    print(f"[CHS] converted {sum(map(len, by_region.values()))}, skipped {skipped}")

    for region in REGIONS:
        feats = by_region.get(region["id"])
        if feats:
            write_region(region, feats, args.out_dir)
        else:
            print(f"[Note] region {region['id']}: no stations, no file written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
