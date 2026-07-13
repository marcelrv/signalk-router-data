# Routing Database Catalog Specification

This document defines the schema of `routing-index.json` — the machine-readable catalog of pre-compiled nautical routing graph databases used by [SignalK Autoroute](https://github.com/marcelrv/signalk-autoroute).

This document covers the catalog (which files exist, where to fetch them,
top-level stats). For the internal schema of a single `.sqlite` file —
tables, source-tier provenance, navmesh regions, and how a routing engine
must consume them — see
[`routing-database-format-specification.md`](routing-database-format-specification.md).

## 1. Format Overview

```json
{
  "catalog_schema_version": "1.0.0",
  "version": 2,
  "generated": "2026-06-16T10:47:29Z",
  "region_count": 1,
  "regions": [ ... ]
}
```

## 2. Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `catalog_schema_version` | String | Yes | SemVer of this catalog specification (e.g. `"2.0.0"`) |
| `version` | Integer | Yes | Backward-compatible numeric version (same major as `catalog_schema_version`) |
| `generated` | String | Yes | ISO 8601 UTC timestamp when the catalog was generated |
| `region_count` | Integer | Yes | Number of entries in the `regions` array |
| `regions` | Array | Yes | List of region entry objects |

## 3. Region Entry Schema

Each entry in the `regions` array represents a single `.sqlite.gz` routing graph database.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | String | Yes | Auto-generated region identifier derived from the filesystem path (e.g. `europe_nl_netherlands`) |
| `file` | String | Yes | Relative path to the `.sqlite.gz` file in the repository |
| `inner_filename` | String | Yes | Filename of the decompressed `.sqlite` file |
| `sha256` | String | Yes | Hex SHA-256 hash of the `.sqlite.gz` file for integrity verification |
| `size_bytes` | Integer | Yes | Compressed file size in bytes |
| `compression` | String | Yes | Compression method: `"gzip"` or `"none"` |
| `country` | String | Yes | ISO 3166-1 alpha-2 country code |
| `name` | String | Yes | Human-readable region name |
| `description` | String | Yes | Coverage area and data source description |
| `last_update` | String | Yes | ISO 8601 UTC timestamp of the last data update |
| `schema_version` | Integer | Yes | SQLite database schema version of the `.sqlite` file |
| `tags` | Array | Yes | Array of tag strings for filtering and categorization |
| `contributor` | String | Yes | GitHub username or organization that contributed the data |
| `url` | String | Yes | Link to the original data source or license information |
| `bounding_box` | Object | Yes | Geographic bounding box (`min_lat`, `min_lon`, `max_lat`, `max_lon`) |
| `boundary_geometry` | Object | Yes | GeoJSON Polygon or MultiPolygon of the region's coverage area |
| `architecture` | String | No | Copied from the database's `metadata.architecture` — identifies the structural approach used (e.g. `"navmesh-hybrid"`, `"point-graph"`). Informational only; absent is legal and simply means the producer didn't set it. |
| `license` | String | No | Copied from `metadata.license`. Falls back to this repository's default (`LICENSE-DATA.md`) when absent. |
| `copyright` | String | No | Copied from `metadata.copyright` — ready-to-display attribution string, see `routing-database-format-specification.md` §8. |
| `stats` | Object | Yes | Graph statistics object |

### 3.1 `stats` Object

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | Integer | Total number of graph nodes |
| `edges` | Integer | Total number of edges |
| `pois` | Integer | Number of points of interest |
| `coastal_nodes` | Integer | Number of nodes classified as coastal (type 0) |
| `inland_nodes` | Integer | Number of nodes classified as inland (type 1) |
| `navmesh_regions` | Integer | Number of `navmesh_regions` rows (0 for point-graph-only databases) |
| `override_count` | Integer | Number of rows in `override_provenance` (tier-5 corrections included in this build) |
| `tier_counts` | Object | Optional map of source tier (`"1"`–`"6"`) to combined node+edge+poi row count, for a quick data-quality-mix summary without opening the database |

## 4. Tag Taxonomy

Standardized tags for the routing database catalog:

| Tag | Description |
|-----|-------------|
| `official` | Data from an official hydrographic office |
| `enc` | Derived from S-57 ENC charts |
| `osm` | Derived from OpenStreetMap waterway data |
| `inland` | Contains inland waterway centerlines |
| `coastal` | Contains coastal navmesh |
| `experimental` | Work in progress |
| `rws` | Rijkswaterstaat source data |
| `navmesh` | Contains one or more `navmesh_regions` |
| `osm-fused` | Includes OpenStreetMap/OpenSeaMap data as a Tier-3 fallback layer |
| `bathymetry-filled` | Includes GEBCO/EMODnet Tier-4 depth fill where chart soundings are absent |
| `community-overrides` | Includes one or more human/AI-reviewed Tier-5 overrides from `overrides/` |

## 5. Filesystem Layout Convention

```
regions/{continent}/{country}/{region}.sqlite.gz
```

Region IDs are auto-generated:
```
{continent}_{country}_{region}
```

## 6. Producer

The catalog is generated by `scripts/generate_index.py`, which:
1. Scans `regions/` recursively for `.sqlite.gz` and `.sqlite` files
2. Decompresses and reads the SQLite `metadata` table
3. Computes SHA-256 hashes and file sizes
4. Produces `routing-index.json` and `coverage-map.png`

## 7. Consumer

The [SignalK Autoroute](https://github.com/marcelrv/signalk-autoroute) plugin fetches this catalog to:
- Display available databases in a download UI
- Verify downloaded file integrity via SHA-256
- Render coverage boundaries on a map
- Check for updates via `last_update` timestamps
