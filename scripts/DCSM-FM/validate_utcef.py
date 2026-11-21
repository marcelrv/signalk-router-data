#!/usr/bin/env python3
"""Check a DCSM-derived UTCEF against the model record it came from, using the
signalk-tidal-currents plugin's own prediction code (plugin_predict.mjs), and
plot a few stations. Reports per station the RMS vector error and the peak
speed of model vs prediction. Model includes weather; prediction is tide only,
so the residual is surge/wind plus fit error."""
import argparse
import json
import random
import subprocess
import tempfile
import zipfile
from pathlib import Path

import netCDF4
import numpy as np

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("utcef", type=Path)
    ap.add_argument("--data", type=Path, default=Path("/home/node/signalkdev/matroos-collector/data"))
    ap.add_argument("--plugin-dist", type=Path, default=Path("/home/node/signalkdev/signalk-tidal-currents/dist"))
    ap.add_argument("-n", type=int, default=200)
    ap.add_argument("--ids", nargs="*", default=[])
    ap.add_argument("--plot", type=Path)
    args = ap.parse_args()

    with zipfile.ZipFile(args.utcef) as z:
        feats = json.loads(z.read(z.namelist()[0]))["dataset"]["features"]
    random.seed(1)
    pick = [f for f in feats if f["id"] in args.ids] + random.sample(feats, min(args.n, len(feats)))
    pick = list({f["id"]: f for f in pick}.values())  # dedupe; dict keeps first-seen order (explicit ids first)
    with netCDF4.Dataset(args.data / "grid.nc") as g:
        lat = g["lat"][:].filled(np.nan)
        lon = g["lon"][:].filled(np.nan)
    files = sorted(args.data.glob("*/dcsm100m_nl_*.nc"))
    k = np.cos(np.radians(52.5))
    cells = []
    for f in pick:
        lo, la = f["geometry"]["coordinates"]
        d = (lon - lo) ** 2 * k * k + (lat - la) ** 2
        cells.append(np.unravel_index(np.nanargmin(d), d.shape))
    rr = np.array([c[0] for c in cells])
    cc = np.array([c[1] for c in cells])
    mins, U, V = [], [], []
    for fn in files:
        with netCDF4.Dataset(fn) as d:
            mins.append(d["time"][:])
            # read the bounding block once, then pick cells
            r0, r1, c0, c1 = rr.min(), rr.max() + 1, cc.min(), cc.max() + 1
            U.append(d["velu"][:, r0:r1, c0:c1].filled(0)[:, rr - r0, cc - c0])
            V.append(d["velv"][:, r0:r1, c0:c1].filled(0)[:, rr - r0, cc - c0])
    mins = np.concatenate(mins)
    U = np.concatenate(U)
    V = np.concatenate(V)
    times_ms = (mins.astype(np.int64) * 60000).tolist()
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / args.utcef.name).write_bytes(args.utcef.read_bytes())
        res = subprocess.run(["node", str(HERE / "plugin_predict.mjs"), str(args.plugin_dist), td],
                             input=json.dumps({"ids": [f["id"] for f in pick], "times_ms": times_ms}),
                             capture_output=True, text=True, check=True)
    pred = json.loads(res.stdout)
    missing = [f["id"] for f in pick if f["id"] not in pred]
    if missing:
        print(f"WARNING: plugin did not load {len(missing)} of the sampled stations, e.g. {missing[:3]}")
    keep = [j for j, f in enumerate(pick) if f["id"] in pred]
    pick = [pick[j] for j in keep]
    U, V = U[:, keep], V[:, keep]
    rms, pk_m, pk_p, names = [], [], [], []
    for j, f in enumerate(pick):
        p = np.array(pred[f["id"]])
        e = np.sqrt(np.mean((p[:, 0] - U[:, j]) ** 2 + (p[:, 1] - V[:, j]) ** 2))
        rms.append(e)
        pk_m.append(np.max(np.hypot(U[:, j], V[:, j])))
        pk_p.append(np.max(np.hypot(p[:, 0], p[:, 1])))
        names.append(f["properties"]["station_name"])
    rms, pk_m, pk_p = map(np.array, (rms, pk_m, pk_p))
    print(f"{len(pick)} stations, {len(mins)} hours (plugin astro.ts prediction vs raw model incl. weather)")
    print(f"RMS vector error  p50 {np.median(rms):.3f}  p90 {np.percentile(rms, 90):.3f}  max {rms.max():.3f} m/s")
    print(f"peak speed ratio pred/model p50 {np.median(pk_p / np.maximum(pk_m, 1e-3)):.3f}")
    worst = np.argsort(-rms)[:5]
    for j in worst:
        print(f"  worst: {names[j]:45s} rms {rms[j]:.3f}  peak model {pk_m[j] * 1.9438:.2f} kn pred {pk_p[j] * 1.9438:.2f} kn")
    if args.plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        sel = [pick.index(f) for f in pick if f["id"] in args.ids][:4] or list(np.argsort(-pk_m)[:4])
        fig, axs = plt.subplots(len(sel), 1, figsize=(14, 2.6 * len(sel)), dpi=100, sharex=True)
        t = (mins - mins[0]) / 1440.0
        for ax, j in zip(np.atleast_1d(axs), sel):
            p = np.array(pred[pick[j]["id"]])
            ax.plot(t, np.hypot(U[:, j], V[:, j]) * 1.9438, "k-", lw=1, label="model (tide+weather)")
            ax.plot(t, np.hypot(p[:, 0], p[:, 1]) * 1.9438, "r-", lw=1, label="UTCEF via plugin")
            ax.set_ylabel("kn")
            ax.set_title(names[j], fontsize=9)
        np.atleast_1d(axs)[0].legend(loc="upper right")
        np.atleast_1d(axs)[-1].set_xlabel("days since record start")
        fig.tight_layout()
        fig.savefig(args.plot)
        print("plot:", args.plot)


if __name__ == "__main__":
    main()
