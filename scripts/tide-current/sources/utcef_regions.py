#!/usr/bin/env python3
"""Collector: discover UTCEF region files and yield one source entry per file."""
import glob
import hashlib
import json
import os
import sys
import zipfile


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(65536)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def read_metadata(utcef_path: str) -> dict:
    with zipfile.ZipFile(utcef_path) as z:
        inner = [n for n in z.namelist() if n.endswith(".json")][0]
        with z.open(inner) as fh:
            return json.loads(fh.read())["metadata"]


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    pattern = os.path.join(repo_root, "regions", "**/*.utcef")
    files = sorted(glob.glob(pattern, recursive=True))

    sources = []
    for path in files:
        meta = read_metadata(path)
        bbox = meta["region"]["bbox"]
        rel = os.path.relpath(path, repo_root)
        sha = sha256_file(path)
        size = os.path.getsize(path)
        region_id = os.path.splitext(os.path.basename(path))[0]

        sources.append({
            "id": f"utcef_{region_id}",
            "source": "fes2014",
            "type": "utcef",
            "name": meta.get("title", region_id),
            "description": meta.get("description", ""),
            "contributor": meta.get("copyright", "CNES/CLS"),
            "url": "https://www.aviso.altimetry.fr/en/data/products/auxiliary-products/global-tide-fes.html",
            "tags": ["utcef", "tidal", "currents", "fes2014", "regional"],
            "region": {
                "name": meta.get("title", region_id),
                "bounding_box": {
                    "min_lat": bbox[1],
                    "min_lon": bbox[0],
                    "max_lat": bbox[3],
                    "max_lon": bbox[2],
                },
                "boundary_geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [bbox[0], bbox[1]],
                        [bbox[2], bbox[1]],
                        [bbox[2], bbox[3]],
                        [bbox[0], bbox[3]],
                        [bbox[0], bbox[1]],
                    ]],
                },
            },
            "update_check": {
                "method": "sha256",
                "last_checked": "",
            },
            "files": [{
                "filename": rel,
                "sha256": sha,
                "size_bytes": size,
            }],
        })

    json.dump(sources, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
