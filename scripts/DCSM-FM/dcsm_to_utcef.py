#!/usr/bin/env python3
"""Tidal-stream UTCEF for the Dutch Wadden Sea and the Delta (Ooster- and
Westerschelde) from Rijkswaterstaat's DCSM-FM 100 m model.

Input is the daily record built by the Matroos collector (hourly depth-averaged
u/v on the ZUNO v4 grid, ~200 m in the Wadden, ~70-110 m in the Delta, cropped
to the Dutch coast; see the collector README). Steps:

1. Harmonic analysis of every model cell in one vectorised least-squares solve.
   Nodal corrections and Greenwich astronomical arguments come from UTide, so
   the fitted phases are Greenwich phase lags as UTCEF requires. Constituents
   are picked by the Rayleigh criterion (factor RAYLEIGH) for the record
   length; S2, N2, K2, P1 and Q1 are inferred from M2/S2/K1/O1 (ratios from the
   FES2014 netherlands.utcef) while the record is too short to separate them. Dry samples count as zero
   current, which is what a vessel on a drying flat should be told.
2. Point placement. The tidal-currents plugin answers a position with the
   NEAREST station, so a point set is judged by the error each model cell gets
   from its nearest point. Points are seeded along the routing graph's edges
   (where people actually sail) and then added greedily where that error, or
   the distance to the nearest point, exceeds a per-class tolerance: tight on
   routed channels, looser in other channels and offshore, loose on flats and slow water.
3. UTCEF output (ZIP container with one JSON member), points named after the
   routing database's water body (channel) where they sit on a route.

Prototype: not for navigation.
"""
import argparse
import datetime as dt
import json
import math
import sqlite3
import sys
import zipfile
from pathlib import Path

import netCDF4
import numpy as np
from scipy.spatial import cKDTree
from utide import _ut_constants as UTC
from utide.harmonics import FUV

KTS = 1.943844
# `file` is the output basename and so the catalog id (utcef_<file>): keep it
# stable across rebuilds; mark test status in the title, not the file name.
TEST_RELEASE = True
REGIONS = {
    "wadden": {
        "file": "wadden_dcsm_hd",
        "title": "Dutch Wadden Sea — High-Density Tidal Streams",
        "bbox": [4.55, 52.88, 7.35, 53.72],
    },
    "delta": {
        "file": "delta_dcsm_hd",
        "title": "Ooster-/Westerschelde, Voordelta & Maasmond — High-Density Tidal Streams",
        "bbox": [3.0, 51.20, 4.42, 52.05],  # north edge takes in Haringvliet, Slijkgat, Maasmond
    },
}
CATALOG = {
    "source": "rws_dcsm",
    "contributor": "Model data: Rijkswaterstaat (DCSM-FM 100 m via NOOS Matroos). Harmonic analysis and "
                   "point placement: signalk-router-data.",
    "url": "https://noos.matroos.rws.nl/",
    "tags": ["utcef", "tidal", "currents", "rws", "dcsm-fm", "harmonic", "high-density", "regional"],
}
# Preference order; a constituent is fitted when it is Rayleigh-separable from
# every constituent already chosen. 2MS6 etc. are left out because the plugin's
# astro.ts does not know them.
CANDIDATES = ["M2", "S2", "K1", "O1", "M4", "MS4", "M6", "N2", "K2", "P1", "Q1",
              "MN4", "S4", "M8", "MK3", "M3", "NU2", "L2", "2N2", "MU2", "2SM2",
              "S6", "MSF", "MM", "MF"]
