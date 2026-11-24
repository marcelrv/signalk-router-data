#!/usr/bin/env python3
"""Leisure-traffic density raster on the NorKyst v3 grid.

Source: EMODnet Human Activities vessel density, annual averages, ship types
04 (Sailing) + 05 (Pleasure craft), CC-BY 4.0 (originator Cogea Srl).
The provider's WCS fails server-side ("Failed to read the coverage"), so the
per-type annual-average GeoTIFF zips are used. They are 1 km, EPSG:3035
(LAEA), float32, hours/km2/month; read with Pillow (no GDAL).
Output density.npz: density (Y,X float32, NaN outside raster), p50/p80/p90/p95
per region (non-zero cells inside the region box).
"""
import argparse
import glob
import os
import shutil
import sys
import urllib.request
import zipfile

import numpy as np
from PIL import Image
from pyproj import Transformer

Image.MAX_IMAGE_PIXELS = None
BASE = ("https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/"
        "0f2f3ff1-30ef-49e1-96e7-8ca78d58a07c/attachments/")
OPENDAP = "https://thredds.met.no/thredds/dodsC/fou-hi/norkystv3_800m_m00_be"
# name: (lat0, lat1, lon0, lon1)
REGIONS = {
    "south": (57.8, 60.2, 5.5, 11.5),
    "west": (58.8, 64.0, 4.0, 11.0),
    "nordland": (63.7, 68.0, 9.5, 17.0),
    "north": (67.5, 71.5, 12.0, 31.5),
}


def fetch_types(ais, types, years):
    """Download+extract each type's zip into its own directory; require every
    requested type/year TIFF (extract to a temp dir, rename when complete)."""
    for t in types:
        d = os.path.join(ais, f"ex_{t}Avg")
        need = [f"vesseldensity_{t}_{y}.tif" for y in years]

        def have():
            return {os.path.basename(f) for f in glob.glob(os.path.join(d, "**", "*.tif"), recursive=True)}
        if not set(need) <= have():
            zp = os.path.join(ais, "zips", f"EMODnet_HA_Vessel_Density_{t}Avg.zip")
            os.makedirs(os.path.dirname(zp), exist_ok=True)
            if not os.path.exists(zp):
                print("downloading", zp, file=sys.stderr)
                part = zp + ".part"  # a cut-off download must not look finished
                with urllib.request.urlopen(BASE + os.path.basename(zp), timeout=120) as r, open(part, "wb") as fh:
                    shutil.copyfileobj(r, fh)
                os.replace(part, zp)
            tmp = d + ".tmp"
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp)
            with zipfile.ZipFile(zp) as z:
                for n in z.namelist():  # flat extract, no path traversal
                    if n.endswith(".tif"):
                        with open(os.path.join(tmp, os.path.basename(n)), "wb") as f:
                            f.write(z.read(n))
            if not set(need) <= {f for f in os.listdir(tmp)}:
                miss = sorted(set(need) - set(os.listdir(tmp)))
                shutil.rmtree(tmp, ignore_errors=True)
                sys.exit(f"type {t}: zip lacks {miss}")
            shutil.rmtree(d, ignore_errors=True)
            os.rename(tmp, d)
        miss = sorted(set(need) - have())
        if miss:
            sys.exit(f"type {t}: missing input TIFFs {miss}")


def read_tif(path):
    """Return float32 array (nodata->NaN) and (x0, y0_top, pixel_m) in EPSG:3035."""
    im = Image.open(path)
    tags = im.tag_v2
    sx = tags[33550][0]
    x0, y0 = tags[33922][3], tags[33922][4]  # top-left corner (PixelIsArea)
    a = np.array(im, dtype=np.float32)
    nd = float(tags.get(42113, -3.4e38))
    a[(a == nd) | (a < -1e30)] = np.nan
    return a, (x0, y0, sx)


