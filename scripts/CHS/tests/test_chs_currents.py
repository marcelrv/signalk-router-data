# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline tests for the CHS converter, using recorded IWLS fixtures.

Run: pytest scripts/CHS/tests
"""
import json
import math
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.join(HERE, ".."))

import chs_currents_to_utcef as chs  # noqa: E402
from noaa_astro import predict_uv  # noqa: E402


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_select_current_stations_needs_speed_and_direction_predictions():
    picked = chs.select_current_stations(load("stations.json"))
    # 08108 has wcsp1+wcdp1; 09339 only observations (wcs1/wcd1); 00066 only
    # slack times; 04315 is a water-level station.
    assert [s["code"] for s in picked] == ["08108"]


def test_date_chunks_respect_the_7_day_limit_and_cover_the_window():
    start, end = utc(2026, 11, 17), utc(2027, 11, 17)
    chunks = chs.date_chunks(start, end)
    assert chunks[0][0] == start and chunks[-1][1] == end
    assert all(b - a <= timedelta(days=7) for a, b in chunks)
    assert all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1))
    assert len(chunks) == 53


def test_merge_series_joins_on_time_and_drops_duplicates():
    spd, drn = load("wcsp1_08108.json"), load("wcdp1_08108.json")
    # Overlapping chunk boundaries repeat the edge sample; duplicates must collapse.
    times, s, d = chs.merge_series(spd + spd[-1:], drn + drn[-1:])
    assert len(times) == len(spd) == 3 * 96 + 1
    assert np.all(np.diff(times) == 15 * 60000)
    assert s[0] == spd[0]["value"] and d[0] == drn[0]["value"]


def test_to_uv_converts_knots_and_set_direction():
    u, v = chs.to_uv(np.array([1.0, 2.0, 1.0]), np.array([0.0, 90.0, 180.0]))
    ms = 1852.0 / 3600.0
    np.testing.assert_allclose(u, [0.0, 2 * ms, 0.0], atol=1e-12)
    np.testing.assert_allclose(v, [ms, 0.0, -ms], atol=1e-12)
    # Seymour Narrows: flood sets 180°, so a southward current is + flood.
    assert chs.axis_speed_kn(np.array([0.0]), np.array([-ms]), 180.0)[0] == pytest.approx(1.0)


def test_fit_recovers_known_constituents():
    truth = {
        "M2": {"u_amplitude": 1.2, "u_phase_g": 40.0, "v_amplitude": 2.5, "v_phase_g": 220.0},
        "K1": {"u_amplitude": 0.3, "u_phase_g": 110.0, "v_amplitude": 0.6, "v_phase_g": 290.0},
        "M4": {"u_amplitude": 0.1, "u_phase_g": 15.0, "v_amplitude": 0.2, "v_phase_g": 195.0},
    }
    start = utc(2026, 11, 17)
    times = np.arange(0, 365 * 96) * 15 * 60000.0 + start.timestamp() * 1000.0
    u, v = predict_uv(truth, times)
    speed = np.hypot(u, v) / chs.KN_TO_MS
    direction = np.degrees(np.arctan2(u, v)) % 360.0
    station = {"code": "TEST", "id": "x", "latitude": 50.0, "longitude": -125.0, "officialName": "Test  "}
    feat = chs.build_feature(station, {"floodDirection": 180, "ebbDirection": 0}, times, speed, direction,
                             (start, start + timedelta(days=365)))
    hc = feat["properties"]["harmonic_constituents"]
    for name, c in truth.items():
        assert hc[name]["u_amplitude"] == pytest.approx(c["u_amplitude"], abs=1e-3)
        assert hc[name]["v_amplitude"] == pytest.approx(c["v_amplitude"], abs=1e-3)
        assert hc[name]["v_phase_g"] == pytest.approx(c["v_phase_g"], abs=0.1)
    assert feat["properties"]["chs"]["fit_rms_kn"] < 0.001
    assert feat["properties"]["station_name"] == "Test"
    assert feat["id"] == "CHS_TEST"


def test_recorded_seymour_narrows_fit_reproduces_the_series(tmp_path):
    station = chs.select_current_stations(load("stations.json"))[0]
    times, spd, drn = chs.merge_series(load("wcsp1_08108.json"), load("wcdp1_08108.json"))
    meta = load("metadata_08108.json")
    # Three days cannot separate the full constituent set, so fit only the
    # dominant ones; the in-sample series must still be reproduced closely.
    feat = chs.build_feature(station, meta, times, spd, drn, (utc(2026, 11, 17), utc(2026, 11, 20)),
                             names=["M2", "K1", "M4", "M6", "MK3"])
    u, v = predict_uv(feat["properties"]["harmonic_constituents"], times, feat["properties"]["mean_offset"])
    ou, ov = chs.to_uv(spd, drn)
    err = chs.axis_speed_kn(u, v, meta["floodDirection"]) - chs.axis_speed_kn(ou, ov, meta["floodDirection"])
    assert float(np.sqrt(np.mean(err ** 2))) < 0.10 * float(np.max(spd))

    path = chs.write_region(chs.region_for(50.13, -125.35), [feat], str(tmp_path))
    with zipfile.ZipFile(path) as z:
        payload = json.loads(z.read("chs_bc_coast.json"))
    md = payload["metadata"]
    assert md["catalog"]["source"] == "chs"
    assert md["notice"].startswith("This product is not to be used for navigation.")
    assert md["notice"] in md["description"]
    assert "non-commercial" in md["license"]
    assert payload["dataset"]["features"][0]["id"] == "CHS_08108"


def test_short_series_drop_constituents_that_need_a_year():
    assert "SA" in chs.fit_names_for(365) and "T2" in chs.fit_names_for(365)
    short = chs.fit_names_for(260)
    assert not chs.YEAR_ONLY & set(short)
    assert {"M2", "S2", "K1", "P1", "K2"} <= set(short)


def test_region_split():
    assert chs.region_for(50.1, -125.3)["id"] == "chs_bc_coast"
    assert chs.region_for(46.2, -63.7)["id"] == "chs_atlantic_canada"


def test_notice_is_clause_10_with_licensee():
    lines = chs.CHS_NOTICE.split("\n")
    assert len(lines) == 4
    assert lines[0] == "This product is not to be used for navigation."
    assert "[insert" not in chs.CHS_NOTICE and chs.LICENSEE in lines[1]
    assert not math.isnan(chs.KN_TO_MS)