# Inferred while the record cannot separate them: minor <- first reference in
# this list that is fitted, with the FES2014 amplitude ratio and phase offset.
INFER = {"S2": ["M2"], "N2": ["M2"], "K2": ["S2", "M2"], "O1": ["K1"], "P1": ["K1"], "Q1": ["O1", "K1"]}
RAYLEIGH = 1.0  # factor on the 1/T separation
# Tested on the 15-day Nov 2026 record against the tide-only run (astro_compare.py):
# RAYLEIGH 1.2 + S2 inferred from FES gave a flatter spring-neap cycle but a
# larger RMS error (Wadden 0.225 vs 0.177 m/s) because FES's S2 phase is off
# locally; fitting S2 freely exaggerates the spring-neap swing (0.68-1.07).
# Both go away once the record passes ~29 days.
# Extra conditions on top of the Rayleigh test: minimum record length (days)
# and constituents that must already be fitted. Without these, a short record
# fits 2N2 (which passes Rayleigh against M2) in place of the much larger N2,
# and long-period terms soak up the weather.
MIN_DAYS = {"MSF": 60, "MM": 90, "MF": 60, "2SM2": 30}
REQUIRES = {"2N2": "N2", "MU2": "N2", "NU2": "N2", "L2": "N2", "MN4": "N2", "Q1": "O1"}
# per class: RMS vector error tolerance (m/s) and max distance to nearest point (m)
CLASSES = {
    "route":      {"tol": 0.05, "maxd": 500,  "code": 0},  # routed, through a real stream
    "route_slow": {"tol": 0.07, "maxd": 1000, "code": 1},  # routed, slow water or over flats
    "channel":    {"tol": 0.07, "maxd": 1000, "code": 2},  # fast stream, off the routes
    "slow":       {"tol": 0.10, "maxd": 2000, "code": 3},  # weak flow near the routes
    "flat":       {"tol": 0.15, "maxd": 2000, "code": 4},  # dries at low water
    "open":       {"tol": 0.08, "maxd": 4000, "code": 5},  # far from any route
}
FAST_M2 = 0.35   # m/s M2 major-axis amplitude: a real stream
FLAT_DRY = 0.03  # fraction of samples dry (u = v = 0 exactly): a drying flat
MIN_SPACING_M = 150
ROUTE_DIST_M = 200
OPEN_DIST_M = 2500  # farther than this from any routed edge = open water
SEED_STEP_M = 500

CONST = UTC.ut_constants.const
NAMES = list(CONST.name)


def cidx(name):
    return NAMES.index(name)


def freq_cph(name):
    return float(CONST.freq[cidx(name)])


def to_datenum(minutes_since_epoch):
    # UTide (Python) time base: proleptic-ordinal days, 1970-01-01 = 719163
    # (NOT MATLAB datenum 719529: that is 366 days off and wrecks M2/O1 phases;
    # caught by validate_utcef.py running the plugin's own astro.ts)
    return 719163.0 + np.asarray(minutes_since_epoch, dtype=float) / 1440.0


# ── record ──────────────────────────────────────────────────────────────────

class Record:
    def __init__(self, data_dir: Path):
        self.files = sorted(data_dir.glob("*/dcsm100m_nl_*.nc"))
        if not self.files:
            sys.exit(f"no daily files under {data_dir}")
        with netCDF4.Dataset(data_dir / "grid.nc") as g:
            self.lat = g["lat"][:].filled(np.nan)
            self.lon = g["lon"][:].filled(np.nan)
            self.z = g["z"][:].filled(np.nan)
        t = []
        for f in self.files:
            with netCDF4.Dataset(f) as d:
                t.append(d["time"][:].astype(np.int64))
        self.minutes = np.concatenate(t)
        if np.any(np.diff(self.minutes) <= 0):
            sys.exit("record times are not strictly increasing")
        self.days = (self.minutes[-1] - self.minutes[0]) / 1440.0 + 1 / 24

    def read(self, var, rows: slice, cols: slice):
        """(T, nrows, ncols) float32 with NaN where dry/missing."""
        parts = []
        for f in self.files:
            with netCDF4.Dataset(f) as d:
                parts.append(d[var][:, rows, cols].astype(np.float32).filled(np.nan))
        return np.concatenate(parts, axis=0)


# ── harmonic analysis ───────────────────────────────────────────────────────

def choose_constituents(days):
    min_sep = RAYLEIGH / (days * 24.0)  # Rayleigh, cycles/hour
    chosen = []
    for c in CANDIDATES:
        # the mean (frequency 0) counts as a fitted term too
        if days < MIN_DAYS.get(c, 0) or (c in REQUIRES and REQUIRES[c] not in chosen):
            continue
        if freq_cph(c) >= min_sep and all(abs(freq_cph(c) - freq_cph(o)) >= min_sep for o in chosen):
            chosen.append(c)
    inferred = {}
    for m, refs in INFER.items():
        if m in chosen:
            continue
        ref = next((r for r in refs if r in chosen), None)
        if ref:
            inferred[m] = ref
    return chosen, inferred


