# Contributing to SignalK Routing Data

Thank you for contributing routing data! This repository hosts pre-compiled nautical routing graphs used by the [SignalK Autoroute nautical route planner](https://github.com/marcelrv/signalk-autoroute).

## Database Format

Each region is a standard SQLite database (gzip-compressed to `.sqlite.gz`).
The full schema — tables, node/POI ID hashing, source-tier provenance,
navmesh regions, and what a consuming router must do with each — is
specified in
[`specs/routing-database-format-specification.md`](specs/routing-database-format-specification.md).
Any tool producing a database matching that schema is welcome; this
document does not duplicate it to avoid the two drifting apart.

At minimum: `schema_version` must be set to the version defined in that
spec (currently `1`), `boundary_geometry` must be a valid GeoJSON
polygon/convex hull covering the graph, and node IDs must use the
deterministic coordinate-hashing scheme documented there for cross-region
merge compatibility.

## Contributing a Fix to One Location (Overrides)

For a specific wrong lock passage, missing bridge clearance, or similar
local correction, you don't need to run a full pipeline or submit a whole
region. Open a PR adding a file under `overrides/{continent}/{country}/{region}/`
— see `specs/routing-database-format-specification.md` §2.11 for the
required fields (`reason`, `evidence`, `contributor`). A maintainer
reviews and merges it as a Tier-5 correction; it's picked up automatically
by the next rebuild of that region and never lost to a regeneration.

## Adding a New Region

### 1. Generate the Database

The recommended tool is [signalk-router-pipeline](https://github.com/marcelrv/signalk-router-pipeline), which builds a schema-compatible database from free NOAA/ENC/IENC/OSM/bathymetry sources for US and European waters. If it doesn't suit your data, you can write your own generator — any tool producing a database matching the [format spec](specs/routing-database-format-specification.md) is accepted.

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

Use the deploy script from signalk-router-pipeline:

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
4. The CI workflow will automatically regenerate `routing-index.json` and `coverage-map.png`

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

Open an issue on [signalk-router-pipeline](https://github.com/marcelrv/signalk-router-pipeline) for questions about the pipeline, or on this repository for questions about the database format or catalog.
