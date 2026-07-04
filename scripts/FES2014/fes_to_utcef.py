#!/usr/bin/env python3
"""
FES2014 to UTCEF (Unified Tidal and Current Exchange Format) Converter
Generates regional oceanographic databases as `.utcef` files — each a standard
ZIP archive (like .apk/.docx) holding a single `<region>.json` payload member.
"""

import os
import json
import math
import zipfile
import argparse
from datetime import datetime, timezone
import numpy as np
from netCDF4 import Dataset

def bilinear_interpolate(lon, lat, grid_lon, grid_lat, data):
    """Performs standard bilinear interpolation over a 2D regular NetCDF grid."""
    grid_min_lon = np.min(grid_lon)
    if grid_min_lon >= 0 and lon < 0:
        lon += 360.0
    elif grid_min_lon < 0 and lon > 180.0:
        lon -= 360.0

    idx_lon = np.searchsorted(grid_lon, lon) - 1
    idx_lat = np.searchsorted(grid_lat, lat) - 1

    if idx_lon < 0 or idx_lon >= len(grid_lon) - 1 or idx_lat < 0 or idx_lat >= len(grid_lat) - 1:
        closest_lon = np.argmin(np.abs(grid_lon - lon))
        closest_lat = np.argmin(np.abs(grid_lat - lat))
        val = data[closest_lat, closest_lon]
        return float(val) if not np.ma.is_masked(val) else 0.0

    x0, x1 = grid_lon[idx_lon], grid_lon[idx_lon + 1]
    y0, y1 = grid_lat[idx_lat], grid_lat[idx_lat + 1]

    q00 = data[idx_lat, idx_lon]
    q10 = data[idx_lat, idx_lon + 1]
    q01 = data[idx_lat + 1, idx_lon]
    q11 = data[idx_lat + 1, idx_lon + 1]

    corners = [q00, q10, q01, q11]
    if any(np.ma.is_masked(c) for c in corners):
        unmasked = [float(c) for c in corners if not np.ma.is_masked(c)]
        return sum(unmasked) / len(unmasked) if unmasked else 0.0

    denom = (x1 - x0) * (y1 - y0)
    wa = (x1 - lon) * (y1 - lat) / denom
    wb = (lon - x0) * (y1 - lat) / denom
    wc = (x1 - lon) * (lat - y0) / denom
    wd = (lon - x0) * (lat - y0) / denom

    return float(wa * q00 + wb * q10 + wc * q01 + wd * q11)


def extract_from_netcdf(nc_path, lat, lon, amp_var="amplitude", phase_var="phase"):
    """Helper to load a NetCDF variable and interpolate at lat, lon."""
    if not os.path.exists(nc_path):
        raise FileNotFoundError(f"Missing FES2014 file: {nc_path}")

    with Dataset(nc_path, "r") as nc:
        lon_name = "lon" if "lon" in nc.variables else "longitude"
        lat_name = "lat" if "lat" in nc.variables else "latitude"

        grid_lon = nc.variables[lon_name][:]
        grid_lat = nc.variables[lat_name][:]

        amp_data = nc.variables[amp_var][:]
        phase_data = nc.variables[phase_var][:]

        amp = bilinear_interpolate(lon, lat, grid_lon, grid_lat, amp_data)
        phase = bilinear_interpolate(lon, lat, grid_lon, grid_lat, phase_data)

        return amp, phase


# FES2014 distributions differ in how the sub-datasets are named on disk: the
# AVISO+ download used here ships `ocean_tide_extrapolated`,
# `eastward_velocity` and `northward_velocity`, while older layouts use
# `ocean_tide`, `eastward_current` and `northward_current`. Resolve the actual
# directory among the known aliases instead of hardcoding one spelling.
FES_SUBDIR_ALIASES = {
    "height": ["ocean_tide_extrapolated", "ocean_tide"],
    "eastward": ["eastward_velocity", "eastward_current"],
    "northward": ["northward_velocity", "northward_current"],
}


def fes_subdir(fes_root_dir, kind):
    """Return the existing FES2014 sub-dataset directory for `kind`."""
    for name in FES_SUBDIR_ALIASES[kind]:
        p = os.path.join(fes_root_dir, name)
        if os.path.isdir(p):
            return p
    raise FileNotFoundError(
        f"None of {FES_SUBDIR_ALIASES[kind]} found under {fes_root_dir} for '{kind}' data"
    )


