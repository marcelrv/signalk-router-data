# Tide/Current Data Source Catalog Specification

This document defines the schema of `tide-current-index.json` — the machine-readable catalog of tidal current data sources for the [SignalK Tidal Currents](https://github.com/marcelrv/signalk-tidal-currents) plugin.

## 1. Format Overview

```json
{
  "catalog_schema_version": "1.0.0",
  "version": 1,
  "generated": "2026-07-03T10:30:00Z",
  "source_count": 2,
  "sources": [ ... ]
}
```

## 2. Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `catalog_schema_version` | String | Yes | SemVer of this catalog specification (e.g. `"1.0.0"`) |
| `version` | Integer | Yes | Backward-compatible numeric version (same major as `catalog_schema_version`) |
| `generated` | String | Yes | ISO 8601 UTC timestamp when the catalog was generated |
| `source_count` | Integer | Yes | Number of entries in the `sources` array |
| `sources` | Array | Yes | List of tidal current data source entries |

## 3. Source Entry Schema

Each entry in the `sources` array describes one upstream data provider and its downloadable files.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | String | Yes | Unique stable identifier for this source |
| `source` | String | Yes | Machine-readable provider code for grouping/filtering (e.g. `"noaa"`, `"opencpn"`) |
| `type` | String | Yes | Data format: `"harmonic"` for XTide/OpenCPN harmonic files, `"grib2"` for GRIB2 gridded forecasts, `"utcef"` for UTCEF datasets (see `specs/utcef-specification.md`) |
| `name` | String | Yes | Human-readable source name |
| `description` | String | Yes | Longer description of the data and its coverage |
| `contributor` | String | Yes | Organization that provides the data |
| `url` | String | Yes | URL to the provider's homepage or data documentation |
| `tags` | Array | Yes | Array of tag strings for filtering and categorization |
| `region` | Object | Yes | Geographic scope of the source |
| `update_check` | Object | Yes | Mechanism for detecting new versions |
| `files` | Array | Yes | List of downloadable file entries |

### 3.1 `region` Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | String | Yes | Human-readable region name |
| `bounding_box` | Object | Yes | Geographic bounding box (`min_lat`, `min_lon`, `max_lat`, `max_lon`) |
| `boundary_geometry` | Object | Yes | GeoJSON Polygon or MultiPolygon for map rendering |

### 3.2 `update_check` Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `method` | String | Yes | Update detection method: `"sha256"` for static files, `"expiry"` for time-based |
| `last_checked` | String | Yes | ISO 8601 UTC timestamp of last verification |
| `max_age_hours` | Integer | No | Required for `"expiry"`: max age before re-download (e.g. `24`) |
| `latest_cycle` | String | No | For `"expiry"`: ISO 8601 UTC timestamp of the latest known forecast cycle |

### 3.3 File Entry Schema

Files can be either **static** (direct URL with integrity hash) or **template-based** (parameterized URL for time-series data).

#### Static File

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `filename` | String | Yes | Name of the file |
| `url` | String | Yes | Direct download URL |
| `sha256` | String | Yes | Hex SHA-256 hash for integrity |
| `size_bytes` | Integer | Yes | File size in bytes |

#### Template-Based File

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `region_id` | String | Yes | Identifier for the geographic sub-region |
| `name` | String | Yes | Human-readable region name |
| `description` | String | Yes | Description of the sub-region |
| `boundary_geometry` | Object | Yes | GeoJSON polygon of the sub-region for map rendering |
| `type` | String | Yes | `"forecast"` or `"nowcast"` |
| `variant` | String | No | Disambiguates multiple files that would otherwise share `region_id` + `type` (see below) |
| `url_template` | String | Yes | URL template with `{YYYYMMDD}`, `{HH}`, `{hour:03d}` variables |
| `forecast_hours` | Array | Yes | Array of forecast hour offsets (e.g. `[24, 48, 72]`) |
| `cycle_hours` | Array | Yes | Array of cycle hours (e.g. `["00"]`) |

**`region_id` + `type` + `variant` together must be unique within a source.** A source normally
bundles every forecast horizon it publishes into ONE template file (`forecast_hours: [24, 48,
72]` with one `url_template` parameterized by `{hour:03d}`) — that's the preferred shape, and
`variant` should be omitted (defaults to absent). Only use `variant` when a source genuinely
cannot bundle its horizons into one file/cycle — e.g. BSH publishes each forecast day as a
separate physical file with its own cycle-availability (day+1 at both the 00Z and 12Z cycles,
day+2/day+3 only at 12Z), which a single shared `cycle_hours` can't express. In that case, emit
one template file entry per horizon with the SAME `region_id`/`type` and a distinct `variant`
(e.g. `"+24h"`, `"+48h"`, `"+72h"`) so consumers can tell them apart and download/track each one
independently. A consumer that predates `variant` will still see these as ambiguous — this is an
additive, backward-compatible field, not a `catalog_schema_version` major bump, but producers
should prefer bundling over `variant` whenever the upstream data allows it.

## 4. URL Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{YYYYMMDD}` | Forecast cycle date | `20260703` |
| `{HH}` | Cycle hour | `00` |
| `{hour:03d}` | Forecast hour offset | `024` |

## 5. Update Detection Flow

### SHA-256 Method (Static Sources)
1. Plugin reads `sha256` from the index
2. Computes SHA-256 of the local copy
3. If different → update available

### Expiry Method (Time-Series Sources)
1. Plugin reads `latest_cycle` and `max_age_hours`
2. Compares against last download timestamp
3. If `latest_cycle > last_download` or `now - last_download > max_age_hours` → newer data available

The plugin never needs to hit upstream URLs directly to check for updates — the catalog serves as the authoritative freshness indicator.

## 6. Tag Taxonomy

Standardized tags for the tide/current catalog:

| Tag | Description |
|-----|-------------|
| `harmonic` | XTide/OpenCPN ASCII harmonic constituents |
| `grib2` | GRIB2 gridded forecast fields |
| `utcef` | UTCEF datasets (ZIP container with harmonic current/height stations) |
| `global` | Global coverage |
| `regional` | Regional sub-region coverage |
| `gridded` | Gridded/interpolated data model |
| `station-based` | Discrete station locations |
| `forecast` | Forecast/prediction data |
| `static` | Rarely-changing reference data |
| `daily` | Updated daily |
| `community` | Community-contributed data |
| `noaa` | NOAA/NCEP source |
| `opencpn` | OpenCPN project source |
| `xtide` | XTide format |
| `ocean-currents` | Ocean current content |

## 7. Producer

The catalog is generated by `scripts/tide-current/generate_index.py`, which:
1. Auto-discovers collector scripts in `scripts/tide-current/sources/*.py`
2. Runs each collector, captures JSON output
3. Validates schemas and merges into the final index
4. Adds a new collector to add a new data source

## 8. Consumer

The [SignalK Tidal Currents](https://github.com/marcelrv/signalk-tidal-currents) plugin fetches this catalog to:
- Display available data sources in a download UI with coverage map
- Verify downloaded file integrity via SHA-256
- Detect new forecast cycles via expiry mechanism
- Filter/group sources by `source`, `type`, and `tags`
