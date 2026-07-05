#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""
Batch FES2024 UTCEF Generator
Orchestrates generation of all regional FES2024 UTCEF files from the basin and region configs.
Reads all fes2024_*.json region definitions, merges them into a single config,
and invokes fes_to_utcef.py to generate the regional databases.
"""

import os
import sys
import json
import glob
import subprocess
import argparse
from datetime import datetime
from pathlib import Path

def load_region_configs(config_source_dir):
    """Load all fes2024_*.json files and merge into a unified region dict."""
    config_files = sorted(glob.glob(os.path.join(config_source_dir, "fes2024_*.json")))
    # Exclude the merged config file
    config_files = [f for f in config_files if not f.endswith('merged_config.json')]

    if not config_files:
        print(f"[Error] No fes2024_*.json files found in {config_source_dir}")
        return {}, {}

    print(f"Found {len(config_files)} regional config files:")
    for cf in config_files:
        print(f"  - {os.path.basename(cf)}")

    merged_regions = {}
    basin_mapping = {}  # Track which basin each region belongs to

    for config_file in config_files:
        with open(config_file, 'r', encoding='utf-8') as f:
            catalog = json.load(f)

        basin = catalog.get('basin', 'unknown')
        print(f"\nProcessing {basin}...")

        for region in catalog.get('regions', []):
            region_id = region['region_id']
            bbox = [
                region['bounding_box']['min_lon'],
                region['bounding_box']['min_lat'],
                region['bounding_box']['max_lon'],
                region['bounding_box']['max_lat']
            ]

            # Build minimal region config for fes_to_utcef.py
            merged_regions[region_id] = {
                'title': region['name'],
                'description': region['description'],
                'bbox': bbox,
                'stations': [],
                'current_grids': [
                    {
                        'id_prefix': region_id.upper(),
                        'step': _get_resolution_step(region['resolution_code']),
                        'lat_min': bbox[1],
                        'lat_max': bbox[3],
                        'lon_min': bbox[0],
                        'lon_max': bbox[2]
                    }
                ],
                'region_id': region_id,
                'basin': basin,
                'traffic_level': region.get('traffic_level', 'moderate'),
                'key_features': region.get('key_features', [])
            }
            basin_mapping[region_id] = _map_basin_to_folder(basin)
            print(f"  + {region_id} ({region['name']}) - {region['resolution']} grid")

    print(f"\n[Summary] Merged {len(merged_regions)} total regions across all basins")
    return merged_regions, basin_mapping

def _get_resolution_step(resolution_code):
    """Convert resolution code to decimal degree grid step."""
    mapping = {
        '1/16': 1/16,    # ~6.9 km
        '1/8': 1/8,      # ~13.8 km
        '1/4': 1/4,      # ~27.6 km
    }
    return mapping.get(resolution_code, 1/16)

def _map_basin_to_folder(basin_name):
    """Map basin name to output folder name."""
    mapping = {
        'Atlantic': 'atlantic',
        'Europe & Mediterranean': 'mediterranean',
        'Indian Ocean': 'indian_ocean',
        'Pacific North & Asia': 'pacific_asia',
        'Pacific South & Oceania': 'pacific_oceania',
    }
    return mapping.get(basin_name, 'other')

def create_merged_config(merged_regions, output_path):
    """Write merged regions to a single config file for fes_to_utcef.py."""
    config = {
        'regions': merged_regions
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\n[Config] Saved merged config to: {output_path}")
    return output_path

def run_fes_conversion(config_path, fes_dir, merged_regions, basin_mapping, region_id='all'):
    """Invoke fes_to_utcef.py for specified regions, routing each to correct basin folder."""
    script_path = os.path.join(os.path.dirname(__file__), 'FES2014', 'fes_to_utcef.py')

    if not os.path.exists(script_path):
        print(f"[Error] Conversion script not found: {script_path}")
        return False

    if not os.path.exists(fes_dir):
        print(f"[Error] FES data directory not found: {fes_dir}")
        print("Please download and extract FES data to this directory.")
        return False

    # Determine which regions to process
    if region_id == 'all':
        regions_to_process = sorted(merged_regions.keys())
    elif region_id in merged_regions:
        regions_to_process = [region_id]
    else:
        print(f"[Error] Region '{region_id}' not found in merged config.")
        return False

    success_count = 0
    fail_count = 0

    for rid in regions_to_process:
        # Determine output folder for this region
        basin_folder = basin_mapping.get(rid, 'other')
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(project_root, 'regions', basin_folder)
        os.makedirs(output_dir, exist_ok=True)

        cmd = [
            sys.executable,
            script_path,
            '--config', config_path,
            '--fes-dir', fes_dir,
            '--output-dir', output_dir,
            '--region', rid
        ]

        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
            if result.returncode == 0:
                success_count += 1
                print(f"  ✓ {rid:35s} → regions/{basin_folder}/")
            else:
                fail_count += 1
                print(f"  ✗ {rid:35s} failed")
                if result.stderr:
                    print(f"    Error: {result.stderr[:100]}")
        except Exception as e:
            fail_count += 1
            print(f"  ✗ {rid:35s} error: {e}")

    print(f"\n[Summary] {success_count} succeeded, {fail_count} failed")
    return fail_count == 0

def main():
    parser = argparse.ArgumentParser(
        description="Batch FES2024 UTCEF Generator - Orchestrates generation of all regional databases."
    )
    parser.add_argument(
        '--config-dir',
        default='./scripts/FES2014',
        help='Directory containing fes2024_*.json region definitions (default: ./scripts/FES2014)'
    )
    parser.add_argument(
        '--fes-dir',
        default='./scripts/FES2014/tide_models/fes2014',
        help='Path to FES2024/FES2014 NetCDF data (default: ./scripts/FES2014/tide_models/fes2014)'
    )
    parser.add_argument(
        '--region',
        default='all',
        help='Specific region ID to generate, or "all" (default: all)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Load configs and show plan without executing conversion'
    )

    args = parser.parse_args()

    print("=" * 70)
    print("FES2024 UTCEF Batch Generation")
    print("=" * 70)
    print(f"Start time: {datetime.now().isoformat()}\n")

    # Resolve paths
    if not os.path.isabs(args.config_dir):
        args.config_dir = os.path.abspath(args.config_dir)

    if not os.path.isabs(args.fes_dir):
        args.fes_dir = os.path.abspath(args.fes_dir)

    # Load and merge all region configs
    merged_regions, basin_mapping = load_region_configs(args.config_dir)
    if not merged_regions:
        print("[Error] No regions loaded. Aborting.")
        return 1

    # Create temporary merged config
    config_path = os.path.join(args.config_dir, 'fes2024_merged_config.json')
    create_merged_config(merged_regions, config_path)

    # Show generation plan
    print("\n" + "=" * 70)
    if args.region == 'all':
        print(f"Plan: Generate {len(merged_regions)} UTCEF files into basin folders")
        print("\nRegions to be generated:")
        for rid in sorted(merged_regions.keys()):
            r = merged_regions[rid]
            basin_folder = basin_mapping.get(rid, 'other')
            print(f"  • {rid:30s} → regions/{basin_folder:15s} ({r.get('traffic_level', 'moderate')})")
    else:
        if args.region in merged_regions:
            r = merged_regions[args.region]
            basin_folder = basin_mapping.get(args.region, 'other')
            print(f"Plan: Generate 1 UTCEF file")
            print(f"  Region: {args.region} - {r['title']}")
            print(f"  Output: regions/{basin_folder}/")
        else:
            print(f"[Error] Region '{args.region}' not found in merged config.")
            return 1
    print("=" * 70 + "\n")

    if args.dry_run:
        print("[Dry-run] Plan displayed. No conversion executed.")
        return 0

    # Run conversion
    success = run_fes_conversion(config_path, args.fes_dir, merged_regions, basin_mapping, args.region)

    print("=" * 70)
    if success:
        print("✓ Batch generation completed successfully")
        print("Output files saved to:")
        for basin in set(basin_mapping.values()):
            basin_path = f"regions/{basin}/"
            print(f"  • {basin_path}")
    else:
        print("✗ Batch generation failed or encountered errors")
        print("Check FES data availability and file permissions")
    print(f"End time: {datetime.now().isoformat()}")
    print("=" * 70)

    return 0 if success else 1

if __name__ == '__main__':
    sys.exit(main())
