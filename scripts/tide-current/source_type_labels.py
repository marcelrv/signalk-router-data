#!/usr/bin/env python3
# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
"""
Shared end-user-facing style (label + color + opacity) for each catalog
`type` value.

Single source of truth for render_coverage_map.py (the static PNG) and
generate_coverage_geojson.py (the interactive GitHub-rendered map) so the
two never drift apart — same colors and translucency in both, not just
the same wording. Labels are deliberately NOT the internal source format
name ("utcef", "grib2") — a sailor looking at either map has no reason to
know what those acronyms mean; what matters to them is whether a region
is a live weather-aware forecast or a static astronomical prediction. See
specs/tide-current-catalog.md for what `type` actually means in the
catalog schema.

alpha/draw_order exist for the same reason in both consumers: the
static-prediction types (harmonic, utcef) cover huge, heavily-overlapping
areas, so they need to be quite translucent individually or many stacked
layers read as solid and bury the far sparser, higher-value forecast
layers underneath — draw_order makes sure forecast layers paint on top
even when a renderer doesn't otherwise respect z-order (GitHub's geojson
viewer doesn't expose a z-order control, so it relies on the GeoJSON
FeatureCollection's array order instead — see order_for_geojson below).
"""

TYPE_STYLES = {
    "harmonic": {
        "label": "Tide station predictions (astronomical)",
        "color": "#3b8fd4", "alpha": 0.15, "draw_order": 0,
    },
    "harmonic_constituents": {
        "label": "Tide station predictions (astronomical)",
        "color": "#3b8fd4", "alpha": 0.15, "draw_order": 0,
    },
    "utcef": {
        "label": "Predicted currents (astronomical, no weather)",
        "color": "#f59e0b", "alpha": 0.10, "draw_order": 0,
    },
    "station": {
        "label": "Station data",
        "color": "#8b5cf6", "alpha": 0.30, "draw_order": 1,
    },
    "grib2": {
        "label": "Forecast currents (tide + weather)",
        "color": "#22c55e", "alpha": 0.45, "draw_order": 2,
    },
    "forecast": {
        "label": "Forecast currents (tide + weather)",
        "color": "#22c55e", "alpha": 0.45, "draw_order": 2,
    },
}

DEFAULT_STYLE = {"label": "Other", "color": "#64748b", "alpha": 0.30, "draw_order": 1}


def style_for(source_type: str) -> dict:
    return TYPE_STYLES.get(source_type, DEFAULT_STYLE)


def label_for(source_type: str) -> str:
    return style_for(source_type)["label"]
