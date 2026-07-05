#!/usr/bin/env python3
"""
One-time utility: parse HARMONICS_NO_US.IDX to extract all station coordinates,
compute a convex hull (buffered slightly), and print the GeoJSON boundary polygon.

Usage:
    python3 _tools/extract_harmonics_boundary.py HARMONICS_NO_US.IDX

Output (stdout): a GeoJSON Polygon suitable for embedding in opencpn_harmonics.py
"""
import re
import sys
import json
from shapely.geometry import MultiPoint
from shapely import wkt

STATION_RE = re.compile(r'^[TtCc]\S+\s+([\d.-]+)\s+([\d.-]+)\s+\d+:\d+\s+')

def main():
    if len(sys.argv) < 2:
        print("Usage: extract_harmonics_boundary.py <HARMONICS_NO_US.IDX>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    coords = []
    with open(path) as f:
        for line in f:
            m = STATION_RE.match(line)
            if m:
                lon, lat = float(m.group(1)), float(m.group(2))
                if -180 <= lon <= 180 and -90 <= lat <= 90:
                    coords.append((lon, lat))

    print(f"Parsed {len(coords)} station coordinates", file=sys.stderr)

    if not coords:
        print("No station coordinates found", file=sys.stderr)
        sys.exit(1)

    points = MultiPoint(coords)
    hull = points.convex_hull

    hull_buffered = hull.buffer(2.0, resolution=32)

    if hull_buffered.geom_type == "MultiPolygon":
        merged = hull_buffered
    elif hull_buffered.geom_type == "Polygon":
        from shapely.geometry import MultiPolygon
        merged = MultiPolygon([hull_buffered])
    else:
        merged = hull_buffered

    simplified = merged.simplify(0.5, preserve_topology=True)

    geojson = json.loads(json.dumps(simplified.__geo_interface__))

    print(json.dumps(geojson, indent=2))

if __name__ == "__main__":
    main()
