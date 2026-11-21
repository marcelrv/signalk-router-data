# DCSM-FM tidal streams (Wadden Sea, Ooster- and Westerschelde)

Dense tidal-stream UTCEF for the areas FES2014 cannot resolve (its 1/16° land
mask leaves 2 current points inside the Dutch Wadden). Source: Rijkswaterstaat's
operational DCSM-FM 100 m model via the open NOOS Matroos server, collected
daily by a separate collector (kept outside this repo) into hourly u/v on the
ZUNO v4 grid (~200 m Wadden, ~70–110 m Delta).

Output: `regions/europe/wadden_dcsm_hd.utcef` and `delta_dcsm_hd.utcef`
(catalog ids `utcef_wadden_dcsm_hd`, `utcef_delta_dcsm_hd`). **Test release**
(`TEST_RELEASE` in the generator: "(TEST)" in the title, `test` tag, caveats
in the description) until a 30+ day rebuild; keep the file names so the
catalog ids stay stable when the test flag is dropped.

| Script | What it does |
|---|---|
| `dcsm_to_utcef.py` | Harmonic fit of every model cell (one vectorised least-squares; UTide nodal corrections and Greenwich arguments), point placement, UTCEF writer |
| `validate_utcef.py` + `plugin_predict.mjs` | Runs the signalk-tidal-currents plugin's own `utcefSampleAt` on the output and compares with the raw model series |
| `astro_compare.py` | Compares UTCEF (through the plugin) with the tide-only DCSM-FM run (Matroos maps1d `dcsm_fm100m_astro`, no weather) at its ~270 named stations in the Wadden and Delta — the cleanest check of the fit |

```bash
.venv/bin/python scripts/DCSM-FM/dcsm_to_utcef.py --routing nl-routing.sqlite --out dist
.venv/bin/python scripts/DCSM-FM/validate_utcef.py dist/wadden_dcsm_hd.utcef --plot val.png
```

Needs `numpy scipy netCDF4 utide` (and `node` + a built plugin `dist/` for validation;
`matplotlib` for `--plot`).

## Point placement

The plugin answers a position with the **nearest** station, so a point set is
judged by the error every model cell gets from its nearest point. Seeds go
500 m apart along the routing graph's edges (where people sail); points are then
added greedily where the RMS tidal vector error to the nearest point, or the
distance to it, exceeds the cell's class tolerance:

| class | meaning | tol (m/s RMS, ×`--tol-scale`) | max distance |
|---|---|---|---|
| route | on a route, fast stream (M2 > 0.35 m/s) | 0.05 | 500 m |
| route_slow | on a route, slow water or over flats | 0.07 | 1000 m |
| channel | fast stream off the routes | 0.07 | 1000 m |
| slow | weak flow near the routes | 0.10 | 2000 m |
| flat | dries (u = v = 0 in > 3 % of samples) | 0.15 | 2000 m |
| open | > 2.5 km from any route | 0.08 | 4000 m |

In fast channels neighbouring 200 m cells differ by ~0.065 m/s RMS (median),
so tolerances below that turn every cell into a point. Default scale 2.5.

## Notes and pitfalls

- UTide's Python `FUV` takes proleptic-ordinal days (1970-01-01 = 719163), not
  MATLAB datenum (719529); the 366-day offset shifts M2/O1 phases by ~75°.
  `validate_utcef.py` catches this class of error.
- Dry cells are not masked in the product: velocity reads exactly 0. That is
  kept (a vessel on a dry flat has no current) and used to detect flats.
- Short records: constituents are chosen by the Rayleigh criterion plus
  minimum lengths; N2, K2 (and P1) are inferred from M2, S2, K1 with FES2014
  ratios until the record separates them (~28 days for N2, ~180 for K2).
- The record includes weather, yet the fitted mean flow (`mean_offset`, which
  the plugin adds to every prediction) is mostly tidal residual flow: zeroing
  it raised the RMS against the tide-only run (Delta p50 0.155 → 0.174 m/s,
  Wadden 0.177 → 0.181), so it is exported.
- The open NOOS Matroos keeps only ~14 days for every current source (maps and
  station series alike); longer history is on the login-only Matroos. The
  collector therefore also stores the tide-only station run (`astro1d/`).
- Nov 2026, 15-day record, against the tide-only run: set within 1–2° (median),
  RMS 0.16–0.18 m/s, but the spring-neap swing is exaggerated (daily speed
  ratio 0.68–1.07): S2 is poorly separated from M2 at 15 days. Inferring S2
  from FES flattened the swing but raised the RMS (FES S2 phase is off
  locally), so S2 stays fitted; rebuild once the record passes ~29 days.
- Against the BSH North Sea GRIB (surface 1 m, ~5.5 km): raw DCSM vector
  correlation 0.94 (Wadden) / 0.98 (Delta), speeds ~0.8× BSH (surface vs
  depth-averaged), direction within ~10°.
- Licence: Rijkswaterstaat publishes its data as open data under CC0 1.0
  ("open, tenzij"); the derived UTCEF files are CC-BY-4.0 with attribution to
  Rijkswaterstaat / Deltares DCSM-FM and a not-for-navigation disclaimer
  (`metadata.license`, `copyright`, `citation_required`, `data_sources`).
