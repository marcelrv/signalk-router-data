#!/usr/bin/env python3
"""Tidal-stream UTCEF for the Norwegian coast from MET Norway's NorKyst v3 (800 m)
surface currents.

Same algorithm as scripts/DCSM-FM/dcsm_to_utcef.py (imported, not copied):

1. Harmonic analysis of every wet model cell of a region, tile by tile in blocks
   of cells sharing one design matrix (UTide nodal corrections, Greenwich phase
   lags). The input is the ~366-day tile cache written by fetch_record.py, so
   every constituent in choose_constituents is fitted directly: nothing is
   inferred and no FES2014 ratios are used.
2. Point placement. The tidal-currents plugin answers a position with the
   nearest station, so points are added greedily until each cell is within a
   per-class tolerance of its nearest point. "Routes" here are the cells with
   leisure-vessel density (EMODnet, density.npz from ais_density.py) at or
   above a regional percentile; the density only chooses WHERE points go, no
   vessel data is in the output. There is no drying in this model, so no
   'flat' class.
3. UTCEF output (ZIP with one JSON member) plus a diagnostic npz per region.

Straits narrower than the 800 m grid (Saltstraumen, Rystraumen, ...) are land
in the model and carry no current. Prototype: not for navigation.
"""
import argparse
import datetime as dt
import json
import sys
import zipfile
from pathlib import Path

import netCDF4
import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "DCSM-FM"))
from dcsm_to_utcef import (CLASSES, FAST_M2, KTS, MIN_SPACING_M, OPEN_DIST_M,  # noqa: E402,F401
                           ROUTE_DIST_M, SEED_STEP_M, LocalXY, choose_constituents,
                           cidx, design_matrix, place, thin_points, to_datenum,
                           vec_rms_err)

TEST_RELEASE = True
FILL = -32767
TILE = 64
# TODO: import bounds from scripts/tide-current/sources/norkyst.py once branch norkyst-currents merges.
REGIONS = {
    "south": {"file": "norkyst_south_hd", "title": "Skagerrak, Oslofjord & Sørlandet — High-Density Tidal Streams",
              "bbox": [5.5, 57.8, 11.5, 60.2]},
    "west": {"file": "norkyst_west_hd", "title": "West coast: Stavanger – Bergen – Ålesund – Trondheim — High-Density Tidal Streams",
             "bbox": [4.0, 58.8, 11.0, 64.0]},
    "nordland": {"file": "norkyst_nordland_hd", "title": "Helgeland – Bodø – Lofoten — High-Density Tidal Streams",
                 "bbox": [9.5, 63.7, 17.0, 68.0]},
    "north": {"file": "norkyst_north_hd", "title": "Vesterålen – Tromsø – Finnmark — High-Density Tidal Streams",
              "bbox": [12.0, 67.5, 31.5, 71.5]},
}
CATALOG = {
    "source": "met_norkyst",
    "contributor": "Model data: Norwegian Meteorological Institute (MET Norway), NorKyst v3. Harmonic analysis and "
                   "point placement: signalk-router-data. Point positions chosen with EMODnet vessel density "
                   "(Cogea Srl).",
    "url": "https://thredds.met.no/",
    "tags": ["utcef", "tidal", "currents", "norkyst", "met-norway", "harmonic", "high-density", "regional"],
}


# ── cache ───────────────────────────────────────────────────────────────────

def load_grid(data: Path):
    with netCDF4.Dataset(data / "grid.nc") as g:
        return g["lat"][:].filled(np.nan), g["lon"][:].filled(np.nan), g["wet"][:].astype(bool)


def find_tiles(data, only, months_only):
    """{(y0, x0): [(month, path), ...]} of complete files (never *.tmp)."""
    out = {}
    for d in sorted((data / "fit").glob("tile_*")):
        if only and d.name not in only:
            continue
        y0, x0 = (int(s) for s in d.name.split("_")[1:3])
        fs = [(f.stem, f) for f in sorted(d.glob("????-??.nc"))
              if not months_only or f.stem in months_only]
        if fs:
            out[(y0, x0)] = fs
    return out


def read_tile(files, sel):
    """(T, ncells) float32 u, v (m/s, NaN = missing) for cells `sel` (tile-shaped bool)."""
    us, vs = [], []
    for _, f in files:
        with netCDF4.Dataset(f) as d:
            for name, acc in (("u_eastward", us), ("v_northward", vs)):
                var = d[name]
                var.set_auto_maskandscale(False)
                a = var[:].reshape(var.shape[0], -1)[:, sel.ravel()]
                acc.append(np.where(a == FILL, np.nan, a.astype(np.float32) * np.float32(0.001)))
    return np.concatenate(us), np.concatenate(vs)


