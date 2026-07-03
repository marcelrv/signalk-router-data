#!/usr/bin/env python3
"""
Master orchestrator for the tide/current data index.

Discovers every script in sources/*.py (excluding _tools/), runs each as a
subprocess, collects their JSON fragments, validates, and merges into a
single tide-current-index.json.

Usage:
    python3 generate_index.py --output-dir .
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


TOP_LEVEL_SCHEMA = {
    "version": 1,
    "generated": "",
    "source_count": 0,
    "sources": [],
}

REQUIRED_SOURCE_FIELDS = [
    "id", "type", "name", "description", "source",
    "region", "update_check", "files",
]

OPTIONAL_SOURCE_FIELDS = ["tags"]

REQUIRED_REGION_FIELDS = ["boundary_geometry"]
REQUIRED_UPDATE_FIELDS = ["method"]


def validate_source(src: dict, path: str) -> list[str]:
    errors = []
    for field in REQUIRED_SOURCE_FIELDS:
        if field not in src or src[field] is None:
            errors.append(f"  missing or null '{field}'")
    if "region" in src:
        for f in REQUIRED_REGION_FIELDS:
            if f not in src["region"]:
                errors.append(f"  region missing '{f}'")
    if "update_check" in src:
        for f in REQUIRED_UPDATE_FIELDS:
            if f not in src["update_check"]:
                errors.append(f"  update_check missing '{f}'")
    if "files" in src and isinstance(src["files"], list):
        for i, entry in enumerate(src["files"]):
            if "filename" not in entry and "url_template" not in entry:
                errors.append(f"  files[{i}] must have 'filename' or 'url_template'")
    if "tags" in src:
        if not isinstance(src["tags"], list):
            errors.append(f"  'tags' must be an array, got {type(src['tags']).__name__}")
        elif not all(isinstance(t, str) for t in src["tags"]):
            errors.append("  all 'tags' entries must be strings")

    if errors:
        errors.insert(0, f"{path}:")
    return errors


def main():
    parser = argparse.ArgumentParser(
        description="Generate tide-current-index.json from source collectors."
    )
    parser.add_argument(
        "--output-dir", default=".",
        help="Output directory for tide-current-index.json (default: .)",
    )
    args = parser.parse_args()

    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.isdir(scripts_dir):
        print(f"Sources directory not found: {scripts_dir}", file=sys.stderr)
        sys.exit(1)

    collector_pattern = os.path.join(scripts_dir, "*.py")
    scripts = sorted(glob.glob(collector_pattern))

    if not scripts:
        print("No collector scripts found in sources/", file=sys.stderr)
        sys.exit(1)

    fragments = []
    errors = []

    for script_path in scripts:
        name = os.path.basename(script_path)
        print(f"  Running {name} ...", file=sys.stderr)
        try:
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"{name}: timed out")
            continue
        except OSError as e:
            errors.append(f"{name}: failed to run — {e}")
            continue

        if result.returncode != 0:
            stderr = result.stderr.strip()
            errors.append(f"{name}: exited {result.returncode}"
                          + (f" ({stderr})" if stderr else ""))
            continue

        if not result.stdout.strip():
            errors.append(f"{name}: produced no output")
            continue

        try:
            fragment = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            errors.append(f"{name}: invalid JSON — {e}")
            continue

        ve = validate_source(fragment, name)
        if ve:
            errors.extend(ve)
            continue

        # stamp last_checked for update_check
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fragment["update_check"]["last_checked"] = now

        fragments.append(fragment)

    # Build index
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index = {
        "version": 1,
        "generated": now,
        "source_count": len(fragments),
        "sources": fragments,
    }

    os.makedirs(output_dir, exist_ok=True)
    index_path = os.path.join(output_dir, "tide-current-index.json")
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)

    print(f"  Written {index_path} with {len(fragments)} sources", file=sys.stderr)

    if errors:
        print("\nWarnings / errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