FES_CITATION = ("FES2014 was produced by Noveltis, Legos and CLS and distributed by Aviso+, "
                "with support from Cnes (https://www.aviso.altimetry.fr/)")


def add_fes_credit(meta: dict, inferred: dict) -> dict:
    """Credit FES2014 when constituents were inferred with its ratios.

    The ratios come from netherlands.utcef, which is under the AVISO+ licence
    (scientific and non-commercial redistribution) and requires the FES2014
    citation. Until the inference no longer uses FES (planned: fit N2 directly
    from a 29+ day record, infer K2 from the equilibrium ratio), the output
    says so in its licence, copyright, citation and data sources."""
    if not inferred:
        return meta
    names = "/".join(inferred)
    meta["license"] = (f"CC-BY-4.0, except the {names} constituents, inferred with FES2014 ratios: "
                       "AVISO+ licence (scientific and non-commercial use)")
    meta["copyright"] += (f" The {names} constituents are inferred with amplitude ratios and phase "
                          "offsets from FES2014 (Copyright Legos/Noveltis/Cnes/CLS, AVISO+ licence for "
                          "scientific and non-commercial use).")
    meta["citation_required"] += f"; {FES_CITATION}"
    meta["data_sources"].append({
        "name": "FES2014 (via AVISO+)", "url": "https://www.aviso.altimetry.fr/", "role": "Inference ratios",
        "license": "AVISO+ License Agreement (scientific and non-commercial)",
        "notes": f"Median {names} ratios to M2/S2 from regions/europe/netherlands.utcef"})
    # metadata.description and catalog.contributor reach tide-current-index.json.
    meta["description"] += (f" The {names} values are inferred with FES2014 ratios and fall under the "
                            f"AVISO+ licence (scientific and non-commercial use). {FES_CITATION}.")
    meta["catalog"] = {**meta["catalog"],
                       "contributor": meta["catalog"]["contributor"] + " Inference ratios: FES2014 (AVISO+)."}
    return meta


def fes_inference_ratios(fes_path: Path, bbox, pairs):
    """Median complex ratio minor/reference on the major axis, from FES2014.
    pairs: {minor: reference}. Returns {minor: (amplitude ratio, g_minor - g_ref)}."""
    with zipfile.ZipFile(fes_path) as z:
        feats = json.loads(z.read(z.namelist()[0]))["dataset"]["features"]
    out = {}
    for minor, ref in pairs.items():
        rs = []
        for f in feats:
            lo, la = f["geometry"]["coordinates"]
            hc = f["properties"].get("harmonic_constituents", {})
            if not (bbox[0] - 0.5 <= lo <= bbox[2] + 0.5 and bbox[1] - 0.5 <= la <= bbox[3] + 0.5):
                continue
            if minor not in hc or ref not in hc or "u_amplitude" not in hc[ref]:
                continue
            a, b = hc[ref], hc[minor]
            axis = "u" if a["u_amplitude"] >= a["v_amplitude"] else "v"
            if a[f"{axis}_amplitude"] < 0.05:
                continue
            ca = a[f"{axis}_amplitude"] * np.exp(-1j * np.radians(a[f"{axis}_phase_g"]))
            cb = b[f"{axis}_amplitude"] * np.exp(-1j * np.radians(b[f"{axis}_phase_g"]))
            rs.append(cb / ca)
        rs = [r for r in rs if r != 0]  # a zero ratio has no phase
        if rs:
            rs = np.array(rs)
            r = np.median(np.abs(rs))
            ph = np.degrees(np.angle(np.median(rs / np.abs(rs))))
            out[minor] = (float(r), float(-ph))  # g_minor = g_ref + dg
    return out


