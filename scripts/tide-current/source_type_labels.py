#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Shared end-user-facing labels for each catalog `type` value.

Single source of truth for render_coverage_map.py (the static PNG) and
generate_coverage_geojson.py (the interactive GitHub-rendered map) so the
two never drift apart. Labels are deliberately NOT the internal source
format name ("utcef", "grib2") — a sailor looking at either map has no
reason to know what those acronyms mean; what matters to them is whether a
region is a live weather-aware forecast or a static astronomical
prediction. See specs/tide-current-catalog.md for what `type` actually
means in the catalog schema.
"""

TYPE_LABELS = {
    "harmonic": "Tide station predictions (astronomical)",
    "harmonic_constituents": "Tide station predictions (astronomical)",
    "utcef": "Predicted currents (astronomical, no weather)",
    "station": "Station data",
    "grib2": "Forecast currents (tide + weather)",
    "forecast": "Forecast currents (tide + weather)",
}

DEFAULT_LABEL = "Other"


def label_for(source_type: str) -> str:
    return TYPE_LABELS.get(source_type, DEFAULT_LABEL)
