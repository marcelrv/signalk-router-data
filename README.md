# Nautical Routing & Tidal Streams Data

[![Generate Routing Index](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-routing-index.yml/badge.svg)](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-routing-index.yml)
[![Generate Tide/Current Index](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-tide-current-index.yml/badge.svg)](https://github.com/marcelrv/signalk-router-data/actions/workflows/generate-tide-current-index.yml)

Pre-compiled nautical routing graphs for the [SignalK Autoroute nautical route planner](https://github.com/marcelrv/signalk-autoroute) and tidal/current data sources for the [SignalK Tidal Currents](https://github.com/marcelrv/signalk-tidal-currents) plugin.

Routing databases are stored as `.sqlite.gz` (gzip-compressed) to reduce download size. The plugin's download dialog handles decompression automatically.

## Coverage

### Tidal Streams & Currents

![Tide/Current Coverage Map](tide-current-coverage.png)

### Routing Databases

![Coverage Map](coverage-map.png)

## Machine-Readable Catalogs

| Catalog | File | Spec |
|---------|------|------|
| Routing graph databases | [`routing-index.json`](routing-index.json) | [specs/routing-database-catalog.md](specs/routing-database-catalog.md) |
| Tide/current data sources | [`tide-current-index.json`](tide-current-index.json) | [specs/tide-current-catalog.md](specs/tide-current-catalog.md) |
| Unified Tidal and Current Exchange Format (UTCEF) | — | [specs/utcef-specification.md](specs/utcef-specification.md) |

## Quick Start

### Autoroute (Routing)
1. Install the [SignalK Autoroute nautical route planner](https://github.com/marcelrv/signalk-autoroute)
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

## License

Each database file may have its own licensing terms as documented in the `metadata.url` and `metadata.contributor` fields. Check individual file metadata for attribution and license information.

---

*Maintained by the SignalK Autoroute and Tidal Currents community.*
