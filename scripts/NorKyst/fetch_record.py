#!/usr/bin/env python3
"""Resumable OPeNDAP fetcher for NorKyst v3 surface currents (cropped to coastal tiles).

  fetch_record.py grid                     # lat/lon/h + wet mask -> <cache>/grid.nc
  fetch_record.py fetch [--dry-run]        # default window: last 366 full analysis days
  fetch_record.py fetch --validation       # earlier 60-day window -> <cache>/validation/
  fetch_record.py bench                    # throughput test (3 tiles x 2 days, workers 1 and 4)

Output: <cache>/<fit|validation>/tile_YYY_XXX/YYYY-MM.nc (int16 u/v, scale attrs, time, tile offsets).
"""
import argparse, datetime as dt, glob, os, sys, time, urllib.parse, urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import netCDF4
from scipy import ndimage

URL = "https://thredds.met.no/thredds/dodsC/fou-hi/norkystv3_800m_m00_be"
CACHE = "/home/node/signalkdev/norkyst-collector/data"
TILE = 64
CELL_KM = 0.8
FILL = -32767
# Last ~5 days of the time axis are forecast; stay clear of them.
FORECAST_MARGIN_DAYS = 6
VALIDATION_DAYS = 60
REGIONS = {  # lat0, lat1, lon0, lon1
    "south": (57.8, 60.2, 5.5, 11.5), "west": (58.8, 64.0, 4.0, 11.0),
    "nordland": (63.7, 68.0, 9.5, 17.0), "north": (67.5, 71.5, 12.0, 31.5)}
NC_LOCK = threading.Lock()  # netCDF-C is not thread-safe; downloads run parallel, file writes do not
EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def open_ds():
    return netCDF4.Dataset(URL)


# ---------------------------------------------------------------- grid
def build_grid(cache):
    path = os.path.join(cache, "grid.nc")
    if os.path.exists(path):
        return path
    ds = open_ds()
    lat, lon, h = (ds[k][:].filled(np.nan) for k in ("lat", "lon", "h"))
    u = ds["u_eastward"]
    u.set_auto_maskandscale(False)
    ti = len(ds["time"]) - 8 * 24  # a real analysis step
    raw = u[ti, 0, :, :]
    tmp = path + ".tmp"
    with netCDF4.Dataset(tmp, "w") as o:
        o.createDimension("Y", lat.shape[0]); o.createDimension("X", lat.shape[1])
        for k, v in (("lat", lat), ("lon", lon), ("h", h)):
            o.createVariable(k, "f8", ("Y", "X"), zlib=True)[:] = v
        o.createVariable("wet", "i1", ("Y", "X"), zlib=True)[:] = raw != FILL
        o.sample_time_index = ti
    os.replace(tmp, path)
    return path


def load_grid(cache):
    with netCDF4.Dataset(build_grid(cache)) as g:
        return g["lat"][:], g["lon"][:], g["wet"][:].astype(bool)


def select_tiles(cache, coast_km):
    lat, lon, wet = load_grid(cache)
    in_box = np.zeros(wet.shape, bool)
    for a, b, c, d in REGIONS.values():
        in_box |= (lat >= a) & (lat <= b) & (lon >= c) & (lon <= d)
    near = ndimage.distance_transform_edt(wet) * CELL_KM <= coast_km  # distance to nearest dry cell
    keep = wet & in_box & near
    tiles, cells = [], 0
    ny, nx = wet.shape
    for y0 in range(0, ny, TILE):
        for x0 in range(0, nx, TILE):
            k = keep[y0:y0 + TILE, x0:x0 + TILE]
            if k.any():
                tiles.append((y0, min(y0 + TILE, ny), x0, min(x0 + TILE, nx)))
                cells += int(k.sum())
    area = sum((t[1] - t[0]) * (t[3] - t[2]) for t in tiles)
    print(f"tiles={len(tiles)} wet_cells_kept={cells} tile_cells={area} "
          f"share_of_grid={area / wet.size:.1%} (wet-kept {cells / wet.size:.1%})")
    return tiles


# ---------------------------------------------------------------- time
def time_axis():
    ds = open_ds()
    t = ds["time"][:]
    ds.close()
    return np.asarray(t, dtype="f8")


