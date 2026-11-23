# NorKyst v3 tidal streams (Norwegian coast)

Dense tidal-stream UTCEF for the Norwegian coast, Skagerrak/Oslofjord to Finnmark,
where the FES-based `atn_scandinavia.utcef` is far too coarse for fjords and sounds.
Source: MET Norway's NorKyst v3 800 m ROMS model, best-estimate aggregation
`fou-hi/norkystv3_800m_m00_be` on thredds.met.no (hourly from 2024-01-01, surface u/v).
Point density follows leisure traffic from EMODnet vessel density (Sailing + Pleasure Craft).

Output: `regions/europe/norkyst_norway_hd.utcef` (catalog id `utcef_norkyst_norway_hd`),
one file for the four NorKyst GRIB regions (south, west, nordland, north), ~24.7k points,
~5.9 MB. **Test release** (`TEST_RELEASE` in the generator).

| Script | What it does |
|---|---|
| `fetch_record.py` | Resumable OPeNDAP download of surface u/v in 64×64 tiles (wet cells within 15 km of land in the region boxes): `grid`, `fetch [--validation]`, `bench`, `--dry-run` |
| `ais_density.py` | EMODnet vessel density (types 04 Sailing + 05 Pleasure, annual averages 2017–2023) sampled onto the NorKyst grid → `density.npz` |
| `norkyst_to_utcef.py` | Harmonic fit of every cell, point placement, UTCEF writer; reuses the fit and placement code of `../DCSM-FM/dcsm_to_utcef.py` |
| `validate_norkyst.py` | Runs the plugin's own prediction (`../DCSM-FM/plugin_predict.mjs`) against the raw model on the fit or held-out window |
| `AIS.md` | AIS/traffic source comparison and licences |

```bash
PY=.venv/bin/python
$PY scripts/NorKyst/fetch_record.py grid
$PY scripts/NorKyst/fetch_record.py fetch --workers 4               # ~20 GB raw, ~6 GB on disk, ~20 h
$PY scripts/NorKyst/fetch_record.py fetch --validation --workers 4  # held-out 60 days
$PY scripts/NorKyst/ais_density.py
$PY scripts/NorKyst/norkyst_to_utcef.py --region all --merge norkyst_norway_hd --out dist
$PY scripts/NorKyst/validate_norkyst.py dist/norkyst_norway_hd.utcef --window validation -n 400
```

Data lives outside the repo (`/home/node/signalkdev/norkyst-collector`). The server
answers HTTP 429 above ~4 parallel requests; the fetcher retries with backoff.

## Choices (Nov 2026 build, fit 2025-11-21 – 2026-11-21, held-out 2025-09-23 – 11-21)

- **Surface layer (depth 0)**: the archive has no depth-averaged velocity; a depth
  mean would need all 15 levels. Surface matches the NorKyst GRIBs and what a boat feels.
- **One-year record**: every candidate constituent separates (K2/S2, P1/K1 included),
  so nothing is inferred and no FES2014 is used; the file is plain CC-BY-4.0.
- **Route mask** = cells at or above the region's p90 leisure density; seeds thinned to
  1.5 km (the 500 m DCSM step is below the 800 m grid). Class distances and
  tolerances as DCSM with `--tol-scale 1.0 --maxd-scale 1.0` (a point every 3–4 cells).
- **mean_offset kept**: held-out RMS p50 0.176 vs 0.202 m/s without it, in all regions.
- **Long-period terms (MSF/MM/MF) kept**: held-out RMS p50 0.1757 vs 0.1768 dropped — a near tie.
- Regions overlap at the seams; the merge drops 1,394 same-cell duplicates.

## Results and limits (held-out, merged file, n=400)

- RMS p50/p95 0.154/0.338 m/s, set error at peak 13°.
- Strong tidal streams are well captured: Moskstraumen variance explained 0.84, spring
  daily-max ratio 0.97; Tromsø sound 0.72 / 1.01.
- **Most of the coast is weather- and coastal-current-dominated at the surface**: median
  tidal variance explained ~0.02; p95 speed of the prediction is ~0.47 of the model.
  In the Skagerrak/Oslofjord (tide ~4 % of the variance; Drøbak spring ratio 0.56) the file
  gives the tide plus a yearly mean, not the actual current — use the GRIB forecast there.
- Straits narrower than the 800 m grid (Saltstraumen, Rystraumen) are land in the model
  and have no points.
- AIS density is a relative weight only: many small boats carry no AIS.

## Licence

NorKyst v3 © Norwegian Meteorological Institute (MET Norway), CC-BY-4.0. EMODnet vessel
density (originator Cogea Srl), CC-BY-4.0, used only to choose point positions. The
derived file is CC-BY-4.0 with a not-for-navigation disclaimer.
