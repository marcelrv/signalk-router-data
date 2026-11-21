#!/usr/bin/env python3
"""Compare the DCSM-FM harmonic fit with tidal-stream atlas arrows (e.g. HP33)
read off by hand into a private CSV — the atlas itself is copyrighted and is
never copied into the data; this only checks our numbers against it.

CSV columns: ref_port (Vlissingen|Delfzijl|...), hour_rel_hw, lat, lon,
bearing, neap_kn, spring_kn, label.

For every arrow: fit the model cell at the arrow (and the reference port's
water level), predict a year of hourly values, take the current at HW+hour at
the reference port for tides whose range is near the mean spring range
2(M2+S2) and the mean neap range 2(M2-S2), and compare speed and set."""
import argparse
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dcsm_to_utcef as D  # noqa: E402

PORTS = {"Vlissingen": (51.4423, 3.5960), "Delfzijl": (53.3290, 6.9330),
         "Den Helder": (52.9640, 4.7450), "Harlingen": (53.1760, 5.4090)}


def nearest_cells(rec, pts, require_wet=None):
    k = np.cos(np.radians(52.5))
    out = []
    for la, lo in pts:
        d = (rec.lon - lo) ** 2 * k * k + (rec.lat - la) ** 2
        if require_wet is not None:
            d = np.where(require_wet, d, np.inf)
        out.append(np.unravel_index(np.nanargmin(d), d.shape))
    return out