class RegionCurrentSampler:
    """Loads the current constituents for a region ONCE (a small sub-grid per
    file), then interpolates any number of points cheaply — essential for dense
    grids, where re-reading the full global FES grids per point would be
    hopelessly slow.

    Longitudes are handled in a Greenwich-centred (-180..180) frame so regions
    straddling the 0° meridian (e.g. the English Channel) stay contiguous.
    """

    def __init__(self, fes_root_dir, bbox, constituents, margin=0.5):
        u_dir = fes_subdir(fes_root_dir, "eastward")
        v_dir = fes_subdir(fes_root_dir, "northward")
        lon_min, lat_min, lon_max, lat_max = bbox
        self.grids = {}
        self.sub_lon = None
        self.sub_lat = None
        self.mask = None  # native land/no-data mask (True = land/masked)
        i0 = i1 = None
        col = None

        for con in constituents:
            u_file = os.path.join(u_dir, f"{con}.nc")
            v_file = os.path.join(v_dir, f"{con}.nc")
            if not (os.path.exists(u_file) and os.path.exists(v_file)):
                print(f"  [Warning] Missing current file for '{con}'; skipping constituent.")
                continue
            with Dataset(u_file) as ncu, Dataset(v_file) as ncv:
                if self.sub_lon is None:
                    glon = ncu.variables["lon"][:]
                    glat = ncu.variables["lat"][:]
                    lon_r = np.where(glon > 180.0, glon - 360.0, glon)
                    mlon = (lon_r >= lon_min - margin) & (lon_r <= lon_max + margin)
                    idx_lon = np.where(mlon)[0]
                    idx_lon = idx_lon[np.argsort(lon_r[idx_lon])]  # monotonic ascending
                    mlat = (glat >= lat_min - margin) & (glat <= lat_max + margin)
                    idx_lat = np.where(mlat)[0]
                    i0, i1 = int(idx_lat.min()), int(idx_lat.max()) + 1
                    col = idx_lon
                    self.sub_lon = lon_r[idx_lon]
                    self.sub_lat = glat[i0:i1]
                # Read the (contiguous) latitude slab, then subselect columns.
                Ua = ncu.variables["Ua"][i0:i1, :][:, col]
                Ug = ncu.variables["Ug"][i0:i1, :][:, col]
                Va = ncv.variables["Va"][i0:i1, :][:, col]
                Vg = ncv.variables["Vg"][i0:i1, :][:, col]
            # All velocity constituents share the model's land mask; capture it
            # once (from the first constituent) to gate station placement.
            if self.mask is None:
                self.mask = np.ma.getmaskarray(Ua).copy()
            self.grids[con.upper()] = (Ua, Ug, Va, Vg)

    def sample(self, lat, lon):
        """Return a UTCEF `harmonic_constituents` dict (m/s, Greenwich phase) at lat/lon."""
        hc = {}
        for con, (Ua, Ug, Va, Vg) in self.grids.items():
            ua = bilinear_interpolate(lon, lat, self.sub_lon, self.sub_lat, Ua)
            ug = bilinear_interpolate(lon, lat, self.sub_lon, self.sub_lat, Ug)
            va = bilinear_interpolate(lon, lat, self.sub_lon, self.sub_lat, Va)
            vg = bilinear_interpolate(lon, lat, self.sub_lon, self.sub_lat, Vg)
            hc[con] = {
                "u_amplitude": round(ua / 100.0, 5),  # cm/s -> m/s
                "u_phase_g": round(ug, 2),
                "v_amplitude": round(va / 100.0, 5),
                "v_phase_g": round(vg, 2),
            }
        return hc

    def is_water(self, lat, lon):
        """True only where FES2014 actually has a current cell (the nearest
        native cell is unmasked). This is the authoritative land/enclosed-water
        test — it excludes land, the IJsselmeer/Markermeer (masked by FES) and
        the bilinear values that otherwise leak a few km onto the coast."""
        if self.mask is None or self.sub_lon is None:
            return False
        j = int(np.abs(self.sub_lat - lat).argmin())
        i = int(np.abs(self.sub_lon - lon).argmin())
        return not bool(self.mask[j, i])


