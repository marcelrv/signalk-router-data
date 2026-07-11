# Copyright (C) 2026 Marcel Verpaalen
# SPDX-License-Identifier: GPL-3.0-or-later
# Licensed under the GNU General Public License v3.0 or later.
# See the LICENSE file in the repository root, or <https://www.gnu.org/licenses/>.
"""Tidal astronomy shared by the NOAA UTCEF generator and validation harness.

Python port of the consuming engine's astronomy (signalk-tidal-currents
src/astro.ts — keep the constituent table and nodal series in sync!): IHO /
Schureman catalog, Greenwich equilibrium argument V0 and nodal corrections
(f, u) as cosine series in the lunar node N.

All functions accept scalar times or 1-D numpy arrays of epoch-milliseconds
and broadcast accordingly.
"""
import numpy as np

J2000_MS = 946728000000.0   # 2000-01-01 12:00 UTC
EPOCH_MS = 946684800000.0   # 2000-01-01 00:00 UTC

#                 speed        T15  s   h   p  p1 const nodal
CONSTITUENTS = {
    "M2":      (28.9841042, 2, -2,  2,  0,  0,   0, "M2"),
    "S2":      (30.0000000, 2,  0,  0,  0,  0,   0, "none"),
    "N2":      (28.4397295, 2, -3,  2,  1,  0,   0, "M2"),
    "K2":      (30.0821373, 2,  0,  2,  0,  0,   0, "K2"),
    "NU2":     (28.5125831, 2, -3,  4, -1,  0,   0, "M2"),
    "MU2":     (27.9682084, 2, -4,  4,  0,  0,   0, "M2"),
    "2N2":     (27.8953548, 2, -4,  2,  2,  0,   0, "M2"),
    "L2":      (29.5284789, 2, -1,  2, -1,  0, 180, "M2"),
    "T2":      (29.9589333, 2,  0, -1,  0,  1,   0, "none"),
    "R2":      (30.0410667, 2,  0,  1,  0, -1, 180, "none"),
    "LAMBDA2": (29.4556253, 2, -1,  0,  1,  0, 180, "M2"),
    "2SM2":    (31.0158958, 2,  2, -2,  0,  0,   0, "M2inv"),
    "K1":      (15.0410686, 1,  0,  1,  0,  0,  90, "K1"),
    "O1":      (13.9430356, 1, -2,  1,  0,  0, -90, "O1"),
    "P1":      (14.9589314, 1,  0, -1,  0,  0, -90, "none"),
    "Q1":      (13.3986609, 1, -3,  1,  1,  0, -90, "O1"),
    "S1":      (15.0000000, 1,  0,  0,  0,  0,   0, "none"),
    "J1":      (15.5854433, 1,  1,  1, -1,  0,  90, "J1"),
    "2Q1":     (12.8542862, 1, -4,  1,  2,  0, -90, "O1"),
    "RHO1":    (13.4715145, 1, -3,  3, -1,  0, -90, "O1"),
    "MM":      (0.5443747,  0,  1,  0, -1,  0,   0, "Mm"),
    "MF":      (1.0980331,  0,  2,  0,  0,  0,   0, "Mf"),
    "SSA":     (0.0821373,  0,  0,  2,  0,  0,   0, "none"),
    "SA":      (0.0410686,  0,  0,  1,  0,  0,   0, "none"),
    "MSF":     (1.0158958,  0,  2, -2,  0,  0,   0, "M2inv"),
    "M3":      (43.4761563, 3, -3,  3,  0,  0,   0, "M2^2"),
    "MK3":     (44.0251729, 3, -2,  3,  0,  0,  90, "MK3"),
    "2MK3":    (42.9271398, 3, -4,  3,  0,  0, -90, "2MK3"),
    "M4":      (57.9682084, 4, -4,  4,  0,  0,   0, "M2^2"),
    "MS4":     (58.9841042, 4, -2,  2,  0,  0,   0, "M2"),
    "MN4":     (57.4238337, 4, -5,  4,  1,  0,   0, "M2^2"),
    "S4":      (60.0000000, 4,  0,  0,  0,  0,   0, "none"),
    "M6":      (86.9523127, 6, -6,  6,  0,  0,   0, "M2^2"),
    "S6":      (90.0000000, 6,  0,  0,  0,  0,   0, "none"),
    "M8":      (115.9364166, 8, -8, 8,  0,  0,   0, "M2^2"),
    # M1 and OO1 stay unsupported (need full Schureman I/ξ/ν nodal theory).
}


