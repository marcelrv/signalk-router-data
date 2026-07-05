#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""
Source collector: OpenCPN XTide HARMONICS_NO_US harmonic constituents.

Downloads the HARMONICS_NO_US pair from OpenCPN's GitHub (when ETag
changes), computes SHA-256, outputs a source entry for the master index.

Standalone:  python3 opencpn_harmonics.py  →  JSON to stdout
"""

import hashlib
import json
import os
import sys
import tempfile
import urllib.request
import urllib.error

OPENCPN_FILES = ["HARMONICS_NO_US", "HARMONICS_NO_US.IDX"]
OPENCPN_BASE = "https://raw.githubusercontent.com/OpenCPN/OpenCPN/master/data/tcdata"

# ── coverage geometry ────────────────────────────────────────────────
# HARMONICS_NO_US contains non-US harmonic stations globally.  A simple
# world boundary is used since the stations span every ocean basin.
BOUNDARY_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [-180, -90],
        [180, -90],
        [180, 90],
        [-180, 90],
        [-180, -90],
    ]],
}

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def download(url: str) -> bytes:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main():
    tmpdir = tempfile.mkdtemp(prefix="opencpn_")

    file_entries = []
    for name in OPENCPN_FILES:
        url = f"{OPENCPN_BASE}/{name}"
        local = os.path.join(tmpdir, name)

        data = download(url)
        with open(local, "wb") as f:
            f.write(data)
        sha = sha256_file(local)
        size = os.path.getsize(local)

        file_entries.append({
            "filename": name,
            "url": url,
            "sha256": sha,
            "size_bytes": size,
        })

    for f in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, f))
    os.rmdir(tmpdir)

    source = {
        "id": "opencpn_xtide_harmonics",
        "source": "opencpn",
        "type": "harmonic",
        "name": "OpenCPN XTide Harmonics",
        "description": "Global tidal harmonic constituents for current stations from OpenCPN/XTide (excludes US mainland)",
        "contributor": "OpenCPN",
        "url": "https://github.com/OpenCPN/OpenCPN/tree/master/data/tcdata",
        "tags": [
            "harmonic",
            "global",
            "station-based",
            "community",
            "opencpn",
            "xtide",
            "static",
        ],
        "region": {
            "name": "Global (non-US stations)",
            "bounding_box": {
                "min_lat": -90,
                "min_lon": -180,
                "max_lat": 90,
                "max_lon": 180,
            },
            "boundary_geometry": BOUNDARY_GEOMETRY,
        },
        "update_check": {
            "method": "sha256",
            "last_checked": "",
        },
        "files": file_entries,
    }

    json.dump(source, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
