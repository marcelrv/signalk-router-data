# Routing Database Catalog Specification

This document defines the schema of `routing-index.json` — the machine-readable catalog of pre-compiled nautical routing graph databases used by [SignalK RouteIQ](https://github.com/marcelrv/signalk-routeiq).

This document covers the catalog (which files exist, where to fetch them,
top-level stats). For the internal schema of a single `.sqlite` file —
tables, source-tier provenance, navmesh regions, and how a routing engine
must consume them — see
[`routing-database-format-specification.md`](routing-database-format-specification.md).

## 1. Format Overview

```json
{
  "catalog_schema_version": "1.1.0",
  "version": 2,
  "generated": "2026-06-16T10:47:29Z",
  "region_count": 1,
  "regions": [ ... ]
}
```

> **Changed in 1.1.0** — routing databases are published as assets on a rolling
> GitHub Release instead of being committed to this repository. Region entries
> gained `filename` and `download_url`, and `file` is now present only for the
> few regions still served straight out of the repo. See
> [§5 Hosting & Filesystem Layout](#5-hosting--filesystem-layout).
> Consumers must prefer `download_url` and treat `file` as a fallback.

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
| `filename` | String | Yes | Bare filename of the `.sqlite.gz` asset (e.g. `netherlands.sqlite.gz`) |
| `download_url` | String | Cond. | Absolute URL of the release asset. Present for every release-hosted database — i.e. all of them, in practice. Mutually exclusive with `file`. |
| `file` | String | Cond. | Repository-relative path to the `.sqlite.gz`, resolved by consumers against the catalog's own directory. Present only when the database is committed to the repo rather than released. Mutually exclusive with `download_url`. |
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

## 5. Hosting & Filesystem Layout

### 5.1 Where the databases live

Routing databases are published as assets on a **rolling GitHub Release**:

```
tag:   routing-databases-latest
asset: {region}.sqlite.gz
url:   https://github.com/marcelrv/signalk-router-data/releases/download/routing-databases-latest/{region}.sqlite.gz
```

Assets are overwritten in place on every deploy (`gh release upload --clobber`),
so the download URLs are stable and republishing a region costs nothing.

This is the same mechanism the tide/current GRIB2 files already use
(`ofs-currents-latest`, `ibi-currents-latest`, …), and it exists for the same
reason: git never discards old blobs, so committing a ~10 MB `.sqlite.gz` per
rebuild would grow the clone size of this repository permanently, for every
user, forever. A region can be rebuilt as often as it needs to be.

### 5.2 What the repository holds

One small descriptor per region — the region's catalog entry, written at
deploy time by the pipeline's `deploy_to_data_repo.py` from the metadata,
stats and checksums it reads out of the database:

```
regions/{continent}/{country}/{region}.index.json
```

The descriptor is the catalog entry verbatim, plus a `release_tag` field
naming the release that carries the asset (`generate_index.py` turns that into
`download_url` and drops it). Region IDs are auto-generated:

```
{continent}_{country}_{region}
```

`.sqlite.gz` and `.sqlite` files are gitignored. A checkout may still contain
them locally — see §6.

## 6. Producer

The catalog is generated by `scripts/generate_index.py`, which:
1. Reads every `regions/**/*.index.json` descriptor and derives its `download_url`
2. Additionally scans `regions/` for any `.sqlite.gz` / `.sqlite` files physically
   present — a development convenience for a database not yet released — reading
   the SQLite `metadata` table and emitting a `file` entry for it
3. Prefers the descriptor whenever both exist for one region id: the descriptor
   describes the asset consumers will actually download
4. Produces `routing-index.json` and `coverage-map.png`

`scripts/routing_release_notes.py` renders the release body from
`routing-index.json`, so the region table on the release page is regenerated
from the published catalog rather than maintained by hand.

### 6.1 Publishing a region

From the [pipeline repo](https://github.com/marcelrv/signalk-router-pipeline):

```bash
python3 deploy_to_data_repo.py \
    --input ./data/zeeland.sqlite \
    --continent europe --country nl --region zeeland \
    --data-repo ../router-data \
    --upload
```

This gzips the database into `.release-staging/`, verifies it decompresses to a
usable SQLite file, uploads it to the release (creating the release if needed),
writes the descriptor, and regenerates the catalog. Commit the descriptor and
`routing-index.json`; the `Generate Routing Index & Coverage Map` workflow
refreshes the coverage map and release notes on push.

## 7. Consumer

The [SignalK RouteIQ](https://github.com/marcelrv/signalk-routeiq) plugin fetches this catalog to:
- Display available databases in a download UI, downloading from `download_url`
  (falling back to `file` resolved against the catalog directory)
- Verify downloaded file integrity via SHA-256
- Render coverage boundaries on a map
- Check for updates via `last_update` timestamps
