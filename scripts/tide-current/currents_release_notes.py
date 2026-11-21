#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Emit the markdown body for the rolling `<source>-currents-latest` GitHub
releases of the CMEMS (IBI, MED, NWS) and ECCC CIOPS surface-current
pipelines, and of the Rijkswaterstaat DCSM-FM one, generated from each
source module's REGIONS so the region table never drifts from what the
pipeline actually produces. Stdlib-only.
(The NOAA OFS release has its own generator: ofs_release_notes.py.)

Usage:
    python3 currents_release_notes.py {ibi|med|nws|ciops|dcsm} > notes.md
"""

import importlib
import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)

_COMMON_FOOTER = [
    "- Assets are **overwritten in place** on every run — the download URLs above are stable.",
    "- Consumed automatically by the "
    "[signalk-tidal-currents](https://github.com/marcelrv/signalk-tidal-currents) plugin "
    "via `tide-current-index.json`.",
]


def _cmems_attribution(doi: str, endorsers: str, issue: int) -> list:
    return [
        f"- Generated using E.U. Copernicus Marine Service Information "
        f"(<https://doi.org/{doi}>). These repackaged files are **not an official "
        f"Copernicus product** and are not endorsed by {endorsers} — "
        f"do not use for primary navigation.",
        f"- Background: [#{issue}](https://github.com/marcelrv/signalk-router-data/issues/{issue}).",
    ]


# Per-release text. `module` is the sources/ module whose REGIONS drive the table.
SOURCES = {
    "ibi": {
        "module": "cmems_ibi",
        "intro": (
            "Rolling data release: gridded **surface current forecasts for the Bay of Biscay, "
            "Iberian Peninsula and Gibraltar Strait** (tide + weather + river forcing), "
            "regenerated daily from the "
            "[Copernicus Marine Service IBI analysis-and-forecast model]"
            "(https://data.marine.copernicus.eu/product/IBI_ANALYSISFORECAST_PHY_005_001/description) "
            "(dataset `cmems_mod_ibi_phy_anfc_0.027deg-2D_PT1H-m`) — surface u/v only, "
            "subset at native resolution and re-encoded as compact GRIB2."
        ),
        "updated": "daily ~15:30 UTC",
        "footer": _cmems_attribution("10.48670/moi-00027", "Mercator Ocean International", 3),
    },
    "med": {
        "module": "cmems_med",
        "intro": (
            "Rolling data release: gridded **surface current forecasts for the Mediterranean "
            "Sea** (tide + weather forcing), regenerated daily from the "
            "[Copernicus Marine Service MED analysis-and-forecast model]"
            "(https://data.marine.copernicus.eu/product/MEDSEA_ANALYSISFORECAST_PHY_006_013/description) "
            "(dataset `cmems_mod_med_phy-cur_anfc_4.2km-2D_PT1H-m`) — surface u/v only, "
            "subset at native resolution and re-encoded as compact GRIB2."
        ),
        "updated": "daily ~17:30 UTC",
        "footer": _cmems_attribution("10.48670/mds-00359", "CMCC or Mercator Ocean International", 7),
    },
    "nws": {
        "module": "cmems_nws",
        "intro": (
            "Rolling data release: gridded **surface current forecasts for the UK, Ireland and "
            "English Channel** (tide + weather + river forcing), regenerated daily from the "
            "[Copernicus Marine Service NWS analysis-and-forecast model]"
            "(https://data.marine.copernicus.eu/product/NWSHELF_ANALYSISFORECAST_PHY_004_013/description) "
            "(dataset `cmems_mod_nws_phy-cur_anfc_1.5km-2D_PT1H-i`) — surface u/v only, "
            "subset at native resolution and re-encoded as compact GRIB2."
        ),
        "updated": "daily ~13:30 UTC",
        "footer": _cmems_attribution("10.48670/moi-00054", "the Met Office or Mercator Ocean International", 3),
    },
    "ciops": {
        "module": "eccc_ciops",
        "intro": (
            "Rolling data release: gridded **surface current forecasts for British Columbia and "
            "Atlantic Canada** (tide + weather + river forcing), regenerated four times daily from "
            "Environment and Climate Change Canada's "
            "[Coastal Ice Ocean Prediction System (CIOPS)]"
            "(https://eccc-msc.github.io/open-data/msc-data/nwp_ciops/readme_ciops_en/) "
            "(CIOPS-East, CIOPS-West and CIOPS-Salish Sea, MSC Datamart) — surface u/v only, "
            "geographically subset (optionally block-pooled) and re-encoded from NetCDF as "
            "compact GRIB2."
        ),
        "updated": "4× daily (01/07/13/19 UTC, ~7.5 h after each 00/06/12/18Z run)",
        "footer": [
            "- **Model currents in narrow passes and rapids are much weaker than reality** — "
            "use the CHS station predictions there.",
            "- Data Source: Environment and Climate Change Canada. Contains information licenced "
            "under the [Data Server End-use Licence of Environment and Climate Change Canada]"
            "(https://eccc-msc.github.io/open-data/licence/readme_en/). These repackaged files are "
            "**not an official ECCC product** and are not endorsed by ECCC — not for navigation.",
        ],
    },
    "dcsm": {
        "module": "rws_dcsm",
        "intro": (
            "Rolling data release: high-resolution **forecast currents for the Dutch "
            "Wadden Sea and the Delta** (tide + wind + surge, depth-averaged), regenerated from "
            "the 00Z and 12Z runs of Rijkswaterstaat's operational DCSM-FM 100 m model as "
            "published on the open [NOOS Matroos](https://noos.matroos.rws.nl/) server "
            "(source `dcsm7_harmonie_bf_f2w_zuno`) — u/v regridded from the curvilinear model "
            "grid to a regular lat/lon grid and re-encoded as compact GRIB2."
        ),
        "updated": "twice daily (~06:30 and ~18:30 UTC, from the 00Z and 12Z runs)",
        "footer": [
            "- Depth-averaged currents: expect speeds ~20% below surface-current models "
            "such as BSH. Dry tidal flats read 0.",
            "- Source: Rijkswaterstaat / Deltares DCSM-FM (NOOS Matroos), "
            "[open data](https://www.rijkswaterstaat.nl/zakelijk/open-data) (CC0 1.0). These "
            "files are [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/): **\"Source: "
            "Rijkswaterstaat / Deltares DCSM-FM (NOOS Matroos); processed by signalk-router-data\"**. "
            "**Not an official Rijkswaterstaat product** and not endorsed by Rijkswaterstaat or "
            "Deltares — not for official navigation.",
        ],
    },
}


def _deg(v: float) -> str:
    # One decimal, or two when one would round the bound (e.g. 4.55, 52.05).
    a = abs(v)
    return f"{a:.1f}" if abs(round(a, 1) - a) < 1e-9 else f"{a:.2f}"


def _lat(v: float) -> str:
    return f"{_deg(v)}°{'N' if v >= 0 else 'S'}"


def _lon(v: float) -> str:
    return f"{_deg(v)}°{'E' if v >= 0 else 'W'}"


def _span(lo: float, hi: float, fmt) -> str:
    a, b = fmt(lo), fmt(hi)
    # Same hemisphere → "41.5–43.7°N"; straddling → "1.3°W–4.5°E".
    return f"{a[:-2]}–{b}" if a[-1] == b[-1] else f"{a}–{b}"


def _coverage(b: dict) -> str:
    return f"{_span(b['min_lat'], b['max_lat'], _lat)}, {_span(b['min_lon'], b['max_lon'], _lon)}"


def _km(deg: float) -> str:
    km = deg * 111
    return f"≈{km:.0f} km" if km >= 10 else f"≈{km:.1f} km"


def _resolution(mod, r: dict) -> str:
    if "target_res_deg" in r:
        d = r["target_res_deg"]
        return f"{d:.3g}° ({_km(d)})"
    if "target_res_lat_deg" in r:
        # Non-square grid (NWS, DCSM): quote both steps, distance for the N-S one.
        km = r["target_res_lat_deg"] * 111
        dist = f"≈{km:.1f} km" if km >= 1 else f"≈{km * 1000:.0f} m"
        return (f"{r['target_res_lat_deg']:.3g}° lat × {r['target_res_lon_deg']:.3g}° lon "
                f"({dist})")
    # CIOPS: native resolution from the Datamart domain path, times pooling.
    native = mod.DOMAINS[r["domain"]]["path"].split("/")[-1]  # "2km" / "500m"
    meters = float(native[:-2]) * 1000 if native.endswith("km") else float(native[:-1])
    eff = meters * r.get("pool_step", 1)
    txt = f"{eff / 1000:g} km" if eff >= 1000 else f"{eff:g} m"
    return f"{txt} ({r['pool_step']}×{r['pool_step']} pooled)" if r.get("pool_step", 1) > 1 else txt


def _forecast(r: dict) -> str:
    # max_forecast_hours (DCSM): the run length varies, forecast_hours is the
    # guaranteed minimum.
    hi = r.get("max_forecast_hours")
    if hi and hi > r["forecast_hours"]:
        return f"+{r['forecast_hours']} h (up to +{hi} h), hourly"
    return f"+{r['forecast_hours']} h, hourly"


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in SOURCES:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(SOURCES)}}}")
    cfg = SOURCES[sys.argv[1]]
    mod = importlib.import_module(cfg["module"])
    has_domain = any("domain" in r for r in mod.REGIONS.values())

    head = ["Region", "Download", "Coverage", "Resolution", "Forecast"]
    if has_domain:
        head.insert(1, "Model")
    lines = [
        cfg["intro"],
        "",
        f"Updated {cfg['updated']}.",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "|".join("-" * (len(h) + 2) for h in head) + "|",
    ]
    for rid, r in mod.REGIONS.items():
        fname = f"{rid}_currents.grb2"
        row = [
            r["name"],
            f"[{fname}]({mod.GITHUB_RELEASE_BASE}/{fname})",
            _coverage(r["bounds"]),
            _resolution(mod, r),
            _forecast(r),
        ]
        if has_domain:
            row.insert(1, mod.DOMAINS[r["domain"]]["token"])
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", *_COMMON_FOOTER, *cfg["footer"]]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