def simulate_relative_table(station_lat, station_lon, ref_port_lat, ref_port_lon, fes_root_dir):
    """Simulates 13 relative stream points using FES2014 constituents."""
    height_dir = fes_subdir(fes_root_dir, "height")
    ref_m2_file = os.path.join(height_dir, "m2.nc")
    ref_s2_file = os.path.join(height_dir, "s2.nc")

    _, ref_m2_phase = extract_from_netcdf(ref_m2_file, ref_port_lat, ref_port_lon)
    _, ref_s2_phase = extract_from_netcdf(ref_s2_file, ref_port_lat, ref_port_lon)

    east_dir = fes_subdir(fes_root_dir, "eastward")
    north_dir = fes_subdir(fes_root_dir, "northward")
    u_m2_file = os.path.join(east_dir, "m2.nc")
    u_s2_file = os.path.join(east_dir, "s2.nc")
    v_m2_file = os.path.join(north_dir, "m2.nc")
    v_s2_file = os.path.join(north_dir, "s2.nc")

    u_m2_amp, u_m2_phase = extract_from_netcdf(u_m2_file, station_lat, station_lon, amp_var="Ua", phase_var="Ug")
    u_s2_amp, u_s2_phase = extract_from_netcdf(u_s2_file, station_lat, station_lon, amp_var="Ua", phase_var="Ug")
    v_m2_amp, v_m2_phase = extract_from_netcdf(v_m2_file, station_lat, station_lon, amp_var="Va", phase_var="Vg")
    v_s2_amp, v_s2_phase = extract_from_netcdf(v_s2_file, station_lat, station_lon, amp_var="Va", phase_var="Vg")

    # Convert cm/s to knots
    cm_to_knots = 0.0194384
    u_m2_amp *= cm_to_knots
    u_s2_amp *= cm_to_knots
    v_m2_amp *= cm_to_knots
    v_s2_amp *= cm_to_knots

    omega_m2 = 28.984104
    omega_s2 = 30.000000

    stream_table = []

    for hour in range(-6, 7):
        t_m2_spring = (ref_m2_phase / omega_m2) + hour
        t_s2_spring = (ref_s2_phase / omega_s2) + hour

        t_m2_neap = t_m2_spring
        t_s2_neap = ((ref_s2_phase + 90.0) / omega_s2) + hour

        u_spring = (u_m2_amp * math.cos(math.radians(omega_m2 * t_m2_spring - u_m2_phase)) +
                    u_s2_amp * math.cos(math.radians(omega_s2 * t_s2_spring - u_s2_phase)))
        v_spring = (v_m2_amp * math.cos(math.radians(omega_m2 * t_m2_spring - v_m2_phase)) +
                    v_s2_amp * math.cos(math.radians(omega_s2 * t_s2_spring - v_s2_phase)))

        u_neap = (u_m2_amp * math.cos(math.radians(omega_m2 * t_m2_neap - u_m2_phase)) +
                  u_s2_amp * math.cos(math.radians(omega_s2 * t_s2_neap - u_s2_phase)))
        v_neap = (v_m2_amp * math.cos(math.radians(omega_m2 * t_m2_neap - v_m2_phase)) +
                  v_s2_amp * math.cos(math.radians(omega_s2 * t_s2_neap - v_s2_phase)))

        spring_speed = math.sqrt(u_spring**2 + v_spring**2)
        neap_speed = math.sqrt(u_neap**2 + v_neap**2)
        spring_dir = (90.0 - math.degrees(math.atan2(v_spring, u_spring))) % 360.0

        stream_table.append({
            "hour": hour,
            "direction": round(spring_dir),
            "spring_rate": round(spring_speed, 1),
            "neap_rate": round(neap_speed, 1)
        })

    return stream_table


