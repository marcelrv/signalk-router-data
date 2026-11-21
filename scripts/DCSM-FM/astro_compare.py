#!/usr/bin/env python3
"""Compare DCSM UTCEF files with the tide-only DCSM-FM run (Matroos maps1d
source dcsm_fm100m_astro: same model, no weather forcing) at its named
stations, through the plugin's own prediction code. Weather-free, so this
isolates the harmonic fit and point placement from surge and wind.

Usage: astro_compare.py <utcef> [<utcef> ...] [--astro DIR]"""
import argparse
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

import netCDF4
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
KT = 1.943844


def load_astro(d: Path):
    files = sorted(d.glob("*/dcsm100m_astro1d_*.nc"))
    t, sp, di = [], [], []
    for f in files:
        with netCDF4.Dataset(f) as nc:
            if not t:
                lat = nc["lat"][:].astype(float)
                lon = nc["lon"][:].astype(float)
                names = ["".join(c.decode() for c in r if c).strip() for r in nc["nodenames"][:].filled(b"")]
            t.append(nc["time"][:].astype(np.int64))
            sp.append(nc["water_speed"][:].astype(float).filled(np.nan))
            di.append(nc["water_direction"][:].astype(float).filled(np.nan))
    t = np.concatenate(t)
    sp = np.concatenate(sp)
    di = np.concatenate(di)
    _, keep = np.unique(t, return_index=True)  # overlapping 6-h files
    return lat, lon, names, t[keep], sp[keep], di[keep]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("utcef", nargs="+", type=Path)
    ap.add_argument("--astro", type=Path, default=Path("/home/node/signalkdev/matroos-collector/data/astro1d"))
    ap.add_argument("--plugin-dist", type=Path, default=Path("/home/node/signalkdev/signalk-tidal-currents/dist"))
    ap.add_argument("--min-peak", type=float, default=0.5, help="m/s; skip weak-flow stations")
    ap.add_argument("--max-km", type=float, default=0.5, help="skip stations farther than this from a UTCEF point")
    args = ap.parse_args()

    lat, lon, names, t, sp, di = load_astro(args.astro)
    hourly = (t % 60 == 0)
    t, sp, di = t[hourly], sp[hourly], di[hourly]
    # direction convention: test "towards" and "from"; report which fits
    print(f"tide-only record: {len(t)} hourly steps, {(t[-1] - t[0]) / 1440:.1f} days, {len(names)} stations")
    k = np.cos(np.radians(52.5))
    for path in args.utcef:
        with zipfile.ZipFile(path) as z:
            feats = json.loads(z.read(z.namelist()[0]))["dataset"]["features"]
        xy = np.array([f["geometry"]["coordinates"] for f in feats])
        tree = cKDTree(np.c_[xy[:, 0] * k, xy[:, 1]])
        d, si = tree.query(np.c_[lon * k, lat])
        dkm = d * 111.2
        use = np.where((dkm <= args.max_km) & (np.nanmax(sp, 0) >= args.min_peak) & np.isfinite(sp).all(0))[0]
        ids = [feats[si[j]]["id"] for j in use]
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / path.name).write_bytes(path.read_bytes())
            res = subprocess.run(["node", str(HERE / "plugin_predict.mjs"), str(args.plugin_dist), td],
                                 input=json.dumps({"ids": ids, "times_ms": (t * 60000).tolist()}),
                                 capture_output=True, text=True, check=True)
        pred = json.loads(res.stdout)
        best = None
        for conv, sign in (("towards", 1.0), ("from", -1.0)):
            ua = sign * sp * np.sin(np.radians(di))
            va = sign * sp * np.cos(np.radians(di))
            rms = []
            for j, sid in zip(use, ids):
                if sid not in pred:
                    continue
                p = np.array(pred[sid])
                rms.append(np.sqrt(np.mean((p[:, 0] - ua[:, j]) ** 2 + (p[:, 1] - va[:, j]) ** 2)))
            if best is None or np.median(rms) < best[1]:
                best = (conv, np.median(rms), ua, va)
        conv, _, ua, va = best
        rows = []
        for j, sid in zip(use, ids):
            if sid not in pred:
                continue
            p = np.array(pred[sid])
            ps, as_ = np.hypot(p[:, 0], p[:, 1]), sp[:, j]
            rms = np.sqrt(np.mean((p[:, 0] - ua[:, j]) ** 2 + (p[:, 1] - va[:, j]) ** 2))
            ok = (ps > 0.25) & (as_ > 0.25)
            dd = np.degrees(np.angle((p[ok, 0] + 1j * p[ok, 1]) / (ua[ok, j] + 1j * va[ok, j]))) if ok.any() else np.array([np.nan])
            daily = [ps[i:i + 24].mean() / max(as_[i:i + 24].mean(), 1e-6) for i in range(0, len(t) - 23, 24)]
            rows.append((names[j], dkm[j], rms, ps.max() / as_.max(), np.median(np.abs(dd)), daily))
        R = np.array([r[2] for r in rows])
        D = np.array([r[5] for r in rows])
        dmed = np.median(D, axis=0)
        print(f"\n== {path.name}: {len(rows)} stations (peak >= {args.min_peak} m/s, point within {args.max_km} km); "
              f"direction convention '{conv}'")
        print(f"   RMS vector error p50 {np.median(R):.3f} p90 {np.percentile(R, 90):.3f} m/s "
              f"({np.median(R) * KT:.2f} kn); peak ratio p50 {np.median([r[3] for r in rows]):.2f}; "
              f"|dir diff| p50 {np.nanmedian([r[4] for r in rows]):.0f} deg")
        print(f"   daily mean-speed ratio UTCEF/tide-only: min {dmed.min():.2f} max {dmed.max():.2f}  "
              f"[{' '.join(f'{x:.2f}' for x in dmed)}]")
        for r in sorted(rows, key=lambda r: -r[2])[:4]:
            print(f"   worst: {r[0]:40s} {r[1]:.2f} km  rms {r[2]:.3f}  peak ratio {r[3]:.2f}")


if __name__ == "__main__":
    main()