def read_times(files):
    t = []
    for _, f in files:
        with netCDF4.Dataset(f) as d:
            t.append(d["time"][:].astype(np.float64))
    return np.concatenate(t)  # seconds since epoch


def fit_block(X, Y):
    """Least squares for cells sharing X. NaN samples are excluded (per-cell
    masked solve, only for the cells that have gaps)."""
    bad = np.isnan(Y).any(axis=0)
    coef = np.empty((X.shape[1], Y.shape[1]))
    rms = np.empty(Y.shape[1])
    std = np.empty(Y.shape[1])
    ok = ~bad
    if ok.any():
        c, *_ = np.linalg.lstsq(X, Y[:, ok], rcond=None)
        coef[:, ok] = c
        rms[ok] = np.sqrt(np.mean((Y[:, ok] - X @ c) ** 2, axis=0))
        std[ok] = Y[:, ok].std(axis=0)
    for j in np.where(bad)[0]:
        m = np.isfinite(Y[:, j])
        if m.sum() < 2 * X.shape[1]:
            coef[:, j], rms[j], std[j] = np.nan, np.nan, np.nan
            continue
        c, *_ = np.linalg.lstsq(X[m], Y[m, j], rcond=None)
        coef[:, j] = c
        rms[j] = np.sqrt(np.mean((Y[m, j] - X[m] @ c) ** 2))
        std[j] = Y[m, j].std()
    return coef, rms, std


def mean_aware_err(C, i, j):
    """RMS vector difference including the record mean (exported as mean_offset):
    sqrt(|dmean_u|^2 + |dmean_v|^2 + 0.5 sum|dc|^2)."""
    dm = np.abs(C[i, :2].real - C[j, :2].real)
    dc = C[i, 2:] - C[j, 2:]
    return np.sqrt(np.sum(dm ** 2, axis=1) + 0.5 * np.sum(np.abs(dc) ** 2, axis=1))


# ── region build ────────────────────────────────────────────────────────────