def read_cells(rec, var, cells):
    rr = np.array([c[0] for c in cells])
    cc = np.array([c[1] for c in cells])
    r0, r1, c0, c1 = rr.min(), rr.max() + 1, cc.min(), cc.max() + 1
    blk = rec.read(var, slice(r0, r1), slice(c0, c1))
    return blk[:, rr - r0, cc - c0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--data", type=Path, default=Path("/home/node/signalkdev/matroos-collector/data"))
    ap.add_argument("--fes", type=Path, default=Path(__file__).resolve().parents[2] / "regions/europe/netherlands.utcef")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--out", type=Path, help="write per-arrow results CSV here")
    ap.add_argument("--scan-hours", action="store_true",
                    help="also report the fit for every offset -6..+6 h per reference port "
                         "(to check which hour an atlas page shows)")
    args = ap.parse_args()

    arrows = list(csv.DictReader(open(args.csv)))
    rec = D.Record(args.data)
    chosen, inferred = D.choose_constituents(rec.days)
    ratios = D.fes_inference_ratios(args.fes, [3.0, 51.2, 7.35, 53.7], inferred) if inferred else {}
    inferred = {m: r for m, r in inferred.items() if m in ratios}
    print(f"record {rec.days:.1f} days; fitted {chosen}; inferred {inferred}")
    X = D.design_matrix(rec.minutes, chosen, inferred, ratios, lat=52.5)
    t_year = np.arange(np.datetime64(f"{args.year}-01-01"), np.datetime64(f"{args.year + 1}-01-01"),
                       np.timedelta64(10, "m"))
    minutes_year = (t_year - np.datetime64("1970-01-01")).astype("timedelta64[m]").astype(np.int64)
    Xy = D.design_matrix(minutes_year, chosen, inferred, ratios, lat=52.5)

    # reference ports: water level fit -> HW times and ranges
    ports = sorted({a["ref_port"] for a in arrows})
    with D.netCDF4.Dataset(rec.files[0]) as d0:
        wet_sep = ~np.ma.getmaskarray(d0["sep"][:]).any(axis=0)
        wet_u = ~np.ma.getmaskarray(d0["velu"][0])
    pc = nearest_cells(rec, [PORTS[p] for p in ports], require_wet=wet_sep)
    hsep = read_cells(rec, "sep", pc)
    hcoef, *_ = np.linalg.lstsq(X, np.nan_to_num(hsep - np.nanmean(hsep, 0)), rcond=None)
    hw = {}
    for j, p in enumerate(ports):
        h = Xy @ hcoef[:, j]
        i = np.where((h[1:-1] > h[:-2]) & (h[1:-1] >= h[2:]))[0] + 1
        # keep the main semidiurnal HWs (drop double-HW wiggles: at least 8 h apart)
        keep = [i[0]]
        for ii in i[1:]:
            if minutes_year[ii] - minutes_year[keep[-1]] >= 8 * 60:
                keep.append(ii)
            elif h[ii] > h[keep[-1]]:
                keep[-1] = ii
        keep = np.array(keep)
        lw = np.array([h[max(0, k - 40):k + 40].min() for k in keep])
        rng = h[keep] - lw
        m2 = np.hypot(*hcoef[1 + 2 * chosen.index("M2"):3 + 2 * chosen.index("M2"), j])
        if "S2" in chosen:
            s2 = np.hypot(*hcoef[1 + 2 * chosen.index("S2"):3 + 2 * chosen.index("S2"), j])
        else:  # inferred: FES current ratio, a fair stand-in for the height ratio here
            s2 = m2 * ratios["S2"][0]
        msr, mnr = 2 * (m2 + s2), 2 * (m2 - s2)
        spring = keep[np.abs(rng - msr) < 0.05 * msr]
        neap = keep[np.abs(rng - mnr) < 0.05 * mnr]
        hw[p] = (spring, neap)
        print(f"{p}: M2 {m2:.2f} m, S2 {s2:.2f} m; mean spring range {msr:.2f} m ({len(spring)} tides), "
              f"neap {mnr:.2f} m ({len(neap)} tides)")

    cells = nearest_cells(rec, [(float(a["lat"]), float(a["lon"])) for a in arrows], require_wet=wet_u)
    U = read_cells(rec, "velu", cells)
    V = read_cells(rec, "velv", cells)
    cu, *_ = np.linalg.lstsq(X, np.nan_to_num(U), rcond=None)
    cv, *_ = np.linalg.lstsq(X, np.nan_to_num(V), rcond=None)

    res = []
    for j, a in enumerate(arrows):
        sp_i, np_i = hw[a["ref_port"]]
        off = int(round(float(a["hour_rel_hw"]) * 6))
        out = {}
        for name, idx in (("spring", sp_i), ("neap", np_i)):
            idx = idx + off
            idx = idx[(idx >= 0) & (idx < len(minutes_year))]
            u = Xy[idx] @ cu[:, j]
            v = Xy[idx] @ cv[:, j]
            um, vm = u.mean(), v.mean()
            out[name] = (np.hypot(um, vm) * D.KTS, (90 - np.degrees(np.arctan2(vm, um))) % 360)
        dset = (out["spring"][1] - float(a["bearing"]) + 180) % 360 - 180
        res.append({**a, "model_spring_kn": round(out["spring"][0], 2), "model_neap_kn": round(out["neap"][0], 2),
                    "model_set": round(out["spring"][1]), "dset": round(dset)})
    sp_err = np.array([r["model_spring_kn"] - float(r["spring_kn"]) for r in res])
    np_err = np.array([r["model_neap_kn"] - float(r["neap_kn"]) for r in res])
    ds = np.array([r["dset"] for r in res])
    strong = np.array([float(r["spring_kn"]) >= 1.0 for r in res])
    print(f"\n{len(res)} arrows ({strong.sum()} with spring rate >= 1 kn)")
    for lab, m in (("all", np.ones(len(res), bool)), ("strong", strong)):
        print(f"  {lab:6s} spring rate diff (model-HP33) median {np.median(sp_err[m]):+.2f} kn, "
              f"MAE {np.mean(np.abs(sp_err[m])):.2f}; neap MAE {np.mean(np.abs(np_err[m])):.2f}; "
              f"set diff median |d| {np.median(np.abs(ds[m])):.0f} deg, p90 {np.percentile(np.abs(ds[m]), 90):.0f}")
    for port in ports:
        m = np.array([r["ref_port"] == port for r in res]) & strong
        if m.any():
            print(f"  {port:10s} strong: spring diff median {np.median(sp_err[m]):+.2f} kn, "
                  f"|dset| median {np.median(np.abs(ds[m])):.0f} deg")
    if args.scan_hours:
        for port in ports:
            m = np.array([a["ref_port"] == port and float(a["spring_kn"]) >= 1.0 for a in arrows])
            sp_i, _ = hw[port]
            print(f"  {port}: hour offset scan (spring), strong arrows: median |dset|, median rate diff")
            for h in range(-6, 7):
                idx = sp_i + h * 6
                idx = idx[(idx >= 0) & (idx < len(minutes_year))]
                um = (Xy[idx] @ cu[:, m]).mean(0)
                vm = (Xy[idx] @ cv[:, m]).mean(0)
                setm = (90 - np.degrees(np.arctan2(vm, um))) % 360
                dd = (setm - np.array([float(a["bearing"]) for a, k in zip(arrows, m) if k]) + 180) % 360 - 180
                rate = np.hypot(um, vm) * D.KTS - np.array([float(a["spring_kn"]) for a, k in zip(arrows, m) if k])
                print(f"    HW{h:+d}: {np.median(np.abs(dd)):5.0f} deg  {np.median(rate):+.2f} kn")
    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(res[0]))
            w.writeheader()
            w.writerows(res)
        print("per-arrow results:", args.out)


if __name__ == "__main__":
    main()