def load_grid(root):
    import netCDF4
    p = os.path.join(root, "data", "grid.nc")
    if not os.path.exists(p):
        p = os.path.join(root, "ais", "grid_latlon.nc")
        if not os.path.exists(p):
            print("fetching lat/lon via OPeNDAP", file=sys.stderr)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with netCDF4.Dataset(OPENDAP) as s, netCDF4.Dataset(p, "w") as o:
                for k in ("Y", "X"):
                    o.createDimension(k, len(s.dimensions[k]))
                for v in ("lat", "lon"):
                    o.createVariable(v, "f8", ("Y", "X"))[:] = s[v][:]
    with netCDF4.Dataset(p) as d:
        return np.ma.filled(d["lat"][:], np.nan), np.ma.filled(d["lon"][:], np.nan)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default="/home/node/signalkdev/norkyst-collector")
    ap.add_argument("--years", default="2017-2023", help="a-b or comma list (2024 skipped: data loss)")
    ap.add_argument("--types", default="04,05")
    ap.add_argument("--no-plot", action="store_true")
    a = ap.parse_args()
    ais = os.path.join(a.root, "ais")
    out = os.path.join(a.root, "density.npz")
    types = a.types.split(",")
    if "-" in a.years:
        lo, hi = map(int, a.years.split("-"))
        years = list(range(lo, hi + 1))
    else:
        years = [int(y) for y in a.years.split(",")]

    lat, lon = load_grid(a.root)
    fetch_types(ais, types, years)

    # grid -> EPSG:3035 pixel indices (nearest neighbour)
    gx, gy = Transformer.from_crs(4326, 3035, always_xy=True).transform(lon, lat)
    tot = cnt = None
    for y in years:
        yr = None
        for t in types:
            f = glob.glob(os.path.join(ais, f"ex_{t}Avg", "**", f"vesseldensity_{t}_{y}.tif"), recursive=True)
            if not f:
                sys.exit(f"missing input {t} {y}")
            r, (x0, y0, px) = read_tif(f[0])
            yr = r if yr is None else np.where(np.isnan(yr) & np.isnan(r), np.nan, np.nan_to_num(yr) + np.nan_to_num(r))
        if yr is None:
            continue
        if tot is None:
            tot, cnt = np.zeros_like(yr), np.zeros(yr.shape, np.int16)
        tot += np.nan_to_num(yr)
        cnt += np.isfinite(yr)
    tot = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan).astype(np.float32)  # mean over years with data
    ci = np.floor((gx - x0) / px).astype(int)
    ri = np.floor((y0 - gy) / px).astype(int)
    ok = (ci >= 0) & (ci < tot.shape[1]) & (ri >= 0) & (ri < tot.shape[0]) & np.isfinite(lat)
    dens = np.full(lat.shape, np.nan, np.float32)
    dens[ok] = tot[ri[ok], ci[ok]]

    pct, masks = {}, {}
    for k, (la0, la1, lo0, lo1) in REGIONS.items():
        box = (lat >= la0) & (lat <= la1) & (lon >= lo0) & (lon <= lo1)
        v = dens[box]
        v = v[np.isfinite(v) & (v > 0)]
        pct[k] = np.percentile(v, [50, 80, 90, 95]) if v.size else np.full(4, np.nan)
        masks[k] = box & (dens > pct[k][1])
    save = {"density": dens, "years": np.array(years), "region_names": np.array(list(REGIONS))}
    for i, p in enumerate(("p50", "p80", "p90", "p95")):
        save[p] = np.array([pct[k][i] for k in REGIONS], np.float32)  # order = region_names
        for k in REGIONS:
            save[f"{p}_{k}"] = np.float32(pct[k][i])
    np.savez_compressed(out, **save)
    print("wrote", out, "years", years)

    for k, (la0, la1, lo0, lo1) in REGIONS.items():
        box = (lat >= la0) & (lat <= la1) & (lon >= lo0) & (lon <= lo1)
        nz = int((box & (dens > 0)).sum())
        m = int(masks[k].sum())
        print(f"{k:9s} p50/80/90/95 = " + "/".join(f"{x:.3g}" for x in pct[k]) +
              f"  cells>0={nz}  cells>p80={m}  p80 area={m * 0.64:.0f} km2")

    if not a.no_plot:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for k, (la0, la1, lo0, lo1) in REGIONS.items():
            box = (lat >= la0) & (lat <= la1) & (lon >= lo0) & (lon <= lo1)
            rr, cc = np.where(box)
            sl = (slice(rr.min(), rr.max() + 1), slice(cc.min(), cc.max() + 1))
            z = np.where(box, dens, np.nan)[sl]
            z = np.log10(np.where(z > 0, z, np.nan))
            fig, ax = plt.subplots(figsize=(12, 12 * z.shape[0] / z.shape[1] + 0.8))
            im = ax.imshow(z, origin="lower", cmap="viridis", interpolation="nearest")
            ax.contour(masks[k][sl].astype(float), levels=[0.5], colors="red", linewidths=0.5)
            ax.set_title(f"{k}: log10 leisure density (h/km2/month), red = > p80 ({pct[k][1]:.3g})")
            fig.colorbar(im, ax=ax, shrink=0.6)
            fp = os.path.join(ais, f"density_{k}.png")
            fig.savefig(fp, dpi=110, bbox_inches="tight")
            plt.close(fig)
            print("png", fp)


if __name__ == "__main__":
    main()