def fit_start_date(times, cache):
    """First day of the fit window: earliest timestamp in the fit cache if present,
    else the start the fit window would resolve to."""
    firsts = []
    for d in sorted(glob.glob(os.path.join(cache, "fit", "tile_*"))):
        fs = sorted(glob.glob(os.path.join(d, "*.nc")))
        if fs:
            with netCDF4.Dataset(fs[0]) as ds:
                firsts.append(float(ds["time"][0]))
    if firsts:
        return (EPOCH + dt.timedelta(seconds=min(firsts))).date()
    last_day = (EPOCH + dt.timedelta(seconds=float(times[-1]))).date()
    return last_day - dt.timedelta(days=FORECAST_MARGIN_DAYS + 365)


def resolve_window(times, a, cache=CACHE):
    last_day = (EPOCH + dt.timedelta(seconds=float(times[-1]))).date()
    cutoff = last_day - dt.timedelta(days=FORECAST_MARGIN_DAYS)  # last analysis day
    if a.validation:
        fit_start = fit_start_date(times, cache)
        end = a.end or fit_start - dt.timedelta(days=1)
        if end >= fit_start:
            sys.exit(f"validation window ends {end}, overlapping the fit window starting {fit_start}")
        start = a.start or end - dt.timedelta(days=VALIDATION_DAYS - 1)
    else:
        end, start = a.end or cutoff, a.start
        start = start or end - dt.timedelta(days=365)
    s = (dt.datetime.combine(start, dt.time(), dt.timezone.utc) - EPOCH).total_seconds()
    e = (dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), dt.timezone.utc) - EPOCH).total_seconds()
    i0 = int(np.searchsorted(times, s, "left"))
    i1 = int(np.searchsorted(times, e, "left"))
    return start, end, i0, i1


# ---------------------------------------------------------------- fetch
def month_of(sec):
    return (EPOCH + dt.timedelta(seconds=float(sec))).strftime("%Y-%m")


def jobs(times, tiles, i0, i1, hpr, outdir):
    """Yield (tile, month, [(ia, ib), ...]) work units; one output file per tile-month."""
    months = {}
    for i in range(i0, i1):
        months.setdefault(month_of(times[i]), []).append(i)
    for t in tiles:
        for m, idx in months.items():
            path = os.path.join(outdir, f"tile_{t[0]:04d}_{t[2]:04d}", f"{m}.nc")
            chunks = [(idx[k], idx[min(k + hpr, len(idx)) - 1] + 1) for k in range(0, len(idx), hpr)]
            yield t, path, idx, chunks


def retry(fn, tries=5):
    for n in range(tries):
        try:
            return fn()
        except Exception as e:  # network/server hiccups
            if n == tries - 1:
                raise
            wait = 2 ** n * 2
            log(f"  retry in {wait}s after {type(e).__name__}: {str(e)[:80]}")
            time.sleep(wait)


def dods(var, a, b, y0, y1, x0, x1):
    """Surface slice [a:b, 0, y0:y1, x0:x1] as raw int16 via plain .dods (thread-safe, unlike netCDF-C)."""
    q = f"{var}.{var}[{a}:{b - 1}][0][{y0}:{y1 - 1}][{x0}:{x1 - 1}]"
    with urllib.request.urlopen(URL + ".dods?" + urllib.parse.quote(q), timeout=180) as r:
        body = r.read()
    i = body.index(b"\nData:\n") + 7
    n = int.from_bytes(body[i:i + 4], "big")
    arr = np.frombuffer(body, ">i4", n, i + 8)  # DAP2 sends Int16 array elements as 4-byte XDR ints
    return arr.astype("i2").reshape(b - a, y1 - y0, x1 - x0)


def unit_done(unit, times):
    """True if the tile-month file exists and holds exactly the requested hours."""
    _, path, idx, _ = unit
    if not os.path.exists(path):
        return False
    try:
        with netCDF4.Dataset(path) as d:
            return np.array_equal(d["time"][:].astype("f8"), times[idx])
    except Exception:
        return False