def build(region, cfg, ctx, args):
    bbox = cfg["bbox"]
    lat, lon, wet, dens, dp = ctx["lat"], ctx["lon"], ctx["wet"], ctx["dens"], ctx["dp"]
    inreg = wet & np.isfinite(lat) & (lon >= bbox[0]) & (lon <= bbox[2]) & (lat >= bbox[1]) & (lat <= bbox[3])
    ny, nx = wet.shape
    tiles = {k: v for k, v in ctx["tiles"].items()
             if inreg[k[0]:k[0] + TILE, k[1]:k[1] + TILE].any()}
    if not tiles:
        print(f"[{region}] no cached tiles in region; skipped")
        return
    # months common to all tiles of the region (the fit needs one time axis)
    months = sorted(set.intersection(*[{m for m, _ in v} for v in tiles.values()]))
    if not months:
        print(f"[{region}] tiles share no common month; skipped")
        return
    tiles = {k: [x for x in v if x[0] in months] for k, v in tiles.items()}
    first = next(iter(tiles.values()))
    secs = read_times(first)
    if np.any(np.diff(secs) <= 0):
        sys.exit(f"[{region}] record times are not strictly increasing")
    dh = np.diff(secs) / 3600.0
    gaps = np.where(dh > 1.01)[0]
    for g in gaps[:10]:
        print(f"[{region}] gap: {dh[g]:.1f} h after {dt.datetime.utcfromtimestamp(secs[g])}")
    if len(gaps) > 10:
        print(f"[{region}] ... {len(gaps)} gaps in total")
    minutes = secs / 60.0
    days = (secs[-1] - secs[0]) / 86400.0 + 1 / 24
    chosen, inferred = choose_constituents(days)
    if args.drop_long_period:  # experiment: long-period terms may soak up non-tidal (weather/seasonal) flow
        chosen = [c for c in chosen if c not in ("SA", "SSA", "MSM", "MM", "MSF", "MF")]
    if inferred:
        print(f"[{region}] record {days:.1f} d too short to fit {list(inferred)}; dropped (no FES inference)")
    print(f"[{region}] {len(tiles)} tiles, months {months[0]}..{months[-1]} ({len(months)}), "
          f"{len(secs)} h = {days:.1f} d; fitted {len(chosen)}: {chosen}")
    X = design_matrix(minutes, chosen, {}, {}, lat=float(np.mean(bbox[1::2])))
    K = len(chosen)

    parts = {k: [] for k in ("lat", "lon", "h", "cu", "cv", "ru", "rv", "su", "sv", "peak", "gap")}
    for n, ((y0, x0), files) in enumerate(sorted(tiles.items())):
        sel = np.zeros((TILE, TILE), bool)
        blk = inreg[y0:y0 + TILE, x0:x0 + TILE]
        sel[:blk.shape[0], :blk.shape[1]] = blk
        if not np.array_equal(read_times(files), secs):
            print(f"[{region}] tile {y0},{x0}: time axis differs from the region axis; skipped")
            continue
        U, V = read_tile(files, sel[:blk.shape[0], :blk.shape[1]])
        cu, ru, su = fit_block(X, U)
        cv, rv, sv = fit_block(X, V)
        parts["peak"].append(np.nanmax(np.hypot(U, V), axis=0))
        parts["gap"].append(np.isnan(U).any(axis=0))
        del U, V
        for k, a in (("cu", cu), ("cv", cv), ("ru", ru), ("rv", rv), ("su", su), ("sv", sv)):
            parts[k].append(a.T if a.ndim == 2 else a)
        parts["lat"].append(lat[y0:y0 + TILE, x0:x0 + TILE][blk])
        parts["lon"].append(lon[y0:y0 + TILE, x0:x0 + TILE][blk])
        parts["h"].append(ctx["h"][y0:y0 + TILE, x0:x0 + TILE][blk])
        print(f"    tile {y0:4d},{x0:4d}: {blk.sum()} cells fitted ({n + 1}/{len(tiles)})", flush=True)
    A = {k: np.concatenate(v) for k, v in parts.items()}
    good = np.isfinite(A["cu"]).all(axis=1) & np.isfinite(A["cv"]).all(axis=1)
    A = {k: v[good] for k, v in A.items()}
    clat, clon = A["lat"], A["lon"]
    cu, cv = A["cu"], A["cv"]  # (N, 1+2K)
    comp_u = (cu[:, 1::2] - 1j * cu[:, 2::2]).T  # (K, N), A e^{-ig}
    comp_v = (cv[:, 1::2] - 1j * cv[:, 2::2]).T
    mean_u = cu[:, 0] * (0 if args.no_mean else 1)
    mean_v = cv[:, 0] * (0 if args.no_mean else 1)
    Cmat = np.column_stack([mean_u, mean_v, comp_u.T, comp_v.T]).astype(np.complex128)
    tidal_frac = 1 - (A["ru"] ** 2 + A["rv"] ** 2) / np.maximum(A["su"] ** 2 + A["sv"] ** 2, 1e-12)
    print(f"[{region}] {len(clat)} cells fitted ({int(A['gap'].sum())} with gaps); median tidal variance "
          f"explained {np.median(tidal_frac):.3f}; median residual {np.median(np.hypot(A['ru'], A['rv'])):.3f} m/s")

    # ── classes from the density raster ────────────────────────────────────
    xy = LocalXY(np.mean(bbox[1::2]))
    P = xy(clon, clat)
    thr = dp
    rmask = inreg & np.isfinite(dens) & (dens >= thr)
    rlat, rlon = lat[rmask], lon[rmask]
    if rmask.sum() == 0:
        print(f"[{region}] no route cells at density >= {thr:.3g}; skipped")
        return
    rp = xy(rlon, rlat)
    dist_route, _ = cKDTree(rp).query(P)
    m2_major = np.hypot(np.abs(comp_u[chosen.index("M2")]), np.abs(comp_v[chosen.index("M2")]))
    fast = m2_major > FAST_M2
    on_route = dist_route < ROUTE_DIST_M
    cls = np.full(len(P), CLASSES["slow"]["code"])
    cls[fast] = CLASSES["channel"]["code"]
    cls[dist_route > OPEN_DIST_M] = CLASSES["open"]["code"]
    cls[on_route] = CLASSES["route_slow"]["code"]
    cls[on_route & fast] = CLASSES["route"]["code"]
    by_code = {v["code"]: v for v in CLASSES.values()}
    tol = np.array([by_code[c]["tol"] for c in cls]) * args.tol_scale
    # DCSM distances suit a ~200 m grid; on the 800 m grid route maxd 500 m made every route cell a point
    maxd = np.array([by_code[c]["maxd"] for c in cls]) * args.maxd_scale
    sp = thin_points(rp, SEED_STEP_M * 3)  # 1500 m: SEED_STEP_M (500) is below the 800 m grid, which seeded every route cell
    ds, si = cKDTree(P).query(sp)
    seeds = np.unique(si[ds < 150])
    if len(seeds) == 0:  # partial cache (dev): start from the cell closest to a route
        seeds = np.array([int(np.argmin(dist_route))])
    print(f"[{region}] route threshold p{args.route_pct:g} = {thr:.3g}; {int(rmask.sum())} route cells; "
          f"{len(seeds)} seeds in fitted cells")
    pts = place(P, Cmat, tol, maxd, list(seeds), err_fn=mean_aware_err)
    print(f"[{region}] {len(pts)} points placed (tol-scale {args.tol_scale:g})")
    if len(pts) > args.max_points:
        print(f"[{region}] WARNING: {len(pts)} points exceed --max-points {args.max_points}; "
              f"rerun with a larger --tol-scale (e.g. {args.tol_scale * 1.3:.1f})")

    d, k = cKDTree(P[pts]).query(P)
    err = mean_aware_err(Cmat, np.arange(len(P)), pts[k])
    for name, c in CLASSES.items():
        m = cls == c["code"]
        if m.any():
            print(f"    {name:10s}: {m.sum():6d} cells, {np.sum(np.isin(pts, np.where(m)[0])):5d} points, "
                  f"RMS err p50/p95 {np.median(err[m]):.3f}/{np.percentile(err[m], 95):.3f} m/s, "
                  f"dist p95 {np.percentile(d[m], 95):.0f} m")

    feats = []
    for i in pts:
        hc = {}
        for kk, c in enumerate(chosen):
            au, av = comp_u[kk, i], comp_v[kk, i]
            if max(abs(au), abs(av)) < 0.002 and c not in ("M2", "S2"):
                continue  # M2/S2 always kept: a station without constituents is skipped by the plugin
            hc[c] = {"u_amplitude": round(float(abs(au)), 4),
                     "u_phase_g": round(float(np.degrees(-np.angle(au)) % 360), 2),
                     "v_amplitude": round(float(abs(av)), 4),
                     "v_phase_g": round(float(np.degrees(-np.angle(av)) % 360), 2)}
        feats.append({
            "type": "Feature",
            "id": f"NK_{clat[i]:.4f}_{clon[i]:.4f}".replace(".", "p"),
            "geometry": {"type": "Point", "coordinates": [round(float(clon[i]), 5), round(float(clat[i]), 5)]},
            "properties": {
                "station_name": f"{clat[i]:.4f},{clon[i]:.4f}",
                "prediction_method": "harmonic_constituents_currents",
                "data_unit_speed": "meters_per_second",
                "mean_offset": {"u_residual": round(float(mean_u[i]), 4), "v_residual": round(float(mean_v[i]), 4)},
                "harmonic_constituents": hc,
            },
        })
    args.out.mkdir(parents=True, exist_ok=True)
    info = dict(title=cfg["title"], bbox=bbox, secs=secs, days=days, chosen=chosen, feats=feats)
    np.savez_compressed(args.out / f"norkyst_{region}_diag.npz", lat=clat, lon=clon, cls=cls, err=err, pts=pts,
                        peak=A["peak"], z=A["h"], dist_route=dist_route, tidal_frac=tidal_frac)
    if not args.merge:
        write_utcef(cfg["file"], info, args, region)
    return info