def astronomical_args(time_ms):
    """Fundamental mean longitudes (deg) + rotation term at UTC epoch-ms."""
    t = np.asarray(time_ms, dtype=np.float64)
    T = (t - J2000_MS) / 86400000.0 / 36525.0
    ut_hours = (t - EPOCH_MS) / 3600000.0
    return {
        "T15": 15.0 * ut_hours,
        "s": 218.3164477 + 481267.88123421 * T - 0.0015786 * T * T + T ** 3 / 538841.0,
        "h": 280.4664567 + 36000.76982779 * T + 0.0003032028 * T * T,
        "p": 83.3532465 + 4069.0137287 * T - 0.0103200 * T * T,
        "N": 125.0445479 - 1934.1362891 * T + 0.0020754 * T * T,
        "p1": 282.9373 + 1.7195366 * T + 0.0004597 * T * T,
    }


def equilibrium_arg(a, name):
    """Greenwich equilibrium argument V0 (deg) for a constituent."""
    _, t15, s, h, p, p1, konst, _ = CONSTITUENTS[name]
    v = t15 * a["T15"] + s * a["s"] + h * a["h"] + p * a["p"] + p1 * a["p1"] + konst
    return np.mod(v, 360.0)


def node_factors(a, name):
    """Node factor f (amplitude scale) and u (phase correction, deg)."""
    family = CONSTITUENTS[name][7]
    N = np.radians(a["N"])
    cN, c2N, c3N = np.cos(N), np.cos(2 * N), np.cos(3 * N)
    sN, s2N, s3N = np.sin(N), np.sin(2 * N), np.sin(3 * N)
    one = np.ones_like(N)
    if family == "none":
        return one, 0.0 * N
    if family == "Mm":
        return 1.0 - 0.1300 * cN + 0.0013 * c2N, 0.0 * N
    if family == "Mf":
        return 1.0429 + 0.4135 * cN - 0.0040 * c2N, -23.74 * sN + 2.68 * s2N - 0.38 * s3N
    if family == "O1":
        return (1.0089 + 0.1871 * cN - 0.0147 * c2N + 0.0014 * c3N,
                10.80 * sN - 1.34 * s2N + 0.19 * s3N)
    if family == "K1":
        return (1.0060 + 0.1150 * cN - 0.0088 * c2N + 0.0006 * c3N,
                -8.86 * sN + 0.68 * s2N - 0.07 * s3N)
    if family == "J1":
        return (1.0129 + 0.1676 * cN - 0.0170 * c2N + 0.0016 * c3N,
                -12.94 * sN + 1.34 * s2N - 0.19 * s3N)
    if family == "M2":
        return 1.0004 - 0.0373 * cN + 0.0002 * c2N, -2.14 * sN
    if family == "K2":
        return (1.0241 + 0.2863 * cN + 0.0083 * c2N - 0.0015 * c3N,
                -17.74 * sN + 0.68 * s2N - 0.04 * s3N)
    if family == "M2^2":
        f_m2 = 1.0004 - 0.0373 * cN + 0.0002 * c2N
        u_m2 = -2.14 * sN
        order = CONSTITUENTS[name][1] / 2.0
        return f_m2 ** order, order * u_m2
    if family == "MK3":
        f_m2, u_m2 = 1.0004 - 0.0373 * cN + 0.0002 * c2N, -2.14 * sN
        f_k1 = 1.0060 + 0.1150 * cN - 0.0088 * c2N + 0.0006 * c3N
        u_k1 = -8.86 * sN + 0.68 * s2N - 0.07 * s3N
        return f_m2 * f_k1, u_m2 + u_k1
    if family == "2MK3":
        f_m2, u_m2 = 1.0004 - 0.0373 * cN + 0.0002 * c2N, -2.14 * sN
        f_k1 = 1.0060 + 0.1150 * cN - 0.0088 * c2N + 0.0006 * c3N
        u_k1 = -8.86 * sN + 0.68 * s2N - 0.07 * s3N
        return f_m2 * f_m2 * f_k1, 2 * u_m2 - u_k1
    if family == "M2inv":
        return 1.0004 - 0.0373 * cN + 0.0002 * c2N, 2.14 * sN
    raise ValueError(family)


def predict_uv(constituents: dict, time_ms, mean=None):
    """(u, v) in m/s from UTCEF harmonic_constituents at UTC epoch-ms.

    `constituents` is the UTCEF dict {name: {u_amplitude, u_phase_g, …}};
    unknown names are skipped (mirrors the plugin engine).
    """
    a = astronomical_args(time_ms)
    u = np.zeros_like(np.asarray(time_ms, dtype=np.float64)) + float((mean or {}).get("u_residual") or 0.0)
    v = np.zeros_like(u) + float((mean or {}).get("v_residual") or 0.0)
    for name, c in constituents.items():
        if name not in CONSTITUENTS:
            continue
        f, un = node_factors(a, name)
        v0 = equilibrium_arg(a, name)
        u = u + f * c["u_amplitude"] * np.cos(np.radians(v0 + un - c["u_phase_g"]))
        v = v + f * c["v_amplitude"] * np.cos(np.radians(v0 + un - c["v_phase_g"]))
    return u, v
