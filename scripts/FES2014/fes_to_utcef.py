#!/usr/bin/env python3
"""
FES2014 to UTCEF (Unified Tidal and Current Exchange Format) Converter
Constructed to generate compressed, regional oceanographic databases (.utcef.gz).
"""

import os
import json
import math
import gzip
import argparse
from datetime import datetime
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


def simulate_relative_table(station_lat, station_lon, ref_port_lat, ref_port_lon, fes_root_dir):
    """Simulates 13 relative stream points using FES2014 constituents."""
    ref_m2_file = os.path.join(fes_root_dir, "ocean_tide", "m2.nc")
    ref_s2_file = os.path.join(fes_root_dir, "ocean_tide", "s2.nc")
    
    _, ref_m2_phase = extract_from_netcdf(ref_m2_file, ref_port_lat, ref_port_lon)
    _, ref_s2_phase = extract_from_netcdf(ref_s2_file, ref_port_lat, ref_port_lon)

    u_m2_file = os.path.join(fes_root_dir, "eastward_current", "m2.nc")
    u_s2_file = os.path.join(fes_root_dir, "eastward_current", "s2.nc")
    v_m2_file = os.path.join(fes_root_dir, "northward_current", "m2.nc")
    v_s2_file = os.path.join(fes_root_dir, "northward_current", "s2.nc")

    u_m2_amp, u_m2_phase = extract_from_netcdf(u_m2_file, station_lat, station_lon)
    u_s2_amp, u_s2_phase = extract_from_netcdf(u_s2_file, station_lat, station_lon)
    v_m2_amp, v_m2_phase = extract_from_netcdf(v_m2_file, station_lat, station_lon)
    v_s2_amp, v_s2_phase = extract_from_netcdf(v_s2_file, station_lat, station_lon)

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

    stations = config["stations"]
    port_coords = {s["id"]: (s["lat"], s["lon"]) for s in stations}

    print(f"Processing region '{region_id}' ({len(stations)} stations)...")

    for station in stations:
        station_id = station["id"]
        name = station["name"]
        lat = station["lat"]
        lon = station["lon"]
        method = station["prediction_method"]

        properties = {
            "station_id": station_id,
            "station_name": name,
            "prediction_method": method
        }

        if method == "harmonic_constituents_heights":
            properties["data_unit_height"] = "meters"
            properties["mean_sea_level"] = station.get("mean_sea_level", 0.0)
            properties["harmonic_constituents"] = {}

            elevation_dir = os.path.join(fes_root_dir, "ocean_tide")
            for con in constituents:
                try:
                    nc_file = os.path.join(elevation_dir, f"{con}.nc")
                    amp, phase = extract_from_netcdf(nc_file, lat, lon)
                    properties["harmonic_constituents"][con.upper()] = {
                        "amplitude": round(amp, 4),
                        "phase_g": round(phase, 2)
                    }
                except Exception as e:
                    print(f"  [Warning] Height {con} failed for {station_id}: {e}")

        elif method == "harmonic_constituents_currents":
            properties["data_unit_speed"] = "meters_per_second"
            properties["mean_offset"] = {"u_residual": 0.0, "v_residual": 0.0}
            properties["harmonic_constituents"] = {}

            u_dir = os.path.join(fes_root_dir, "eastward_current")
            v_dir = os.path.join(fes_root_dir, "northward_current")

            for con in constituents:
                try:
                    u_file = os.path.join(u_dir, f"{con}.nc")
                    v_file = os.path.join(v_dir, f"{con}.nc")

                    u_amp, u_phase = extract_from_netcdf(u_file, lat, lon)
                    v_amp, v_phase = extract_from_netcdf(v_file, lat, lon)

                    properties["harmonic_constituents"][con.upper()] = {
                        "u_amplitude": round(u_amp / 100.0, 5),
                        "u_phase_g": round(u_phase, 2),
                        "v_amplitude": round(v_amp / 100.0, 5),
                        "v_phase_g": round(v_phase, 2)
                    }
                except Exception as e:
                    print(f"  [Warning] Current {con} failed for {station_id}: {e}")

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

    utcef_payload = {
        "metadata": {
            "schema_version": "1.0.0",
            "dataset_version": datetime.utcnow().strftime("%Y.%m.%d"),
            "title": config["title"],
            "description": "UTCEF compliant database processed from FES2014. To download or review the raw global FES2014 dataset, visit AVISO+ at: https://www.aviso.altimetry.fr/en/data/products/auxiliary-products/global-tide-fes.html",
            "last_updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "region": {
                "name": config.get("title", region_id),
                "bbox": config["bbox"]
            },
            "copyright": "Copyright © 2014-ongoing Legos/Noveltis/Cnes/CLS.",
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
    output_path = os.path.join(output_dir, f"{region_id}.utcef")

    # Write directly to Gzip
    with gzip.open(output_path, "wt", encoding="utf-8") as f:
        json.dump(utcef_payload, f, indent=2, ensure_ascii=False)
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