def fetch_unit(unit, times, stats):
    t, path, idx, chunks = unit  # run_jobs already skipped finished units (netCDF reads need NC_LOCK)
    y0, y1, x0, x1 = t
    U = np.empty((len(idx), y1 - y0, x1 - x0), "i2"); V = np.empty_like(U)
    pos = 0
    for a, b in chunks:
        t0 = time.time()
        U[pos:pos + b - a] = retry(lambda: dods("u_eastward", a, b, y0, y1, x0, x1))
        V[pos:pos + b - a] = retry(lambda: dods("v_northward", a, b, y0, y1, x0, x1))
        pos += b - a
        stats.append((2, U[0].nbytes * (b - a) * 2, time.time() - t0))  # (requests, stored bytes, seconds)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with NC_LOCK, netCDF4.Dataset(tmp, "w") as o:
        o.createDimension("time", len(idx)); o.createDimension("y", y1 - y0); o.createDimension("x", x1 - x0)
        o.createVariable("time", "f8", ("time",))[:] = times[idx]
        o["time"].units = "seconds since 1970-01-01 00:00:00"
        for k, a in (("u_eastward", U), ("v_northward", V)):
            var = o.createVariable(k, "i2", ("time", "y", "x"), zlib=True, fill_value=FILL)
            var.set_auto_maskandscale(False)
            var[:] = a
            var.scale_factor = 0.001; var.add_offset = 0.0
        o.y0, o.y1, o.x0, o.x1 = y0, y1, x0, x1
        o.time_index0 = idx[0]
    os.replace(tmp, path)


def run_jobs(units, times, workers):
    stats, t0 = [], time.time()
    todo = [u for u in units if not unit_done(u, times)]
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(fetch_unit, u, times, stats) for u in todo]
        try:
            for f in as_completed(futs):  # completion order, so a failure is seen at once
                f.result()
                done += 1
                if done % 10 == 0 or done == len(todo):
                    log(f"{done}/{len(todo)} files, {time.time() - t0:.0f}s")
        except BaseException:  # a failed unit or Ctrl-C: drop the queue, let running units finish
            for f in futs:
                f.cancel()
            raise
    wall = time.time() - t0
    req = sum(s[0] for s in stats); mb = sum(s[1] for s in stats) / 1e6
    return wall, req, mb


# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["grid", "fetch", "bench"])
    p.add_argument("--cache", default=CACHE)
    p.add_argument("--coast-km", type=float, default=15)
    p.add_argument("--start", type=dt.date.fromisoformat)
    p.add_argument("--end", type=dt.date.fromisoformat)
    p.add_argument("--validation", action="store_true")
    p.add_argument("--hours-per-request", type=int, default=24)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    if a.cmd == "grid":
        build_grid(a.cache); select_tiles(a.cache, a.coast_km); return
    tiles = select_tiles(a.cache, a.coast_km)
    times = time_axis()
    if a.cmd == "bench":
        s = dt.date(2025, 6, 1)
        a.start, a.end, a.validation = s, s + dt.timedelta(days=1), False
        tiles = tiles[len(tiles) // 2:][:3]
        a.cache = os.path.join(a.cache, "bench")
    start, end, i0, i1 = resolve_window(times, a, a.cache)
    outdir = os.path.join(a.cache, "validation" if a.validation else "fit")
    hours = i1 - i0
    nreq = len(tiles) * -(-hours // a.hours_per_request) * 2
    gb = sum((t[1] - t[0]) * (t[3] - t[2]) for t in tiles) * hours * 4 / 1e9  # raw int16 u+v
    print(f"window {start}..{end} hours={hours} tiles={len(tiles)} requests~{nreq} raw={gb:.1f} GB")
    if a.dry_run:
        n = sum(not unit_done(u, times) for u in jobs(times, tiles, i0, i1, a.hours_per_request, outdir))
        print(f"files to fetch: {n}")
        return
    if a.cmd == "bench":
        import shutil
        for w in (1, 4):  # different days per run so neither benefits from the server's warm cache
            a.start = dt.date(2025, 6, 1) + dt.timedelta(days=10 * w); a.end = a.start + dt.timedelta(days=1)
            start, end, i0, i1 = resolve_window(times, a, a.cache)
            shutil.rmtree(outdir, ignore_errors=True)
            units = list(jobs(times, tiles, i0, i1, a.hours_per_request, outdir))
            wall, req, mb = run_jobs(units, times, w)
            print(f"workers={w}: wall={wall:.1f}s requests={req} MB={mb:.2f} "
                  f"MB/s={mb / wall:.3f} s/request(wall)={wall / req:.2f}")
        return
    run_jobs(list(jobs(times, tiles, i0, i1, a.hours_per_request, outdir)), times, a.workers)


if __name__ == "__main__":
    main()