def write_utcef(fname, info, args, label, areas=None):
    title, bbox, secs, days, chosen, feats = (info[k] for k in ("title", "bbox", "secs", "days", "chosen", "feats"))
    title = title + (" (TEST)" if TEST_RELEASE else "")
    t0 = dt.datetime.fromtimestamp(secs[0], dt.timezone.utc)
    t1 = dt.datetime.fromtimestamp(secs[-1], dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "metadata": {
            "schema_version": "1.0.0",
            "dataset_version": now.strftime("%Y.%m.%d"),
            "title": title,
            "description": (
                ("TEST RELEASE: experimental, validate before relying on it. " if TEST_RELEASE else "") +
                "Tidal stream harmonics derived by harmonic analysis of MET Norway's NorKyst v3 800 m ocean model "
                f"(surface current, hourly, thredds.met.no fou-hi/norkystv3_800m_m00_be) over {t0:%Y-%m-%d} – "
                f"{t1:%Y-%m-%d} ({days:.0f} days)"
                + (f", for four areas: {areas}" if areas else "") +
                f". Fitted: {', '.join(chosen)}. Point positions follow "
                "leisure-vessel traffic density (EMODnet Human Activities, used only to choose where points "
                "go) and are denser where the stream changes quickly. Straits narrower than the model's 800 m "
                "grid (for example Saltstraumen and Rystraumen) are land in the model and carry no current. "
                "Gives the tide plus the yearly mean surface flow only: along much of the coast, and above all in "
                "the Skagerrak and Oslofjord, the surface current is driven mostly by wind and the Norwegian "
                "Coastal Current, so the actual current can be much stronger; use a current forecast there. "
                "Surface current of a numerical model: not for official navigation, not an official product of "
                "MET Norway or the Norwegian Hydrographic Service and not endorsed by them; no liability is "
                "accepted for errors or for incidents resulting from its use."),
            "last_updated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "region": {"name": title, "bbox": bbox},
            "copyright": ("Tidal current harmonic constituents derived from NorKyst v3 model output, Copyright "
                          "Norwegian Meteorological Institute (MET Norway), CC BY 4.0. Point positions chosen "
                          "with EMODnet Human Activities vessel density (originator Cogea Srl, CC BY 4.0). "
                          "Harmonic analysis, point placement and packaging: signalk-router-data."),
            "license": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "citation_required": ("Contains model data from the Norwegian Meteorological Institute (MET Norway), "
                                  "CC BY 4.0 (thredds.met.no fou-hi/norkystv3_800m_m00_be); vessel density used to "
                                  "choose point positions: EMODnet Human Activities, originator Cogea Srl, CC BY 4.0 "
                                  "(https://emodnet.ec.europa.eu/); processed by signalk-router-data"),
            "data_sources": [
                {"name": "Norwegian Meteorological Institute (MET Norway)", "url": "https://thredds.met.no/",
                 "role": "Model data (NorKyst v3, norkystv3_800m_m00_be)", "license": "CC-BY-4.0",
                 "license_url": "https://creativecommons.org/licenses/by/4.0/"},
                {"name": "EMODnet Human Activities, vessel density (Cogea Srl)",
                 "url": "https://emodnet.ec.europa.eu/", "role": "Point placement only",
                 "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                 "notes": "Data downloaded from the EMODnet Portal, originator Cogea Srl"},
            ],
            "catalog": {**CATALOG, "tags": CATALOG["tags"] + (["test"] if TEST_RELEASE else [])},
        },
        "dataset": {"type": "FeatureCollection", "features": feats},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{fname}.utcef"
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{fname}.json", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    print(f"[{label}] wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {len(feats)} stations)")


MERGED_TITLE = "Norwegian Coast — High-Density Tidal Streams (NorKyst v3)"
MERGED_AREAS = ("Skagerrak, Oslofjord & Sørlandet; the west coast from Stavanger via Bergen and Ålesund to "
                "Trondheim; Helgeland, Bodø & Lofoten; Vesterålen, Tromsø & Finnmark")


def merge(infos, args):
    """One UTCEF from all regions. Regions overlap, so the same cell can be a point in two regions
    (same id) and nearby cells can give points closer than MIN_SPACING_M; the point from the region whose
    bbox centre is nearer is kept."""
    names = list(infos)
    cent = {r: (np.mean(infos[r]["bbox"][0::2]), np.mean(infos[r]["bbox"][1::2])) for r in names}
    items = [(r, f) for r in names for f in infos[r]["feats"]]
    lon = np.array([f["geometry"]["coordinates"][0] for _, f in items])
    lat = np.array([f["geometry"]["coordinates"][1] for _, f in items])
    reg = np.array([names.index(r) for r, _ in items])
    ids = [f["id"] for _, f in items]
    dcent = np.array([np.hypot((lon[i] - cent[items[i][0]][0]) * np.cos(np.radians(lat[i])), lat[i] - cent[items[i][0]][1])
                      for i in range(len(items))])
    la, lo = np.radians(lat), np.radians(lon)
    xyz = 6371000.0 * np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])
    drop = np.zeros(len(items), bool)
    n_id = n_sp = 0
    first = {}
    for i, k in enumerate(ids):  # same grid cell
        j = first.setdefault(k, i)
        if j != i:
            loser = i if dcent[i] >= dcent[j] else j
            if not drop[loser]:
                drop[loser] = True
                n_id += 1
            first[k] = j if loser == i else i
    pairs = sorted(cKDTree(xyz).query_pairs(MIN_SPACING_M, output_type="ndarray").tolist(),
                   key=lambda p: -min(dcent[p[0]], dcent[p[1]]))
    for i, j in pairs:
        if reg[i] == reg[j] or drop[i] or drop[j]:
            continue
        drop[i if dcent[i] >= dcent[j] else j] = True
        n_sp += 1
    feats = [f for (_, f), d in zip(items, drop) if not d]
    print(f"[merge] {len(items)} points in {len(names)} regions; removed {n_id} same-cell duplicates and "
          f"{n_sp} within {MIN_SPACING_M} m of another region's point; {len(feats)} remain")
    bb = [min(infos[r]["bbox"][0] for r in names), min(infos[r]["bbox"][1] for r in names),
          max(infos[r]["bbox"][2] for r in names), max(infos[r]["bbox"][3] for r in names)]
    first_info = infos[names[0]]
    info = dict(first_info, title=MERGED_TITLE, bbox=bb, feats=feats,
                secs=np.array([min(infos[r]["secs"][0] for r in names), max(infos[r]["secs"][-1] for r in names)]),
                days=max(infos[r]["days"] for r in names))
    write_utcef(args.merge, info, args, "merge", areas=MERGED_AREAS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    root = Path("/home/node/signalkdev/norkyst-collector")
    ap.add_argument("--data", type=Path, default=root / "data")
    ap.add_argument("--density", type=Path, default=root / "density.npz")
    ap.add_argument("--region", choices=[*REGIONS, "all"], default="all")
    ap.add_argument("--out", type=Path, default=Path("dist"))
    ap.add_argument("--tol-scale", type=float, default=1.0, help="multiply all error tolerances")
    ap.add_argument("--maxd-scale", type=float, default=1.0,
                    help="multiply the DCSM class max distances (500 m route .. 4 km open) for the 800 m grid")
    ap.add_argument("--route-pct", type=float, default=90,
                    help="route cells = density >= this percentile of the region (50/80/90/95 use density.npz)")
    ap.add_argument("--max-points", type=int, default=17000, help="warn above this many points per region")
    ap.add_argument("--no-mean", action="store_true", help="zero mean_offset (validation experiment)")
    ap.add_argument("--drop-long-period", action="store_true",
                    help="experiment: do not fit SA/SSA/MSM/MM/MSF/MF (they may absorb non-tidal flow)")
    ap.add_argument("--merge", metavar="NAME",
                    help="build all regions and write ONE UTCEF NAME.utcef (overlap duplicates removed)")
    ap.add_argument("--tiles", nargs="*", help="dev: only these tile directories (e.g. tile_0576_1344)")
    ap.add_argument("--months", nargs="*", help="dev: only these months (YYYY-MM)")
    args = ap.parse_args()

    lat, lon, wet = load_grid(args.data)
    with netCDF4.Dataset(args.data / "grid.nc") as g:
        h = g["h"][:].filled(np.nan)
    dz = np.load(args.density)
    dens = dz["density"]
    tiles = find_tiles(args.data, set(args.tiles or []), set(args.months or []))
    print(f"{len(tiles)} tiles with complete files in {args.data / 'fit'}")
    infos = {}
    if args.merge and args.region != "all":
        sys.exit("--merge needs --region all")
    for r in (REGIONS if args.region == "all" else [args.region]):
        key = f"p{args.route_pct:g}"
        if key in dz.files:
            dp = float(dz[f"{key}_{r}"])
        else:
            b = REGIONS[r]["bbox"]
            box = (lat >= b[1]) & (lat <= b[3]) & (lon >= b[0]) & (lon <= b[2])
            v = dens[box]
            dp = float(np.percentile(v[np.isfinite(v) & (v > 0)], args.route_pct))
        ctx = dict(lat=lat, lon=lon, wet=wet, h=h, dens=dens, dp=dp, tiles=tiles)
        res = build(r, REGIONS[r], ctx, args)
        if res:
            infos[r] = res
    if args.merge:
        merge(infos, args)


if __name__ == "__main__":
    main()
