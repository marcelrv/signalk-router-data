#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Emit the markdown body for the rolling `routing-databases-latest` GitHub
release, generated from routing-index.json so the region table never drifts
from what is actually published. Stdlib-only.

Usage:
    python3 scripts/routing_release_notes.py > notes.md
"""

import argparse
import json
import os
import sys


def _coverage(b: dict | None) -> str:
    if not b:
        return "—"
    ns = lambda v: f"{abs(v):.1f}°{'N' if v >= 0 else 'S'}"  # noqa: E731
    ew = lambda v: f"{abs(v):.1f}°{'E' if v >= 0 else 'W'}"  # noqa: E731
    return (f"{ns(b['min_lat'])}–{ns(b['max_lat'])}, "
            f"{ew(b['min_lon'])}–{ew(b['max_lon'])}")


def _size(n: int | None) -> str:
    return f"{n / 1048576:.1f} MB" if n else "—"


def _count(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "—"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="routing-index.json",
                        help="Path to routing-index.json (default: routing-index.json)")
    args = parser.parse_args()

    if not os.path.isfile(args.index):
        print(f"ERROR: {args.index} not found", file=sys.stderr)
        sys.exit(1)

    with open(args.index) as f:
        catalog = json.load(f)

    # Only release-hosted regions belong in the table — a region still served
    # from the repo has no asset here and listing it would advertise a
    # download that this release does not carry.
    regions = [r for r in catalog.get("regions", []) if r.get("download_url")]

    lines = [
        "Rolling data release: pre-compiled **nautical routing graph databases** "
        "(SQLite, gzip-compressed) built from ENC charts and open waterway data by the "
        "[signalk-router-pipeline](https://github.com/marcelrv/signalk-router-pipeline).",
        "",
        "| Region | Country | Download | Size | Coverage | Nodes | Edges | POIs | Updated |",
        "|--------|---------|----------|------|----------|-------|-------|------|---------|",
    ]
    for r in regions:
        stats = r.get("stats") or {}
        lines.append(
            f"| {r.get('name') or r['id']} | {r.get('country', '')} "
            f"| [{r['filename']}]({r['download_url']}) | {_size(r.get('size_bytes'))} "
            f"| {_coverage(r.get('bounding_box'))} | {_count(stats.get('nodes'))} "
            f"| {_count(stats.get('edges'))} | {_count(stats.get('pois'))} "
            f"| {(r.get('last_update') or '')[:10] or '—'} |"
        )

    if not regions:
        lines.append("| _(no regions published yet)_ | | | | | | | | |")

    lines += [
        "",
        "- Assets are **overwritten in place** on every deploy — the download URLs above are "
        "stable, and republishing a region adds nothing to this repository's git history.",
        "- Each file is a gzipped SQLite database; SHA-256 checksums and full metadata live in "
        "[`routing-index.json`](https://github.com/marcelrv/signalk-router-data/blob/main/routing-index.json).",
        "- Consumed automatically by the "
        "[SignalK RouteIQ](https://github.com/marcelrv/signalk-routeiq) plugin via its "
        "database-download UI.",
        "- Derived data — see [LICENSE-DATA.md](https://github.com/marcelrv/signalk-router-data/blob/main/LICENSE-DATA.md). "
        "**Not for primary navigation.**",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
