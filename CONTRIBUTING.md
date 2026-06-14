# Contributing to SignalK Routing Data

Thank you for contributing routing data! This repository hosts pre-compiled nautical routing graphs used by the [SignalK Autoroute nautical route planner](https://github.com/marcelrv/signalk-autoroute).

## Database Format

Each region is a standard SQLite database (gzip-compressed to `.sqlite.gz`). The schema is defined below — any tool that produces a compatible database is welcome.

### Tables

**`metadata`** — one row describing the data source:
| Column | Type | Description |
|--------|------|-------------|
| `country` | TEXT | ISO 3166-1 alpha-2 code |
| `name` | TEXT | Human-readable region name |
| `description` | TEXT | Coverage area and data sources |
| `last_update_date` | TEXT | ISO 8601 date of last update |
| `tags` | TEXT | JSON array of tags (e.g. `["enc","coastal"]`) |
| `contributor` | TEXT | GitHub username or organization |
| `url` | TEXT | Link to original data source / license |
| `bounding_box` | TEXT | JSON `[minLon, minLat, maxLon, maxLat]` |
| `boundary_geometry` | TEXT | GeoJSON polygon of the region's convex hull |
| `schema_version` | INTEGER | Must be `2` or higher |
| `region_id` | INTEGER | Unique auto-increment ID |

**`nodes`** — routing graph vertices:
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Globally unique: `(type * 648_000_000_000_000) + (lat_int * 36_000_000) + lon_int` |
| `lat` | REAL | Latitude (degrees) |
| `lon` | REAL | Longitude (degrees) |
| `region_id` | INTEGER | References `metadata.region_id` |

Node IDs encode the node type: `Math.floor(id / 648000000000000)` gives 0 (coastal) or 1 (inland). The `lat` and `lon` are snapped to 5 decimal places. This scheme allows merging databases from multiple regions without ID collisions.

**`edges`** — connections between nodes:
| Column | Type | Description |
|--------|------|-------------|
| `source` | INTEGER | Source node ID |
| `target` | INTEGER | Target node ID |
| `distance` | REAL | Edge length in metres |
| `edge_type` | TEXT | `'coastal'` or `'inland'` |

**`pois`** — points of interest (marinas, anchorages, etc.):
| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT | Deterministic MD5 hash of `"{type}_{round(lat,5)}_{round(lon,5)}"` (13 hex chars) |
| `type` | TEXT | POI category |
| `name` | TEXT | Display name |
| `lat` | REAL | Latitude |
| `lon` | REAL | Longitude |
| `region_id` | INTEGER | References `metadata.region_id` |

POI IDs use `INSERT OR IGNORE` so duplicates from overlapping regions are skipped.

### File Format

- Stored as `.sqlite.gz` in the repo (gzip-compressed).
- The download handler on the plugin side decompresses automatically.
- SHA-256 of the `.sqlite.gz` file is recorded in `index.json` for integrity checks.

### Requirements

- `schema_version` must be `2` or higher.
- `boundary_geometry` must be a valid GeoJSON polygon/convex hull covering the graph (used for coverage map rendering).
- `bounding_box` must be `[minLon, minLat, maxLon, maxLat]`.
- Node IDs must use the deterministic coordinate-hashing scheme above for cross-region merge compatibility.

## Adding a New Region

### 1. Generate the Database

The recommended tool is the [nautical_routing_pipeline.py](https://github.com/marcelrv/signalk-autoroute) from the autoroute project. Note that this pipeline was built for Dutch waters and may need adaptation for other regions. If it doesn't suit your data, you can write your own generator — any tool producing a database matching the [schema above](#database-format) is accepted.

Example (pipeline-specific):

```bash
python3 nautical_routing_pipeline.py \
  --input-dir ./output_geojson \
  --output ./myregion.sqlite \
  --country NL \
  --name "My Region Name" \
  --description "Brief description of coverage area and data sources" \
  --tags '["official","my-source","inland","coastal"]' \
  --contributor "your-github-username" \
  --url "https://source-of-original-data.example.com"
```

### 2. Deploy with the Script (Recommended)

Use the deploy script from the autoroute project:

```bash
python3 backend/deploy_to_data_repo.py \
  --input ./myregion.sqlite \
  --continent europe \
  --country nl \
  --region my-region \
  --data-repo /path/to/signalk-router-data
```

This gzips the `.sqlite` file and places it at:

```
regions/{continent}/{country-slug}/{region-slug}.sqlite.gz
```

| Component | Convention | Example |
|-----------|-----------|---------|
| `{continent}` | `europe`, `north-america`, `south-america`, `asia`, `africa`, `oceania` | `europe` |
| `{country-slug}` | ISO 3166-1 alpha-2 (lowercase) or descriptive slug | `nl`, `gb`, `usa` |
| `{region-slug}` | Descriptive, hyphen-separated, lowercase | `netherlands`, `usa-east-coast` |

Examples:
- `regions/europe/nl/netherlands.sqlite.gz`
- `regions/europe/gb/uk-west-coast.sqlite.gz`
- `regions/north-america/usa/chesapeake-bay.sqlite.gz`

### 3. Submit a Pull Request

1. Fork this repository
2. Add your `.sqlite.gz` file in the correct folder (or use the deploy script above)
3. Open a Pull Request
4. The CI workflow will automatically regenerate `index.json` and `coverage-map.png`

### 4. Updating an Existing Region

To update a region (e.g., with newer ENC data):

1. Regenerate the `.sqlite` file with the pipeline (same `--name`, `--region`)
2. Run the deploy script again — it overwrites the existing `.sqlite.gz`
3. Submit a PR — the new `last_update_date` in the metadata will signal to users that an update is available

## Guidelines

- **File size**: Please keep individual `.sqlite` files under 200 MB. For very large regions, consider splitting into sub-regions.
- **Metadata completeness**: Always provide `country`, `name`, `description`, `tags`, `contributor`, and `url` so users can evaluate the data.
- **Tags**: Use consistent tag names. Common tags:
  - `official` — from an official hydrographic office
  - `enc` — derived from S-57 ENC charts
  - `osm` — derived from OpenStreetMap waterway data
  - `inland` — contains inland waterway centerlines
  - `coastal` — contains coastal navmesh
  - `experimental` — work in progress
- **License**: Ensure you have the right to redistribute the derived routing graph. Set the `url` field to document the data source and its license terms.

## Getting Help

Open an issue on the [autoroute project](https://github.com/marcelrv/signalk-autoroute) for questions about the pipeline or database format.
