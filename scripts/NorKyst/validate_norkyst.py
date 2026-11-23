#!/usr/bin/env python3
"""Check a NorKyst-derived UTCEF against the raw NorKyst tile cache (fetch_record.py),
using the signalk-tidal-currents plugin's own prediction code (the shared
scripts/DCSM-FM/plugin_predict.mjs, used by path).

For sampled stations (plus --ids and named spot checks) the model series at the
station's grid cell is compared with the plugin prediction over --window:
  fit         the cached 366-day fit record (in-sample)
  validation  the held-out <cache>/validation folder (falls back to fit, with a
              warning, while that folder does not exist yet)
Per station: RMS vector error, tidal variance explained (1 - SSE / model variance),
peak-speed ratio (pred/model max), set error at peak flow (median |direction
difference| over the model's top-1 % speeds), and the range (p10..p90 over days)
of the daily max-speed ratio pred/model (spring-neap check), speed p95/p99 ratios and the
mean-daily-max ratio on spring days (top quarter of predicted daily maxima), which are less
sensitive to weather extremes than the absolute peak. Spots outside the file's bbox are skipped. The model contains
weather, the prediction is tide (+mean_offset) only.

--no-mean zeroes mean_offset in the UTCEF copy fed to the plugin.
"""
import argparse
import hashlib
import json
import random
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import netCDF4
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from norkyst_to_utcef import TILE, read_tile, read_times  # noqa: E402

PLUGIN_PREDICT = HERE.parent / "DCSM-FM" / "plugin_predict.mjs"
SPOTS = {"Moskstraumen": (67.80, 12.85), "Drobak narrows": (59.66, 10.62),
         "Tromso sound": (69.65, 18.95), "Raftsundet": (68.45, 14.55)}
KN = 1.9438


def xy(lon, lat):
    return np.column_stack([np.asarray(lon) * np.cos(np.radians(65)) * 111195.0, np.asarray(lat) * 111195.0])


