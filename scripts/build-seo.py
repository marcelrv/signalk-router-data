#!/usr/bin/env python3
"""
Deploy-time SEO enrichment for GitHub Pages (zero external dependencies).

Reads tide-current-index.json and routing-index.json from the assembled _site/
directory and:
  1. Injects rich schema.org/DataCatalog + Dataset JSON-LD (for Google Search
     and Google Dataset Search) into _site/index.html.
  2. Injects a clean <noscript> HTML index of all regions & formats so crawlers
     index every region name even without executing JavaScript.
  3. Generates _site/sitemap.xml.
"""

import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

BASE_URL = "https://marcelrv.github.io/signalk-router-data/"


def normalize_bbox(bbox: dict | None, geom: dict | None) -> dict | None:
    if not bbox:
        return None
    min_lon = bbox["min_lon"]
    max_lon = bbox["max_lon"]
    # Flip wide Pacific bboxes stored with min_lon < max_lon spanning > 180°
    # (e.g. -75..110 -> 110..-75 across the antimeridian)
    is_simple_rect = (
        geom
        and geom.get("type") == "Polygon"
        and len(geom.get("coordinates", [])) == 1
        and len(geom["coordinates"][0]) == 5
    )
    if is_simple_rect and 180 < (max_lon - min_lon) < 350:
        return {**bbox, "min_lon": max_lon, "max_lon": min_lon}
    return bbox


def main() -> None:
    site_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_site")
    index_path = site_dir / "index.html"
    tci = json.loads((site_dir / "tide-current-index.json").read_text("utf-8"))
    ri = json.loads((site_dir / "routing-index.json").read_text("utf-8"))

    datasets_ld = []
    noscript_rows = []

    for s in tci.get("sources", []):
        name = s.get("name", s.get("id", ""))
        desc = s.get("description", "")
        stype = s.get("type", "").upper()
        region = s.get("region", {})
        bbox = normalize_bbox(region.get("bounding_box"), region.get("boundary_geometry"))

        ds = {
            "@type": "Dataset",
            "name": name,
            "description": desc[:400],
            "encodingFormat": "application/octet-stream" if stype == "GRIB2" else "text/plain",
            "keywords": s.get("tags", []),
            "url": f"{BASE_URL}?type={quote(s.get('type', ''), safe='')}&q={quote(name, safe='')}",
        }
        if bbox:
            ds["spatialCoverage"] = {
                "@type": "Place",
                "name": region.get("name") or name,
                "geo": {
                    "@type": "GeoShape",
                    "box": f"{bbox['min_lat']} {bbox['min_lon']} {bbox['max_lat']} {bbox['max_lon']}",
                },
            }
        datasets_ld.append(ds)

        subregions = sorted({f.get("name") for f in s.get("files", []) if f.get("name")})
        sub_txt = f" ({', '.join(subregions)})" if subregions else ""
        noscript_rows.append(f"<li><strong>{html.escape(name)}</strong> [{html.escape(stype)}]{html.escape(sub_txt)}</li>")

    for r in ri.get("regions", []):
        name = r.get("name", r.get("id", ""))
        bbox = r.get("bounding_box")
        ds = {
            "@type": "Dataset",
            "name": name,
            "description": r.get("description", "")[:400],
            "encodingFormat": "application/x-sqlite3",
            "keywords": r.get("tags", []),
            "url": f"{BASE_URL}?catalog=routing",
        }
        if bbox:
            ds["spatialCoverage"] = {
                "@type": "Place",
                "name": name,
                "geo": {
                    "@type": "GeoShape",
                    "box": f"{bbox['min_lat']} {bbox['min_lon']} {bbox['max_lat']} {bbox['max_lon']}",
                },
            }
        datasets_ld.append(ds)
        noscript_rows.append(f"<li><strong>{html.escape(name)}</strong> [Routing DB — SignalK RouteIQ]</li>")

    catalog_ld = {
        "@context": "https://schema.org",
        "@type": "DataCatalog",
        "name": "Open Nautical Data",
        "description": (
            "Free catalog of GRIB2 surface current forecasts, predicted tidal stream tables "
            "(UTCEF & XTide harmonics), and nautical routing databases for OpenCPN, qtVlm, "
            "XyGrib, LuckGrib, and Signal K."
        ),
        "url": BASE_URL,
        "dataset": datasets_ld,
    }

    html_text = index_path.read_text("utf-8")

    # 1. Replace JSON-LD block (escape '<' so '</script>' or '<!--' cannot break HTML)
    safe_jsonld = json.dumps(catalog_ld, ensure_ascii=False).replace("<", "\\u003c")
    ld_script = (
        '<script type="application/ld+json" id="seo-jsonld">\n'
        + safe_jsonld
        + "\n  </script>"
    )
    html_text = re.sub(
        r'<script type="application/ld\+json" id="seo-jsonld">.*?</script>',
        lambda _: ld_script,
        html_text,
        flags=re.DOTALL,
    )

    # 2. Inject crawlable <noscript> dataset list
    noscript_block = (
        "<noscript>\n"
        "    <section style=\"padding:24px;max-width:900px;margin:0 auto;font-family:sans-serif\">\n"
        "      <h2>Open Nautical Data — Available Datasets</h2>\n"
        "      <p>Enable JavaScript for the interactive coverage map. Available GRIB2 forecasts, tidal streams, and routing databases:</p>\n"
        "      <ul>\n        " + "\n        ".join(noscript_rows) + "\n      </ul>\n"
        "    </section>\n"
        "  </noscript>"
    )
    html_text = html_text.replace("<!-- SEO_NOSCRIPT_PLACEHOLDER -->", noscript_block)
    index_path.write_text(html_text, "utf-8")

    # 3. Generate sitemap.xml
    lastmod = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = [
        BASE_URL,
        f"{BASE_URL}?type=grib2",
        f"{BASE_URL}?type=utcef",
        f"{BASE_URL}?catalog=routing",
    ]
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(
            f"  <url><loc>{html.escape(u)}</loc><lastmod>{lastmod}</lastmod><changefreq>daily</changefreq></url>\n"
            for u in urls
        )
        + "</urlset>\n"
    )
    (site_dir / "sitemap.xml").write_text(sitemap, "utf-8")
    print(f"SEO build complete: {len(datasets_ld)} datasets indexed in JSON-LD + sitemap.xml")


if __name__ == "__main__":
    main()