def build_region(region_id, config, fes_root_dir, output_dir):
    """Extracts and builds a single regional dataset and saves it to a .utcef.gz file."""
    constituents = ["m2", "s2", "n2", "k2", "k1", "o1", "p1", "q1"]
    features = []

    stations = config.get("stations", [])
    current_grids = config.get("current_grids", [])
    port_coords = {s["id"]: (s["lat"], s["lon"]) for s in stations}

    # The region current sampler loads the FES current grids once for the whole
    # region (shared by both named current stations and the dense grids). Built
    # lazily so a heights-only region does not pay for it.
    current_sampler = None

    def get_sampler():
        nonlocal current_sampler
        if current_sampler is None:
            current_sampler = RegionCurrentSampler(fes_root_dir, config["bbox"], constituents)
        return current_sampler

    print(f"Processing region '{region_id}' ({len(stations)} named station(s), "
          f"{len(current_grids)} current grid(s))...")

    for station in stations:
        station_id = station["id"]
        name = station["name"]
        lat = station["lat"]
        lon = station["lon"]
        method = station["prediction_method"]

        # Canonical identity is the top-level Feature.id (UTCEF spec §4.2);
        # properties.station_id is only an optional alias, so it is omitted here.
        properties = {
            "station_name": name,
            "prediction_method": method
        }

        if method == "harmonic_constituents_heights":
            properties["data_unit_height"] = "meters"
            properties["mean_sea_level"] = station.get("mean_sea_level", 0.0)
            properties["harmonic_constituents"] = {}

            elevation_dir = fes_subdir(fes_root_dir, "height")
            for con in constituents:
                try:
                    nc_file = os.path.join(elevation_dir, f"{con}.nc")
                    amp, phase = extract_from_netcdf(nc_file, lat, lon)
                    # FES2014 ocean_tide amplitude is in cm; UTCEF data_unit_height
                    # is meters, so convert cm -> m. Phase is a Greenwich phase lag
                    # in degrees (UTCEF spec §5.0), passed through unchanged.
                    properties["harmonic_constituents"][con.upper()] = {
                        "amplitude": round(amp / 100.0, 5),
                        "phase_g": round(phase, 2)
                    }
                except Exception as e:
                    print(f"  [Warning] Height {con} failed for {station_id}: {e}")

        elif method == "harmonic_constituents_currents":
            properties["data_unit_speed"] = "meters_per_second"
            properties["mean_offset"] = {"u_residual": 0.0, "v_residual": 0.0}
            properties["harmonic_constituents"] = get_sampler().sample(lat, lon)

        elif method == "relative_time_offset":
            ref_port_id = station["reference_port"]
            properties["reference_port"] = ref_port_id
            properties["hours_relative_to"] = "high_water_at_reference_port"
            properties["data_unit_speed"] = "knots"
            properties["interpolation"] = {"method": "linear_range_ratio"}
            
            ref_lat, ref_lon = port_coords.get(ref_port_id, (None, None))
            if ref_lat is None:
                print(f"  [Error] Missing ref port coordinates for {station_id}")
                continue

            try:
                properties["tidal_stream_table"] = simulate_relative_table(
                    lat, lon, ref_lat, ref_lon, fes_root_dir
                )
            except Exception as e:
                print(f"  [Error] Failed to calculate stream table for {station_id}: {e}")
                continue

        feature = {
            "type": "Feature",
            "id": station_id,
            "geometry": {
                "type": "Point",
                "coordinates": [round(lon, 5), round(lat, 5)]
            },
            "properties": properties
        }
        features.append(feature)

    # Dense current grids: expand each into harmonic_constituents_currents
    # stations, but ONLY where FES2014 actually has a current cell (strict land
    # mask — no arrows on land, the IJsselmeer, or leaked onto the coast).
    # Multiple grids of different steps may overlap (coarse offshore + fine in
    # the estuaries/Wadden); a shared dedup grid keeps them from stacking.
    grid_seen = set()
    DEDUP_DEG = 0.02  # two nodes closer than this are treated as one
    for grid in current_grids:
        prefix = grid.get("id_prefix", "GRID")
        step = float(grid["step"])
        lat0, lat1 = float(grid["lat_min"]), float(grid["lat_max"])
        lon0, lon1 = float(grid["lon_min"]), float(grid["lon_max"])
        sampler = get_sampler()
        made = 0
        skipped_land = 0
        skipped_dup = 0
        # Iterate on an integer lattice to avoid floating-point drift.
        nrows = int(round((lat1 - lat0) / step))
        ncols = int(round((lon1 - lon0) / step))
        for r in range(nrows + 1):
            glat = round(lat0 + r * step, 4)
            for c in range(ncols + 1):
                glon = round(lon0 + c * step, 4)
                if not sampler.is_water(glat, glon):
                    skipped_land += 1
                    continue
                key = (round(glat / DEDUP_DEG), round(glon / DEDUP_DEG))
                if key in grid_seen:
                    skipped_dup += 1
                    continue
                grid_seen.add(key)
                node_id = f"{prefix}_{glat:.3f}_{glon:.3f}".replace("-", "m").replace(".", "p")
                features.append({
                    "type": "Feature",
                    "id": node_id,
                    "geometry": {"type": "Point", "coordinates": [glon, glat]},
                    "properties": {
                        "station_name": f"{prefix} grid {glat:.3f},{glon:.3f}",
                        "prediction_method": "harmonic_constituents_currents",
                        "data_unit_speed": "meters_per_second",
                        "mean_offset": {"u_residual": 0.0, "v_residual": 0.0},
                        "harmonic_constituents": sampler.sample(glat, glon),
                    },
                })
                made += 1
        print(f"  [Grid '{prefix}' step {step}°] {made} water node(s) added, "
              f"{skipped_land} land skipped, {skipped_dup} dup skipped.")

    utcef_payload = {
        "metadata": {
            "schema_version": "1.0.0",
            "dataset_version": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
            "title": config["title"],
            "description": "FES2014-derived tidal current database generated by signalk-router-data scripts (https://github.com/marcelrv/signalk-router-data). The underlying FES2014 model is produced by Noveltis, Legos and CLS and distributed by AVISO+. This derived dataset is NOT endorsed by, affiliated with, or supported by AVISO, CNES, or any FES2014 copyright owner. To download or review the raw global FES2014 dataset, visit AVISO+ at: https://www.aviso.altimetry.fr/en/data/products/auxiliary-products/global-tide-fes.html",
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "region": {
                "name": config.get("title", region_id),
                "bbox": config["bbox"]
            },
            "copyright": "Underlying FES2014 data: Copyright © 2014-ongoing Legos/Noveltis/Cnes/CLS. Derived database packaging: see repository license.",
            "license": "AVISO+ License Agreement (Scientific and Non-Commercial redistributions)",
            "license_url": "https://www.aviso.altimetry.fr/fileadmin/documents/data/License_Aviso.pdf",
            "citation_required": "FES2014 was produced by Noveltis, Legos and CLS and distributed by Aviso+, with support from Cnes (https://www.aviso.altimetry.fr/)",
            "data_sources": [
                {"name": "AVISO+ Altimetry Portal", "url": "https://www.aviso.altimetry.fr/", "role": "Distributor"},
                {"name": "LEGOS / Laboratoire d'Etudes en Géophysique et Océanographie Spatiales", "role": "Model Developer"}
            ]
        },
        "dataset": {
            "type": "FeatureCollection",
            "features": features
        }
    }

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    # A `.utcef` file is itself a standard ZIP archive (UTCEF spec "Container":
    # like .apk/.docx) holding a single `<basename>.json` payload member. It can
    # be opened anywhere by renaming the extension to `.zip`.
    output_path = os.path.join(output_dir, f"{region_id}.utcef")
    json_text = json.dumps(utcef_payload, indent=2, ensure_ascii=False)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{region_id}.json", json_text)
    print(f"  [Success] Saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="FES2014 to UTCEF Regional Gzip Converter.")
    parser.add_argument("--fes-dir", default="./tide_models/fes2014", help="Path to raw FES2014 NetCDF folder root.")
    parser.add_argument("--config", default="./regions_config.json", help="Path to regional JSON configuration file.")
    parser.add_argument("--output-dir", default="./dist", help="Directory where processed .utcef.gz files will be saved.")
    parser.add_argument("--region", default="all", help="Region ID to build (e.g. 'north_sea', 'english_channel') or 'all'.")

    args = parser.parse_args()

    # Load configuration
    if not os.path.exists(args.config):
        print(f"[Error] Configuration file '{args.config}' not found.")
        return

    with open(args.config, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    regions = config_data.get("regions", {})

    # Check FES2014 raw directory
    if not os.path.exists(args.fes_dir):
        print(f"[Warning] Raw FES2014 folder '{args.fes_dir}' was not found.")
        print("Please ensure the uncompressed model is placed in that directory to process targets.")
        return

    # Filter and run
    if args.region == "all":
        for r_id, r_config in regions.items():
            build_region(r_id, r_config, args.fes_dir, args.output_dir)
    else:
        if args.region in regions:
            build_region(args.region, regions[args.region], args.fes_dir, args.output_dir)
        else:
            print(f"[Error] Region '{args.region}' not defined in '{args.config}'.")


if __name__ == "__main__":
    main()
