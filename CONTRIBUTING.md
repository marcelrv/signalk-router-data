# Contributing to SignalK Routing Data

Thank you for contributing routing data! This repository hosts pre-compiled nautical routing graphs used by the [SignalK RouteIQ nautical route planner](https://github.com/marcelrv/signalk-routeiq).

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
python3 deploy_to_data_repo.py \
  --input ./myregion.sqlite \
  --continent europe \
  --country nl \
  --region my-region \
  --data-repo /path/to/signalk-router-data \
  --upload
```

The database itself is **not committed to this repository**. It is uploaded as
an asset to the rolling `routing-databases-latest` GitHub Release, because git
never discards old blobs — a ~10 MB `.sqlite.gz` committed on every rebuild
would enlarge every user's clone permanently. What the script writes into the
repo is a small descriptor:

```
regions/{continent}/{country-slug}/{region-slug}.index.json
```

| Component | Convention | Example |
|-----------|-----------|---------|
| `{continent}` | `europe`, `north-america`, `south-america`, `asia`, `africa`, `oceania` | `europe` |
| `{country-slug}` | ISO 3166-1 alpha-2 (lowercase) or descriptive slug | `nl`, `gb`, `usa` |
| `{region-slug}` | Descriptive, hyphen-separated, lowercase | `netherlands`, `usa-east-coast` |

Examples:
- `regions/europe/nl/netherlands.index.json` → asset `netherlands.sqlite.gz`
- `regions/europe/gb/uk-west-coast.index.json` → asset `uk-west-coast.sqlite.gz`
- `regions/north-america/usa/chesapeake-bay.index.json` → asset `chesapeake-bay.sqlite.gz`

See [specs/routing-database-catalog.md §5](specs/routing-database-catalog.md#5-hosting--filesystem-layout)
for the full layout.

### 3. Submit a Pull Request

Only maintainers can upload release assets, so a contribution comes in two halves:

1. Fork this repository
2. Run the deploy script with `--no-upload`. It writes the descriptor and stages
   the `.sqlite.gz` under `.release-staging/` without publishing anything.
3. Commit **the descriptor only** (`.sqlite.gz` files are gitignored) and open a
   Pull Request. Say in the PR where the staged `.sqlite.gz` can be fetched — a
   link to it in your own fork's releases is easiest.
4. A maintainer uploads the asset to `routing-databases-latest` and merges. The
   CI workflow then regenerates `routing-index.json`, `coverage-map.png`, and
   the release notes.

Note that the descriptor's `sha256` is checked by clients, so the file a
maintainer uploads must be byte-identical to the one you staged.

### 4. Updating an Existing Region

To update a region (e.g., with newer ENC data):

1. Regenerate the `.sqlite` file with the pipeline (same `--name`, `--region`)
2. Run the deploy script again — it overwrites the release asset in place and
   rewrites the descriptor. Rebuild as often as you need; only the descriptor's
   few changed lines land in git history.
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
