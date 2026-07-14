# Nautical Routing & Tidal Streams Data

[![Generate Routing Index](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-routing-index.yml/badge.svg)](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-routing-index.yml)
[![Generate Tide/Current Index](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-tide-current-index.yml/badge.svg)](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-tide-current-index.yml)

Pre-compiled nautical routing graphs for the [SignalK RouteIQ nautical route planner](https://github.com/marcelrv/signalk-routeiq) and tidal/current data sources for the [SignalK Tidal Currents](https://github.com/marcelrv/signalk-tidal-currents) plugin.

**Licensing:** Code and tooling are [GPLv3](LICENSE). Original/compiled data catalogs and databases are [CC-BY-NC-4.0](LICENSE-DATA.md). Third-party data sources (NOAA, BSH, FES2014, OpenCPN) follow their own upstream terms — see the [License & Attribution](#license--attribution) section below.

Routing databases are stored as `.sqlite.gz` (gzip-compressed) to reduce download size. The plugin's download dialog handles decompression automatically.

## Coverage

### Tidal Streams & Currents

![Tide/Current Coverage Map](tide-current-coverage.png)

### Routing Databases

![Coverage Map](coverage-map.png)

## Data Sources

Tidal/current data comes from three kinds of upstream sources, each suited to a different need:

| `type` | Source | What it provides |
|--------|--------|-------------------|
| `harmonic` | OpenCPN / XTide | ASCII harmonic constituents for named stations — small, static files, no refresh needed |
| `grib2` | BSH | Regional gridded current *forecasts* (tide + weather + river forcing) for the North Sea, Baltic and Elbe, refreshed per model cycle |
| `utcef` | FES2014 (via AVISO+) | Dense-grid harmonic tidal current predictions derived from the FES2014 global tide model — static, no refresh needed |
| `utcef` | NOAA CO-OPS | Official harmonic constituents of ~850 NOAA current-prediction reference stations (US East/West Coast, Gulf of Mexico, Alaska, Hawaii, Puerto Rico), converted by [scripts/NOAA](scripts/NOAA/) — static, astronomical predictions valid for years |

> **Removed (July 2026): NOAA RTOFS `grib2` forecasts.** Global RTOFS is a non-tidal ocean circulation model — its currents do not represent tidal streams in coastal waters and were misleading next to real tidal predictions. US coastal waters are covered by the NOAA CO-OPS station harmonics above; a tide-resolving gridded US source (NOS OFS models) is tracked in [#4](https://github.com/marcelrv/signalk-router-data/issues/4).

See [specs/tide-current-catalog.md](specs/tide-current-catalog.md) for the full catalog schema and [specs/utcef-specification.md](specs/utcef-specification.md) for the UTCEF file format.

## Machine-Readable Catalogs

| Catalog | File | Spec |
|---------|------|------|
| Routing graph databases | [`routing-index.json`](routing-index.json) | [specs/routing-database-catalog.md](specs/routing-database-catalog.md) |
| Tide/current data sources | [`tide-current-index.json`](tide-current-index.json) | [specs/tide-current-catalog.md](specs/tide-current-catalog.md) |
| Unified Tidal and Current Exchange Format (UTCEF) | — | [specs/utcef-specification.md](specs/utcef-specification.md) |

## Quick Start

### RouteIQ (Routing)
1. Install the [SignalK RouteIQ nautical route planner](https://github.com/marcelrv/signalk-routeiq)
2. Set `routingDataDir` in the plugin config to a directory on your server
3. Download the `.sqlite.gz` file(s) for your region(s) from [the regions folder](regions/) or use the plugin's built-in "Manage Routing Data" dialog
4. The plugin automatically decompresses `.sqlite.gz` files on download — just use the dialog
5. Restart the plugin

Multiple `.sqlite` files can coexist in the same directory — the plugin merges them at startup.

### Tidal Currents
1. Install the [SignalK Tidal Currents](https://github.com/marcelrv/signalk-tidal-currents) plugin
2. The plugin reads `tide-current-index.json` to list available data sources
3. Download the sources for your region via the plugin UI

## Contributing

We welcome new regions and data sources! See:
- [CONTRIBUTING.md](CONTRIBUTING.md) for routing database format and submission
- [specs/tide-current-catalog.md](specs/tide-current-catalog.md) for adding new tide/current data sources

## License & Attribution

### Routing databases

Each database file may have its own licensing terms as documented in its `metadata.url` and `metadata.contributor` fields (see [routing-index.json](routing-index.json)). Check individual file metadata for attribution and license information.

### Tidal/current data

- **NOAA CO-OPS current stations** (`utcef` sources under `regions/*/noaa_*.utcef`): derived from the official [NOAA Tides & Currents](https://tidesandcurrents.noaa.gov/) harmonic constituents — public domain, U.S. Government work (17 U.S.C. §105). Predictions are astronomical only; actual currents deviate with weather and river flow. Not an official NOAA product.
- **OpenCPN / XTide harmonics** (`harmonic` sources): see the [OpenCPN project](https://github.com/OpenCPN/OpenCPN/tree/master/data/tcdata) for licensing of the underlying station data.
- **UTCEF / FES2014-derived datasets** (`utcef` sources, e.g. files under [`regions/europe`](regions/europe)): derived from the FES2014 global tide model, produced by Noveltis, LEGOS and CLS and distributed by AVISO+, with support from CNES. Redistribution follows the [AVISO+ License Agreement](https://www.aviso.altimetry.fr/fileadmin/documents/data/License_Aviso.pdf) (scientific and non-commercial use).

  **These derived datasets are not endorsed by, affiliated with, or supported by AVISO, CNES, or any FES2014 copyright holder.** If you use this data, please cite: *"FES2014 was produced by Noveltis, Legos and CLS and distributed by Aviso+, with support from Cnes (https://www.aviso.altimetry.fr/)"*. To access the raw global FES2014 dataset directly, visit [AVISO+](https://www.aviso.altimetry.fr/en/data/products/auxiliary-products/global-tide-fes.html).

---

*Maintained by the SignalK RouteIQ and Tidal Currents community.*
