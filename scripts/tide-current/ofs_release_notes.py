#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Emit the markdown body for the rolling `ofs-currents-latest` GitHub
release, generated from sources/nos_ofs.py's MODELS so the region table
never drifts from what the pipeline actually produces. Stdlib-only.

Usage:
    python3 ofs_release_notes.py > notes.md
"""

import os
import sys

_SOURCES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sources")
if _SOURCES_DIR not in sys.path:
    sys.path.insert(0, _SOURCES_DIR)
import nos_ofs  # noqa: E402


def _coverage(b: dict) -> str:
    return (f"{b['min_lat']:.1f}–{b['max_lat']:.1f}°N, "
            f"{abs(b['min_lon']):.1f}–{abs(b['max_lon']):.1f}°W")


def _resolution(res_deg: float) -> str:
    return f"{res_deg:g}° (≈{res_deg * 111:.0f} km)" if res_deg * 111 >= 10 \
        else f"{res_deg:g}° (≈{res_deg * 111:.1f} km)"


def _forecast(m: dict) -> str:
    step = m["forecast_step_hours"]
    cadence = "hourly" if step == 1 else f"{step}-hourly"
    return f"+{m['forecast_hours']} h, {cadence}"


def _cycles(m: dict) -> str:
    return "/".join(m["cycles"]) + "Z"


def main() -> None:
    lines = [
        "Rolling data release: gridded **surface current forecasts for US waters** "
        "(tide + weather + river forcing), regenerated four times daily from "
        "[NOAA NOS Operational Forecast System (OFS)](https://tidesandcurrents.noaa.gov/ofs/) "
        "model guidance — surface u/v only, block-pooled to a per-region resolution and "
        "re-encoded as compact GRIB2.",
        "",
        "| Region | Model | Download | Coverage | Resolution | Forecast | NOAA cycles |",
        "|--------|-------|----------|----------|------------|----------|-------------|",
    ]
    for mid, m in nos_ofs.MODELS.items():
        fname = f"{mid}_currents.grb2"
        url = f"{nos_ofs.GITHUB_RELEASE_BASE}/{fname}"
        region = m["name"].replace(" Operational Forecast System", "")
        lines.append(
            f"| {region} | {mid.upper()} | [{fname}]({url}) | {_coverage(m['bounds'])} "
            f"| {_resolution(m['target_res_deg'])} | {_forecast(m)} | {_cycles(m)} |"
        )
    lines += [
        "",
        "- Assets are **overwritten in place** on every run — the download URLs above are stable.",
        "- Consumed automatically by the "
        "[signalk-tidal-currents](https://github.com/marcelrv/signalk-tidal-currents) plugin "
        "via `tide-current-index.json`; see "
        "[#4](https://github.com/marcelrv/signalk-router-data/issues/4) for background.",
        "- NOAA data is public domain (U.S. Government work, 17 U.S.C. §105). These repackaged "
        "files are **not an official NOAA product** and are not endorsed by NOAA — "
        "do not use for primary navigation.",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