def tile_files(root, window):
    out = {}
    for d in sorted((root / window).glob("tile_*")):
        fs = [(f.stem, f) for f in sorted(d.glob("????-??.nc"))]
        if fs:
            out[tuple(int(s) for s in d.name.split("_")[1:3])] = fs
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("utcef", type=Path)
    ap.add_argument("--data", type=Path, default=Path("/home/node/signalkdev/norkyst-collector/data"))
    ap.add_argument("--plugin-dist", type=Path, default=Path("/home/node/signalkdev/signalk-tidal-currents/dist"))
    ap.add_argument("--window", choices=["fit", "validation"], default="validation")
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--no-mean", action="store_true", help="zero mean_offset in the prediction")
    ap.add_argument("--no-spots", action="store_true")
    ap.add_argument("--plot", type=Path)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    window = args.window
    if window == "validation" and not (args.data / "validation").is_dir():
        print("WARNING: no validation folder yet; using the fit window (in-sample)")
        window = "fit"
    tiles = tile_files(args.data, window)
    if not tiles:
        sys.exit(f"no complete tiles in {args.data / window}")

    with zipfile.ZipFile(args.utcef) as z:
        payload = json.loads(z.read(z.namelist()[0]))
    feats = payload["dataset"]["features"]
    with netCDF4.Dataset(args.data / "grid.nc") as g:
        lat = g["lat"][:].filled(np.nan)
        lon = g["lon"][:].filled(np.nan)
        wet = g["wet"][:].astype(bool)
    rr, cc = np.where(wet & np.isfinite(lat))
    ctree = cKDTree(xy(lon[rr, cc], lat[rr, cc]))
    flon = np.array([f["geometry"]["coordinates"][0] for f in feats])
    flat = np.array([f["geometry"]["coordinates"][1] for f in feats])
    fxy = xy(flon, flat)
    dcell, ci = ctree.query(fxy)
    cell = {}  # station index -> (row, col)
    for j in range(len(feats)):
        r, c = int(rr[ci[j]]), int(cc[ci[j]])
        if dcell[j] < 600 and (r // TILE * TILE, c // TILE * TILE) in tiles:
            cell[j] = (r, c)
    print(f"{len(cell)} of {len(feats)} stations lie in tiles cached for window '{window}'")

    labels = {}
    pick = []
    for j, f in enumerate(feats):
        if f["id"] in args.ids and j in cell:
            pick.append(j)
            labels[j] = "id"
    if not args.no_spots:
        ftree = cKDTree(fxy)
        bb = payload["metadata"]["region"]["bbox"]
        for name, (la, lo) in SPOTS.items():
            if not (bb[0] <= lo <= bb[2] and bb[1] <= la <= bb[3]):
                continue  # spot belongs to another region file
            d, j = ftree.query(xy([lo], [la])[0])
            if d > 3000:
                print(f"spot {name}: no station within 3 km (nearest {d / 1000:.1f} km); skipped")
            elif int(j) not in cell:
                print(f"spot {name}: station {feats[int(j)]['id']} ({d:.0f} m) not in cached tiles; skipped")
            else:
                pick.append(int(j))
                labels[int(j)] = name
    random.seed(args.seed)
    pool = [j for j in cell if j not in set(pick)]
    sample = random.sample(pool, min(args.n, len(pool)))
    for j in sample:
        labels.setdefault(j, "")
    pick = list(dict.fromkeys(pick + sample))
    if not pick:
        sys.exit("nothing to validate")

    # held-out samples must precede the fit window (an older cache overlapped it by a day)
    t_cut = np.inf
    if window == "validation":
        fit_tiles = tile_files(args.data, "fit")
        if fit_tiles:
            t_cut = min(float(read_times(v[:1])[0]) for v in fit_tiles.values())

    # model series, tile by tile (one read per tile)
    series = {}  # j -> (times_s, U, V)
    by_tile = {}
    for j in pick:
        r, c = cell[j]
        by_tile.setdefault((r // TILE * TILE, c // TILE * TILE), []).append(j)
    for (y0, x0), js in by_tile.items():
        sel = np.zeros((min(TILE, wet.shape[0] - y0), min(TILE, wet.shape[1] - x0)), bool)
        for j in js:
            sel[cell[j][0] - y0, cell[j][1] - x0] = True
        files = tiles[(y0, x0)]
        U, V = read_tile(files, sel)
        t = read_times(files)
        keep = t < t_cut
        if not keep.all():
            U, V, t = U[keep], V[keep], t[keep]
        order = {rc: k for k, rc in enumerate(zip(*np.where(sel)))}  # raster order = read order
        for j in js:
            k = order[(cell[j][0] - y0, cell[j][1] - x0)]
            series[j] = (t, U[:, k].astype(np.float64), V[:, k].astype(np.float64))

    # plugin prediction, one node call per distinct time axis
    groups = {}
    for j in pick:
        t = series[j][0]
        groups.setdefault(hashlib.sha1(np.ascontiguousarray(t, dtype='f8').tobytes()).hexdigest(), []).append(j)
    pred = {}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / args.utcef.name
        if args.no_mean:  # rewrite the zip with mean_offset zeroed
            for f in feats:
                f["properties"]["mean_offset"] = {"u_residual": 0.0, "v_residual": 0.0}
            with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(args.utcef.stem + ".json", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        else:
            p.write_bytes(args.utcef.read_bytes())
        for js in groups.values():
            times_ms = (series[js[0]][0] * 1000).astype(np.int64).tolist()
            res = subprocess.run(["node", str(PLUGIN_PREDICT), str(args.plugin_dist), td],
                                 input=json.dumps({"ids": [feats[j]["id"] for j in js], "times_ms": times_ms}),
                                 capture_output=True, text=True, check=True)
            out = json.loads(res.stdout)
            for j in js:
                if feats[j]["id"] in out:
                    pred[j] = np.array(out[feats[j]["id"]])
    missing = [j for j in pick if j not in pred]
    if missing:
        print(f"WARNING: plugin did not load {len(missing)} stations, e.g. {feats[missing[0]]['id']}")
    pick = [j for j in pick if j in pred]

    rows = {}
    for j in pick:
        t, U, V = series[j]
        p = pred[j]
        ok = np.isfinite(U) & np.isfinite(V)
        if ok.sum() < 48:
            continue
        t, U, V, pu, pv = t[ok], U[ok], V[ok], p[ok, 0], p[ok, 1]
        sse = np.sum((pu - U) ** 2 + (pv - V) ** 2)
        var = np.sum((U - U.mean()) ** 2 + (V - V.mean()) ** 2)
        sm, sp = np.hypot(U, V), np.hypot(pu, pv)
        top = sm >= np.percentile(sm, 99)
        dang = np.degrees(np.angle(np.exp(1j * (np.arctan2(U, V) - np.arctan2(pu, pv)))))  # compass set diff
        day = (t // 86400).astype(int)
        ud = np.unique(day)
        okd = [d for d in ud if (day == d).sum() >= 20]
        dmax_m = np.array([sm[day == d].max() for d in okd])
        dmax_p = np.array([sp[day == d].max() for d in okd])
        dr = dmax_p / np.maximum(dmax_m, 1e-3)
        spring = dmax_p >= np.percentile(dmax_p, 75)  # spring days = top quarter of predicted daily maxima
        rows[j] = dict(rms=np.sqrt(sse / len(t)), ve=1 - sse / max(var, 1e-12), pr=sp.max() / max(sm.max(), 1e-3),
                       set=np.median(np.abs(dang[top])), d10=np.percentile(dr, 10), d90=np.percentile(dr, 90),
                       pk=sm.max(), pp=sp.max(), n=len(t),
                       p95=np.percentile(sp, 95) / max(np.percentile(sm, 95), 1e-3),
                       p99=np.percentile(sp, 99) / max(np.percentile(sm, 99), 1e-3),
                       spr=dmax_p[spring].mean() / max(dmax_m[spring].mean(), 1e-3))
    n0 = next(iter(rows.values()))["n"] if rows else 0
    print(f"\n{len(rows)} stations, {n0} hours, window {window}, mean_offset {'ZEROED' if args.no_mean else 'kept'} "
          f"(plugin prediction vs raw model incl. weather)")
    hdr = f"{'station':28s} {'label':14s} {'rms':>6s} {'VE':>6s} {'pk ratio':>8s} {'set':>5s} {'p95r':>5s} {'p99r':>5s} {'spr':>5s} {'daily ratio p10..p90':>21s} {'model/pred pk kn':>17s}"
    print(hdr)

    def line(j):
        r = rows[j]
        return (f"{feats[j]['properties']['station_name']:28s} {labels.get(j, ''):14s} {r['rms']:6.3f} {r['ve']:6.3f} "
                f"{r['pr']:8.2f} {r['set']:5.1f} {r['p95']:5.2f} {r['p99']:5.2f} {r['spr']:5.2f} {r['d10']:9.2f}..{r['d90']:<9.2f} {r['pk'] * KN:7.2f}/{r['pp'] * KN:<7.2f}")

    shown = [j for j in pick if j in rows and labels.get(j)]
    for j in shown:
        print(line(j))
    agg = [j for j in rows]
    for key, name, unit in (("rms", "RMS vector error", "m/s"), ("ve", "tidal variance explained", ""),
                            ("pr", "peak-speed ratio", ""), ("p95", "speed p95 ratio", ""),
                            ("p99", "speed p99 ratio", ""), ("spr", "spring daily-max ratio", ""), ("set", "set error at peak", "deg"),
                            ("d10", "daily ratio p10", ""), ("d90", "daily ratio p90", "")):
        a = np.array([rows[j][key] for j in agg])
        print(f"aggregate {name:26s} p50 {np.median(a):7.3f}  p95 {np.percentile(a, 95):7.3f}  "
              f"(p5 {np.percentile(a, 5):.3f}) {unit}")
    print("worst by RMS:")
    for j in sorted(agg, key=lambda j: -rows[j]["rms"])[:5]:
        print("  " + line(j))

    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        sel = [j for j in pick if j in rows and labels.get(j)][:4]
        sel += [j for j in sorted(agg, key=lambda j: -rows[j]["pk"]) if j not in sel][:max(0, 4 - len(sel))]
        fig, axs = plt.subplots(len(sel), 1, figsize=(14, 2.6 * len(sel)), dpi=100, sharex=True)
        for ax, j in zip(np.atleast_1d(axs), sel):
            t, U, V = series[j]
            m = (t - t[0]) / 86400 < 14
            ax.plot((t[m] - t[0]) / 86400, np.hypot(U, V)[m] * KN, "k-", lw=1, label="model (tide+weather)")
            ax.plot((t[m] - t[0]) / 86400, np.hypot(pred[j][:, 0], pred[j][:, 1])[m] * KN, "r-", lw=1,
                    label="UTCEF via plugin")
            ax.set_ylabel("kn")
            ax.set_title(f"{feats[j]['properties']['station_name']} {labels.get(j, '')}", fontsize=9)
        np.atleast_1d(axs)[0].legend(loc="upper right")
        np.atleast_1d(axs)[-1].set_xlabel("days since window start")
        fig.tight_layout()
        fig.savefig(args.plot)
        print("plot:", args.plot)


if __name__ == "__main__":
    main()
