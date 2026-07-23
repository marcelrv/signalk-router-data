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
| `grib2` | NOAA NOS OFS | Regional gridded current *forecasts* for US waters (tide + weather + river forcing) from the NOS Operational Forecast Systems (currently Chesapeake Bay, San Francisco Bay, Salish Sea & Columbia River, northern Gulf of Mexico, West Coast, Gulf of Maine, Delaware Bay, Tampa Bay, Cook Inlet, Lakes Erie/Michigan-Huron/Ontario/Superior, New York Harbor, and the St. Johns River), extracted from model output and re-encoded as compact GRIB2 by [scripts/tide-current](scripts/tide-current/), refreshed daily via the rolling [`ofs-currents-latest`](https://github.com/marcelrv/signalk-router-data/releases/tag/ofs-currents-latest) release |
| `grib2` | Copernicus Marine (CMEMS IBI) | Regional gridded current *forecasts* (tide + weather + river forcing) for the Bay of Biscay and NW Iberia/Galicia, extracted from the Copernicus Marine IBI analysis-and-forecast model and re-encoded as compact GRIB2 by [scripts/tide-current](scripts/tide-current/), refreshed daily via the rolling [`ibi-currents-latest`](https://github.com/marcelrv/signalk-router-data/releases/tag/ibi-currents-latest) release |
| `utcef` | FES2014 (via AVISO+) | Dense-grid harmonic tidal current predictions derived from the FES2014 global tide model — static, no refresh needed |
| `utcef` | NOAA CO-OPS | Official harmonic constituents of ~850 NOAA current-prediction reference stations (US East/West Coast, Gulf of Mexico, Alaska, Hawaii, Puerto Rico), converted by [scripts/NOAA](scripts/NOAA/) — static, astronomical predictions valid for years |

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
- **NOAA NOS OFS surface currents** (`grib2` files on the [`ofs-currents-latest`](https://github.com/marcelrv/signalk-router-data/releases/tag/ofs-currents-latest) release): surface u/v extracted and re-encoded from official [NOAA NOS Operational Forecast System](https://tidesandcurrents.noaa.gov/ofs/) model guidance — public domain, U.S. Government work (17 U.S.C. §105). These files are subsampled, repackaged model guidance produced by this repository — **not an official NOAA product** and not endorsed by NOAA; do not use for primary navigation.
- **Copernicus Marine (CMEMS IBI) surface currents** (`grib2` files on the [`ibi-currents-latest`](https://github.com/marcelrv/signalk-router-data/releases/tag/ibi-currents-latest) release): surface u/v extracted and re-encoded from the [Copernicus Marine Service](https://data.marine.copernicus.eu/product/IBI_ANALYSISFORECAST_PHY_005_001/description) IBI analysis-and-forecast model, redistributed under the [Copernicus Marine license](https://marine.copernicus.eu/user-corner/service-commitments-and-licence). Per the license's terms for a modified/reformatted product: **"Generated using E.U. Copernicus Marine Service Information (https://doi.org/10.48670/moi-00027)"**. These files are geographically subsetted and reformatted from netCDF to GRIB2 by this repository — **not an official Copernicus product** and not endorsed by Mercator Ocean International; do not use for primary navigation.
- **OpenCPN / XTide harmonics** (`harmonic` sources): see the [OpenCPN project](https://github.com/OpenCPN/OpenCPN/tree/master/data/tcdata) for licensing of the underlying station data.
- **UTCEF / FES2014-derived datasets** (`utcef` sources, e.g. files under [`regions/europe`](regions/europe)): derived from the FES2014 global tide model, produced by Noveltis, LEGOS and CLS and distributed by AVISO+, with support from CNES. Redistribution follows the [AVISO+ License Agreement](https://www.aviso.altimetry.fr/fileadmin/documents/data/License_Aviso.pdf) (scientific and non-commercial use).

  **These derived datasets are not endorsed by, affiliated with, or supported by AVISO, CNES, or any FES2014 copyright holder.** If you use this data, please cite: *"FES2014 was produced by Noveltis, Legos and CLS and distributed by Aviso+, with support from Cnes (https://www.aviso.altimetry.fr/)"*. To access the raw global FES2014 dataset directly, visit [AVISO+](https://www.aviso.altimetry.fr/en/data/products/auxiliary-products/global-tide-fes.html).

---

*Maintained by the SignalK RouteIQ and Tidal Currents community.*