def design_matrix(minutes, chosen, inferred, ratios, lat):
    t = to_datenum(minutes)
    inf = list(inferred)
    allc = chosen + inf
    F, U, V = FUV(t, t[len(t) // 2], np.array([cidx(c) for c in allc]), lat, [False, False, False, False])
    arg = 2 * np.pi * (U + V)  # radians, (T, nc)
    cols = [np.ones(len(t))]
    for k, c in enumerate(chosen):
        cc, ss = F[:, k] * np.cos(arg[:, k]), F[:, k] * np.sin(arg[:, k])
        for m in inf:
            if inferred[m] == c:
                j = len(chosen) + inf.index(m)
                r, dg = ratios[m]
                a2 = arg[:, j] - np.radians(dg)
                cc = cc + r * F[:, j] * np.cos(a2)
                ss = ss + r * F[:, j] * np.sin(a2)
        cols += [cc, ss]
    return np.column_stack(cols)


def solve_block(X, Y):
    """Least squares for many cells sharing X. Y: (T, N) with NaN = dry -> 0."""
    Y = np.nan_to_num(Y, nan=0.0)
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    resid = Y - X @ coef
    return coef, np.sqrt(np.mean(resid ** 2, axis=0)), np.sqrt(np.mean((Y - Y.mean(0)) ** 2, axis=0))


# ── routing graph ───────────────────────────────────────────────────────────

def load_routes(db: Path, bbox):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = c.execute(
        """select a.lon, a.lat, b.lon, b.lat, coalesce(w.name, '')
           from edges e join nodes a on a.id = e.source join nodes b on b.id = e.target
           left join edge_sequences s on s.id = e.edge_sequence_id
           left join water_bodies w on w.id = s.water_body_id
           where e.source < e.target and a.lon between ? and ? and a.lat between ? and ?""",
        (bbox[0], bbox[2], bbox[1], bbox[3])).fetchall()
    return rows


class LocalXY:
    def __init__(self, lat0):
        self.k = math.cos(math.radians(lat0))

    def __call__(self, lon, lat):
        return np.column_stack([np.asarray(lon) * self.k * 111195.0, np.asarray(lat) * 111195.0])


def route_samples(rows, xy, step):
    """Points every `step` m along each edge (and edge midpoints), with names."""
    pts, names = [], []
    for lo1, la1, lo2, la2, name in rows:
        p1, p2 = xy([lo1], [la1])[0], xy([lo2], [la2])[0]
        n = max(1, int(np.ceil(np.hypot(*(p2 - p1)) / step)))
        for s in np.linspace(0, 1, n + 1):
            pts.append(p1 + s * (p2 - p1))
            names.append(name)
    return np.array(pts), names


def thin_points(pts, min_dist):
    """Greedy Poisson-disk thinning in input order (grid hash)."""
    cell = {}
    keep = []
    for p in pts:
        gx, gy = int(p[0] // min_dist), int(p[1] // min_dist)
        ok = True
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in cell.get((gx + dx, gy + dy), ()):
                    if (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 < min_dist ** 2:
                        ok = False
                        break
                if not ok:
                    break
            if not ok:
                break
        if ok:
            cell.setdefault((gx, gy), []).append(p)
            keep.append(p)
    return np.array(keep)


# ── placement ───────────────────────────────────────────────────────────────

def vec_rms_err(C, i, j):
    """RMS tidal vector difference over time between cells i and j (constituent
    terms are orthogonal over a long record): sqrt(1/2 sum |dc|^2). The record
    mean is left out: over a short record it is mostly wind-driven."""
    dc = C[i, 2:] - C[j, 2:]
    return np.sqrt(0.5 * np.sum(np.abs(dc) ** 2, axis=1))


def place(P, C, cls_tol, cls_maxd, seeds, max_rounds=400, batch=300):
    """P: (N,2) metres. C: (N, 2+2K) complex [mean_u, mean_v, cu_k..., cv_k...].
    seeds: initial cell indices. Returns chosen cell indices."""
    chosen = list(dict.fromkeys(seeds))
    blocked = np.zeros(len(P), bool)
    for rnd in range(max_rounds):
        tree = cKDTree(P[chosen])
        d, k = tree.query(P)
        near = np.asarray(chosen)[k]
        err = vec_rms_err(C, np.arange(len(P)), near)
        exceed = np.maximum(err / cls_tol, d / cls_maxd)
        exceed[blocked] = 0
        cand = np.where(exceed > 1)[0]
        if len(cand) == 0:
            break
        cand = cand[np.argsort(-exceed[cand])]
        added = []
        for i in cand:
            if len(added) >= batch:
                break
            if d[i] < MIN_SPACING_M:
                blocked[i] = True
                continue
            # keep points added in one round apart: half the gap to the old set
            sep = max(MIN_SPACING_M, 0.5 * d[i])
            if added and np.min(np.hypot(*(P[added] - P[i]).T)) < sep:
                continue
            added.append(i)
        if not added:
            break
        chosen += added
        if rnd % 10 == 0:
            print(f"    round {rnd}: {len(chosen)} points, {len(cand)} cells over tolerance")
    return np.asarray(chosen)


# ── main ────────────────────────────────────────────────────────────────────

def build(region, cfg, rec, args):
    bbox = cfg["bbox"]
    lat, lon = rec.lat, rec.lon
    inreg = np.isfinite(lat) & (lon >= bbox[0]) & (lon <= bbox[2]) & (lat >= bbox[1]) & (lat <= bbox[3])
    r_idx = np.where(inreg.any(axis=1))[0]
    c_idx = np.where(inreg.any(axis=0))[0]
    rows = slice(r_idx.min(), r_idx.max() + 1)
    cols = slice(c_idx.min(), c_idx.max() + 1)
    chosen, inferred = choose_constituents(rec.days)
    ratios = fes_inference_ratios(args.fes, bbox, inferred) if inferred else {}
    inferred = {m: ref for m, ref in inferred.items() if m in ratios}
    print(f"[{region}] record {rec.days:.1f} days ({len(rec.minutes)} h); fitted {chosen}; "
          f"inferred {[(m, ref, round(ratios[m][0], 3), round(ratios[m][1], 1)) for m, ref in inferred.items()]}")
    X = design_matrix(rec.minutes, chosen, inferred, ratios, lat=float(np.nanmean(lat[inreg])))

    u = rec.read("velu", rows, cols)
    v = rec.read("velv", rows, cols)
    T, ny, nx = u.shape
    sub = inreg[rows, cols]
    wetfrac = np.mean(np.isfinite(u), axis=0)
    cell = sub & (wetfrac > 0.02)
    U = u.reshape(T, -1)[:, cell.ravel()]
    Vv = v.reshape(T, -1)[:, cell.ravel()]
    del u, v
    dryfrac = np.mean((U == 0) & (Vv == 0), axis=0)  # dry cells read exactly 0
    cu, ru, su = solve_block(X, U)
    cv, rv, sv = solve_block(X, Vv)
    peak = np.nanmax(np.hypot(np.nan_to_num(U), np.nan_to_num(Vv)), axis=0)
    del U, Vv
    K = len(chosen)
    comp_u = cu[1::2] - 1j * cu[2::2]  # A e^{-ig}
    comp_v = cv[1::2] - 1j * cv[2::2]
    Cmat = np.column_stack([cu[0], cv[0], comp_u.T, comp_v.T]).astype(np.complex128)
    clat = lat[rows, cols][cell]
    clon = lon[rows, cols][cell]
    cz = rec.z[rows, cols][cell]
    cwet = wetfrac[cell]
    tidal_frac = 1 - (ru ** 2 + rv ** 2) / np.maximum(su ** 2 + sv ** 2, 1e-12)
    print(f"[{region}] {cell.sum()} cells fitted; median tidal variance explained "
          f"{np.median(tidal_frac):.3f}; median residual {np.median(np.hypot(ru, rv)):.3f} m/s")

    xy = LocalXY(np.mean(bbox[1::2]))
    P = xy(clon, clat)
    routes = load_routes(args.routing, bbox)
    rp, rnames = route_samples(routes, xy, 50)
    dist_route, ridx = cKDTree(rp).query(P)
    m2_major = np.hypot(np.abs(comp_u[0]), np.abs(comp_v[0]))
    # Classes from flow strength, drying (dry samples read exactly 0 — the
    # product does not mask them) and the distance to the routed network.
    fast = m2_major > FAST_M2
    flat = dryfrac > FLAT_DRY
    on_route = dist_route < ROUTE_DIST_M
    cls = np.full(len(P), CLASSES["slow"]["code"])
    cls[fast] = CLASSES["channel"]["code"]
    cls[flat] = CLASSES["flat"]["code"]
    cls[dist_route > OPEN_DIST_M] = CLASSES["open"]["code"]
    cls[on_route] = CLASSES["route_slow"]["code"]
    cls[on_route & fast & ~flat] = CLASSES["route"]["code"]
    by_code = {v["code"]: v for v in CLASSES.values()}
    tol = np.array([by_code[c]["tol"] for c in cls]) * args.tol_scale
    maxd = np.array([by_code[c]["maxd"] for c in cls])

    # seeds: route samples thinned to SEED_STEP_M apart over the whole network
    # (edges are ~200 m long, so per-edge sampling would seed every node),
    # snapped to the nearest wet cell
    sp = thin_points(rp, SEED_STEP_M)
    ds, si = cKDTree(P).query(sp)
    seeds = si[ds < 150]
    print(f"[{region}] {len(routes)} route edges; {len(np.unique(seeds))} seed points")
    pts = place(P, Cmat, tol, maxd, list(np.unique(seeds)))
    print(f"[{region}] {len(pts)} points placed")

    # final error stats
    d, k = cKDTree(P[pts]).query(P)
    err = vec_rms_err(Cmat, np.arange(len(P)), pts[k])
    for name, c in CLASSES.items():
        m = cls == c["code"]
        if m.any():
            print(f"    {name:8s}: {m.sum():6d} cells, {np.sum(np.isin(pts, np.where(m)[0])):5d} points, "
                  f"RMS err p50/p95 {np.median(err[m]):.3f}/{np.percentile(err[m], 95):.3f} m/s, "
                  f"dist p95 {np.percentile(d[m], 95):.0f} m")

    names_all = chosen + list(inferred)
    feats = []
    for i in pts:
        hc = {}
        for kk, c in enumerate(names_all):
            if c in chosen:
                au, av = comp_u[kk, i], comp_v[kk, i]
            else:
                r, dg = ratios[c]
                ref = chosen.index(inferred[c])
                rot = r * np.exp(-1j * np.radians(dg))
                au, av = comp_u[ref, i] * rot, comp_v[ref, i] * rot
            if max(abs(au), abs(av)) < 0.002 and c not in ("M2", "S2"):
                continue  # keep M2/S2 always: a station without constituents is skipped by the plugin
            hc[c] = {"u_amplitude": round(float(abs(au)), 4),
                     "u_phase_g": round(float(np.degrees(-np.angle(au)) % 360), 2),
                     "v_amplitude": round(float(abs(av)), 4),
                     "v_phase_g": round(float(np.degrees(-np.angle(av)) % 360), 2)}
        name = rnames[ridx[i]] if on_route[i] and rnames[ridx[i]] else ""
        pos = f"{clat[i]:.4f},{clon[i]:.4f}"
        feats.append({
            "type": "Feature",
            "id": f"DCSM_{clat[i]:.4f}_{clon[i]:.4f}".replace(".", "p"),
            "geometry": {"type": "Point", "coordinates": [round(float(clon[i]), 5), round(float(clat[i]), 5)]},
            "properties": {
                "station_name": f"{name} {pos}" if name else pos,
                "prediction_method": "harmonic_constituents_currents",
                "data_unit_speed": "meters_per_second",
                # the fitted mean is kept: against the tide-only run it lowers the RMS
                # (Delta p50 0.174 -> 0.155 m/s), so it is mostly tidal residual flow
                "mean_offset": {"u_residual": round(float(cu[0, i]), 4), "v_residual": round(float(cv[0, i]), 4)},
                "harmonic_constituents": hc,
            },
        })
    title = cfg["title"] + (" (TEST)" if TEST_RELEASE else "")
    test_note = ""
    if TEST_RELEASE:
        test_note = (
            f"TEST RELEASE built from a short {rec.days:.0f}-day model record: tidal set is good (median "
            "within a few degrees of the tide-only model), but spring-neap variation is exaggerated "
            "(speeds up to ~30% low at neaps, slightly high at springs) until a 30+ day rebuild. ")
    t0 = dt.datetime(1970, 1, 1) + dt.timedelta(minutes=int(rec.minutes[0]))
    t1 = dt.datetime(1970, 1, 1) + dt.timedelta(minutes=int(rec.minutes[-1]))
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "metadata": {
            "schema_version": "1.0.0",
            "dataset_version": now.strftime("%Y.%m.%d"),
            "title": title,
            "description": (test_note +
                "Tidal stream harmonics derived by harmonic analysis of Rijkswaterstaat's operational "
                "DCSM-FM 100 m model (dflowfm2d_noordzee_100m, depth-averaged, via the open NOOS Matroos "
                f"server) over {t0:%Y-%m-%d} – {t1:%Y-%m-%d} ({rec.days:.0f} days). Fitted: "
                f"{', '.join(chosen)}; inferred from FES2014 ratios: {', '.join(f'{m} (from {r})' for m, r in inferred.items()) or 'none'}. "
                "Points follow the routed channels of the signalk-router-data Netherlands network, denser "
                "where the stream changes quickly. Dry periods count as zero current. "
                "Not for official navigation: derived from numerical model output, not an official "
                "Rijkswaterstaat or Hydrographic Office product and not endorsed by them; no liability is "
                "accepted for errors or for incidents resulting from its use."),
            "last_updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "region": {"name": title, "bbox": bbox},
            "copyright": ("Tidal current harmonic constituents derived from DCSM-FM model output (NOOS Matroos), "
                          "provided by Rijkswaterstaat (Ministry of Infrastructure and Water Management, the "
                          "Netherlands) and Deltares as open data (CC0 1.0). Harmonic analysis, point placement "
                          "and packaging: signalk-router-data."),
            "license": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "citation_required": "Source: Rijkswaterstaat / Deltares DCSM-FM (NOOS Matroos); processed by signalk-router-data",
            "data_sources": [
                {"name": "Rijkswaterstaat NOOS Matroos", "url": "https://noos.matroos.rws.nl/", "role": "Distributor",
                 "license": "CC0-1.0 (Rijkswaterstaat open data)", "license_url": "https://www.rijkswaterstaat.nl/zakelijk/open-data"},
                {"name": "Rijkswaterstaat / Deltares DCSM-FM", "role": "Model Developer"},
            ],
            "catalog": {**CATALOG, "tags": CATALOG["tags"] + (["test"] if TEST_RELEASE else [])},
        },
        "dataset": {"type": "FeatureCollection", "features": feats},
    }
    add_fes_credit(payload["metadata"], inferred)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{cfg['file']}.utcef"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{cfg['file']}.json", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"[{region}] wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {len(feats)} stations)")
    np.savez_compressed(args.out / f"{region}_diag.npz", lat=clat, lon=clon, cls=cls, err=err, pts=pts,
                        peak=peak, z=cz, wet=cwet, dry=dryfrac, tidal_frac=tidal_frac)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path("/home/node/signalkdev/matroos-collector/data"))
    ap.add_argument("--routing", type=Path, required=True, help="nl-routing.sqlite")
    ap.add_argument("--fes", type=Path, default=Path(__file__).resolve().parents[2] / "regions/europe/netherlands.utcef")
    ap.add_argument("--region", choices=[*REGIONS, "all"], default="all")
    ap.add_argument("--out", type=Path, default=Path("dist"))
    ap.add_argument("--tol-scale", type=float, default=2.5,
                    help="multiply all error tolerances (2.5: ~10.8k Wadden + 5.5k Delta points on the Nov 2026 record)")
    args = ap.parse_args()
    rec = Record(args.data)
    for r in (REGIONS if args.region == "all" else [args.region]):
        build(r, REGIONS[r], rec, args)


if __name__ == "__main__":
    main()
