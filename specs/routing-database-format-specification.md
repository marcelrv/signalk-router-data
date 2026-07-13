# Routing Database Format Specification

This document defines the internal schema of a single compiled routing
graph `.sqlite` (before gzip) referenced from `routing-index.json` — see
[`routing-database-catalog.md`](routing-database-catalog.md) for the catalog
that lists these files. Where that document describes *which files exist
and how to fetch them*, this document describes *what's inside one file and
how a routing engine must interpret it*.

Any tool producing a database matching this schema is a valid producer —
[signalk-router-pipeline](https://github.com/marcelrv/signalk-router-pipeline)
is the reference implementation, not a requirement. Any engine that reads a
schema-compatible database correctly is a valid consumer —
[SignalK Autoroute](https://github.com/marcelrv/signalk-autoroute) is the
reference implementation, not the only one.

## 1. Schema Version

This document specifies **`metadata.schema_version = 1`** — the initial
released format. There is no prior published version to stay compatible
with, so everything in §2 is simply the schema, not a delta against
something else.

- `metadata.architecture` (TEXT, optional) identifies the structural
  approach used to build the file (e.g. `"navmesh-hybrid"`,
  `"point-graph"`) so a consumer or debugging tool can tell at a glance
  which representations (navmesh regions, lanes, hierarchy) to expect
  without inspecting table contents first. Informational only — never
  required for correct parsing, and a database with no `navmesh_regions`
  rows and every edge at tier 1 is just as legal as one that uses every
  feature in this spec.
- `metadata.dataset_version` (TEXT, optional): a content revision tag,
  independent of `schema_version`, bumped whenever the underlying
  chart/data inputs are re-processed without a structural change — same
  separation of concerns as UTCEF's `dataset_version`
  (see `utcef-specification.md` §2).
- Future structural changes bump `schema_version`. A consumer built
  against this document should treat an unrecognized higher value as
  informational rather than fatal, and should ignore tables/columns it
  doesn't recognize rather than failing outright — but that's forward
  guidance for whenever a version 2 exists, not a statement that one does.

## 2. Table Reference

### 2.1 `metadata`

One row per region:

| Column | Type | Required | Description |
|---|---|---|---|
| `country` | TEXT | Yes | ISO 3166-1 alpha-2 code |
| `name` | TEXT | Yes | Human-readable region name |
| `description` | TEXT | Yes | Coverage area and data sources |
| `last_update_date` | TEXT | Yes | ISO 8601 date of last update |
| `tags` | TEXT (JSON array) | Yes | e.g. `["enc","coastal","navmesh"]` — see catalog spec §4 for the tag taxonomy |
| `contributor` | TEXT | Yes | GitHub username or organization |
| `url` | TEXT | Yes | Link to original data source / license |
| `bounding_box` | TEXT (JSON) | Yes | `{"min_lat":..., "min_lon":..., "max_lat":..., "max_lon":...}` |
| `boundary_geometry` | TEXT (GeoJSON) | Yes | Polygon/MultiPolygon covering the graph |
| `schema_version` | INTEGER | Yes | `1`, per §1 |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Yes | Unique per-region ID. Referenced by `nodes.region_id`, `edges` (indirectly via node), `pois.region_id`, and `navmesh_regions.region_id` — despite the FK columns on those tables being named `region_id`, the column on `metadata` itself is `id`, not `region_id`. |
| `license` | TEXT | No | Data-sharing terms for this specific compiled region (e.g. `"CC-BY-NC-4.0"`). Falls back to the repository-wide default in `LICENSE-DATA.md` when absent. |
| `copyright` | TEXT | No | Attribution statement to surface in a consuming UI (e.g. "© Rijkswaterstaat, © OpenStreetMap contributors, 2026"). |
| `architecture` | TEXT | No | See §1. |
| `dataset_version` | TEXT | No | See §1. |

### 2.2 `data_sources`

One row per upstream data source that contributed to this file — the
normalized equivalent of UTCEF's `metadata.data_sources` array, structured
as a table (not embedded JSON) so individual nodes/edges/pois can reference
a source by a small integer FK instead of repeating a URL string per row.

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | INTEGER PRIMARY KEY | Yes | Referenced by `nodes.source_id`, `edges.source_id`, `pois.source_id`. |
| `name` | TEXT | Yes | Human-readable source name (e.g. `"NOAA ENC"`, `"OpenStreetMap"`, `"EMODnet Bathymetry"`, `"Community override PR #42"`). |
| `source_type` | TEXT | Yes | One of: `enc`, `ienc`, `osm`, `bathymetry`, `vessel-density`, `override`, `other`. |
| `url` | TEXT | No | Link to the provider or the specific dataset release. |
| `license` | TEXT | No | This source's own license/terms (e.g. `"ODbL-1.0"`, `"Public Domain"`). See `LICENSE-DATA.md` for the full per-source breakdown. |
| `attribution_text` | TEXT | No | Exact attribution string this source's terms require (e.g. `"© OpenStreetMap contributors"`). |
| `accessed_date` | TEXT | No | ISO 8601 date the data was pulled/generated. |
| `default_tier` | INTEGER | Yes | The source-tier (§2.4) rows from this source get unless overridden per-row. |

### 2.3 `edge_type_enum` / `poi_type_enum`

`edge_type_enum`: `0=coastal, 1=inland`.
`poi_type_enum`: `0=harbour, 1=lock, 2=bridge, 3=fairway, 4=waterway`.

### 2.4 Source tiers (not a table — a shared integer domain)

`source_tier` columns on `nodes`, `edges`, and `pois` use this fixed
6-value domain (no enum table needed, the meaning is invariant across all
databases):

| Tier | Meaning | Router guidance |
|---|---|---|
| 1 | Official hydrographic authority (ENC/IENC) | Trust fully |
| 2 | Other official waterway-authority data outside strict ENC | Trust fully |
| 3 | OpenStreetMap / OpenSeaMap community data | Trust topology; treat quantitative attributes (depth/height/width) as provisional |
| 4 | Bathymetric raster fill (GEBCO/EMODnet) | Apply extra safety margin; never the sole basis for a hard depth/clearance pass |
| 5 | Human/AI-curated override, post human sign-off | Trust fully for that specific node/edge/poi |
| 6 | AIS/vessel-density–derived candidate | **Not routable by default.** See §5. |

### 2.5 `edge_kind_enum`

| `id` | Name | Meaning |
|---|---|---|
| `0` | `centerline` | The default kind: an ordinary point-to-point edge. Covers plain skeleton edges and any edge not part of a lane pair, navmesh region, or hierarchy. |
| `1` | `navmesh_boundary` | An edge along the outer boundary of a `navmesh_regions` triangulation. Not directly traversed as a weighted graph edge — see §6. |
| `2` | `lane` | One directional side of a buoyed/regulated channel (paired with another `lane` edge running the opposite direction, offset to the other side). |
| `3` | `macro` | A precomputed supernode-to-supernode hierarchical shortcut aggregating a longer underlying path. See §7. |

### 2.6 `node_kind_enum`

| `id` | Name | Meaning |
|---|---|---|
| `0` | `point` | An ordinary routable vertex (default). |
| `1` | `navmesh_vertex` | A vertex of a navmesh region's triangulation (may or may not also be on the region boundary). |
| `2` | `supernode` | A hierarchy anchor — a navmesh-region boundary, skeleton junction, or lock/bridge/POI location used as an endpoint for `macro` edges. |

### 2.7 `nodes`

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | INTEGER | Yes | Deterministic hash of snapped coordinates + type, see formula below. |
| `lat` | REAL | Yes | Latitude (degrees) |
| `lon` | REAL | Yes | Longitude (degrees) |
| `region_id` | INTEGER | Yes | FK `metadata.id` |
| `node_depth` | REAL | No, default `-1` | Charted depth at this node in metres, `-1` if unknown. Distinct from an edge's `min_depth` (§2.8), which is the shallowest point *along* an edge, not at either endpoint. |
| `node_kind_id` | INTEGER | No, default `0` | FK to §2.6. |
| `source_tier` | INTEGER | No, default `1` | FK to the domain in §2.4. |
| `source_id` | INTEGER | No | FK to `data_sources.id`. |

The node ID hashing formula below is what allows region databases to be
loaded together and merge without collisions — it MUST stay stable across
every database produced against this spec, and none of the other columns
are part of the ID:

```
lat_int  = int((round(lat, 5)  + 90.0)  * 100000)   // 0 .. 18,000,000
lon_int  = int((round(lon, 5)  + 180.0) * 100000)   // 0 .. 36,000,000
type_int = 1 if node is inland else 0
id = (type_int * 648_000_000_000_000) + (lat_int * 36_000_000) + lon_int
```

### 2.8 `edges`

| Column | Type | Required | Description |
|---|---|---|---|
| `source` | INTEGER | Yes | Source node ID |
| `target` | INTEGER | Yes | Target node ID |
| `distance` | REAL | Yes | Edge length in metres |
| `min_depth` | REAL | No, default `99.0` | Shallowest charted depth (metres) anywhere along the edge — the primary draft-constraint gate. `99.0` means "not sampled against charted depth data," not "confirmed 99m deep"; a consumer should not treat the default as a safety guarantee. |
| `drval1` | REAL | No | Raw S-57 `DRVAL1` (charted minimum depth-area value) sample this edge's `min_depth` was derived from, kept for traceability back to the source chart attribute. `NULL` when no depth-area feature covered the edge. Not itself a routing input — use `min_depth`. |
| `max_air_draft` | REAL | No, default `999.0` | Lowest fixed vertical clearance (metres) anywhere along the edge — the air-draft-constraint gate. `999.0` means no charted fixed obstruction was found (or the crossing is a movable-bridge opening, which the pipeline stamps at `999.0` after computing the real opening geometry — see the pipeline's bridge-opening handling). |
| `min_width` | REAL | No, default `999.0` | Narrowest charted width (metres) anywhere along the edge — the beam-constraint gate. `999.0` means unconstrained/not sampled. |
| `cost_factor` | REAL | No, default `1.2` | Multiplicative routing cost (e.g. `0.8` for a preferred fairway, `1.2` for open water/unclassified). |
| `distance_to_land` | REAL | No, default `9999.0` | Distance in metres from the edge to the nearest land polygon, for consumers that want a soft coastal-clearance preference independent of the hard `crosses_land` check below. |
| `edge_type_id` | INTEGER | Yes | FK `edge_type_enum` (§2.3) |
| `traffic_mode` | INTEGER | No, default `0` | `0=two-way, 1=one-way fwd, 2=one-way rev`. Applies to `centerline` edges carrying a regulatory one-way restriction that doesn't warrant a full `lane` pair. `lane`-kind edges are directional by construction (the row's own `source`→`target` direction *is* the direction of travel) and don't need this set. |
| `crosses_land` | INTEGER | No, default `0` | `1` if this edge is known to cross a land polygon. Skeleton/lane/navmesh-boundary edges are land-safe by construction and should never carry `1`; present mainly for the placeholder/legacy edge-kind path. A conformant producer should not emit routable `1` edges at all rather than rely on a consumer to filter them. |
| `crosses_obstacle` | INTEGER | No, default `0` | `1` if this edge crosses a charted obstruction/restricted-area polygon not otherwise captured by `min_depth`/`max_air_draft`/`min_width`. Treated by at least one known consumer as an unconditional hard exclusion regardless of vessel dimensions — producers should only set this for a genuine hard obstruction, not a soft preference (use `cost_factor` for that). |
| `edge_kind_id` | INTEGER | No, default `0` | FK to §2.5. |
| `source_tier` | INTEGER | No, default `1` | FK to the domain in §2.4. |
| `source_id` | INTEGER | No | FK to `data_sources.id`. |
| `width_profile` | TEXT (JSON) | No | Array of `[fraction_along_edge, width_m]` samples for `centerline`/`lane` edges, e.g. `[[0.0, 45], [0.5, 30], [1.0, 50]]`, for beam-constraint checks that need more than one scalar. `min_width` above remains the required scalar summary (`= min` of the profile) for consumers that don't read `width_profile`. |

### 2.9 `navmesh_regions`

One row per open-water region represented as a triangulated mesh instead
of interior point-graph nodes.

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | INTEGER PRIMARY KEY | Yes | |
| `region_id` | INTEGER | Yes | FK `metadata.id` (which compiled database region this belongs to — same field name/meaning as `nodes.region_id`). |
| `boundary_geometry` | TEXT (GeoJSON) | Yes | Polygon/MultiPolygon of the region's free-space outline. |
| `vertices` | TEXT (JSON) | Yes | Flat array of `[lat, lon]` pairs, index `0..n-1`. |
| `triangles` | TEXT (JSON) | Yes | Array of `[i, j, k]` vertex-index triples (one per triangle). |
| `triangle_adjacency` | TEXT (JSON) | No | Array parallel to `triangles`, each `[n0, n1, n2]` giving the adjacent triangle index across each edge, or `-1` at the region boundary. Consumers can recompute this from `triangles` if omitted, but storing it saves a rebuild at load time. |
| `boundary_node_ids` | TEXT (JSON) | Yes | Array of `nodes.id` values — the subset of `vertices` that are also ordinary graph nodes (i.e. where `centerline`/`lane`/`macro` edges may attach to enter or leave this region). |
| `depth_ceiling_m` | REAL | Yes | The "universally safe" depth threshold this region was classified against (see the pipeline's classification step). |
| `source_tier` | INTEGER | No, default `1` | Tier of the depth/boundary data used for classification. |
| `source_id` | INTEGER | No | FK `data_sources.id`. |

### 2.10 `pois`

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | TEXT | Yes | Deterministic MD5 hash of `"{poi_type}_{round(lat,5)}_{round(lon,5)}"` (13 hex chars). `INSERT OR IGNORE` handles duplicates across overlapping regions. |
| `type_id` | INTEGER | Yes | FK `poi_type_enum` (§2.3) |
| `name` | TEXT | No | Display name |
| `properties` | TEXT (JSON) | No | Free-form bag of the source feature's remaining attributes (e.g. raw S-57 fields not otherwise normalized into a column), producer-defined shape — consumers should treat it as opaque/display-only, not depend on specific keys existing. |
| `lat` | REAL | Yes | Latitude |
| `lon` | REAL | Yes | Longitude |
| `region_id` | INTEGER | Yes | FK `metadata.id` |
| `source_tier` | INTEGER | No, default `1` | FK to the domain in §2.4. |
| `source_id` | INTEGER | No | FK to `data_sources.id`. |

### 2.11 `override_provenance`

One row per tier-5 correction, keeping the hot-path `nodes`/`edges`/`pois`
tables lean while making provenance queryable and auditable.

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | INTEGER PRIMARY KEY | Yes | |
| `entity_type` | TEXT | Yes | `node`, `edge`, or `poi`. |
| `entity_ref` | TEXT | Yes | `nodes.id` / `pois.id` as a string, or `"{source}:{target}"` for an edge. |
| `reason` | TEXT | Yes | Why the override exists. |
| `evidence` | TEXT | Yes | Citation — satellite imagery date, Notice to Mariners reference, OSM changeset ID, local knowledge source. |
| `contributor` | TEXT | Yes | Who authored the fix (human or `"agent:<name>"`). |
| `reviewer` | TEXT | Yes | Who approved it (a human; required — see the community override workflow in `signalk-router-pipeline`'s README). |
| `date` | TEXT | Yes | ISO 8601 date of approval. |
| `source_pr_url` | TEXT | No | Link to the merged PR for full audit trail. |

## 3. Complete Example (abridged)

```sql
INSERT INTO metadata (id, country, name, schema_version, architecture, license, copyright)
VALUES (1, 'NL', 'Netherlands', 1, 'navmesh-hybrid', 'CC-BY-NC-4.0',
        '© Rijkswaterstaat, © OpenStreetMap contributors, © EMODnet Bathymetry, 2026');
-- id is normally left to AUTOINCREMENT; shown explicitly here only so the
-- nodes/edges rows below can reference region_id = 1 unambiguously.

INSERT INTO data_sources (id, name, source_type, url, license, default_tier)
VALUES
  (1, 'Rijkswaterstaat IENC', 'ienc', 'https://www.rijkswaterstaat.nl', 'RWS terms', 1),
  (2, 'OpenStreetMap',        'osm',  'https://www.openstreetmap.org',  'ODbL-1.0',  3),
  (3, 'EMODnet Bathymetry',   'bathymetry', 'https://emodnet.ec.europa.eu/en/bathymetry', 'EMODnet terms', 4);

INSERT INTO nodes (id, lat, lon, region_id, node_kind_id, source_tier, source_id)
VALUES (123456789012, 51.65315, 3.68437, 1, 2, 1, 1);  -- a supernode from Tier-1 data

INSERT INTO edges (source, target, distance, edge_type_id, edge_kind_id, cost_factor, source_tier, source_id)
VALUES (123456789012, 123456789099, 842.5, 0, 2, 0.8, 1, 1);  -- a Tier-1 lane edge
```

## 4. Loading & Overlay Merge (consumption contract)

1. Load one or more region `.sqlite` files. Node/POI IDs are globally
   unique and self-merge across regions (§§2.7, 2.10).
2. Apply the override overlay **after** all base regions are loaded:
   process `deleted_nodes`/`deleted_edges` tombstones first, then
   add/replace rows. The override authoring format (human-readable files
   reviewed as PRs, per `signalk-router-pipeline`'s README) compiles down
   to this overlay shape at build time — the runtime only ever needs to
   apply one binary overlay per region, regardless of how many individual
   override files contributed to it.
3. A region regenerating from scratch MUST NOT require re-applying
   overrides manually — the overlay is sourced independently and merged
   at load time regardless of which base files are present.

## 5. Routing Cost & Tier Enforcement (normative)

- Tiers 1, 2, 3, 5 are routable by default.
- **Tier 4** edges are routable by default but consumers SHOULD apply an
  additional safety margin beyond the stored `min_depth`/`max_air_draft`
  (recommended: treat the vessel's required clearance as if it were
  configured margin + 0.5 m greater, tunable) since this tier is
  statistically derived, not surveyed — several source bathymetry
  providers' own terms (e.g. GEBCO) explicitly disclaim use for safety of
  navigation.
- **Tier 6** edges MUST NOT be loaded into the routable graph by a
  conformant consumer unless the deployment explicitly opts into an
  experimental mode. They exist in the database only as candidates
  awaiting promotion to tier 5 through the override review workflow.
- A suggested (non-normative) cost multiplier by tier, applied on top of
  `cost_factor`: tiers 1/2/5 → `1.0`, tier 3 → `1.05`, tier 4 → `1.15` —
  so the router prefers a same-length authoritative route over a
  same-length inferred one without making inferred routes unusable.

## 6. Consuming `navmesh_regions` (and the minimum-viable fallback)

A `navmesh_regions` row represents open water with no interior point
graph. Full support:

1. Locate the region(s) a route's start/end (or intermediate leg) falls
   inside via point-in-polygon against `boundary_geometry`.
2. Find the entry/exit triangles by point location within `vertices`/
   `triangles`.
3. Walk `triangle_adjacency` to find the shortest chain of triangles
   ("corridor") from entry to exit (a small Dijkstra/A* over the triangle
   dual graph — cheap, since triangle counts per region are orders of
   magnitude smaller than a grid would require).
4. Run the funnel algorithm (Simple Stupid Funnel Algorithm) over the
   corridor's shared ("portal") edges to produce the exact taut polyline
   through the region.
5. For long-haul search, treat the region as collapsing to direct
   edges between pairs of `boundary_node_ids` with cost = the funnel
   path length between them (computed on demand, or precomputed and
   cached per boundary-node pair if a region is queried often).

**Minimum-viable fallback** (a consumer that hasn't implemented the
funnel algorithm yet): treat every pair of a region's `boundary_node_ids`
as connected by a straight-line edge at that pair's great-circle distance,
provided the straight line stays within `boundary_geometry` (cheap check,
skip the pair otherwise). This degrades route quality/coverage inside that
region but never breaks routing entirely — a partial implementation still
produces a usable, if less optimal, path. A consumer MUST NOT simply skip
`navmesh_regions` it doesn't understand, since that silently disconnects
every open-water crossing in the database.

## 7. Consuming `macro` Edges (hierarchy)

A `macro` edge aggregates a longer underlying path between two
`supernode`-kind nodes. Its `edges` row stores the usual `distance`, plus
the path's **bottleneck** constraints (minimum `min_depth`, minimum
`max_air_draft`, minimum `min_width` across the underlying path — reuse
the existing edge attribute columns, populated with the aggregate rather
than a single-edge value) so a vessel query can filter it exactly like any
other edge before deciding to traverse it. The full underlying geometry
(for rendering) is stored as `width_profile`'s sibling — a `path_geometry`
JSON polyline, or a reference list of underlying node IDs — at the
producer's discretion; this spec only requires that the bottleneck
attributes be present and correct so filtering never needs to expand the
macro edge first. Multiple `macro` rows MAY share the same
`(source, target)` pair to represent Pareto-alternative paths (e.g. a
shallow shortcut vs. a deeper, longer channel) — a consumer evaluating
supernode-to-supernode hops MUST consider all rows for a given pair, not
just the first found, and pick the cheapest one that satisfies the
vessel's constraints.

## 8. Attribution & Licensing (see also `LICENSE-DATA.md`)

Because a single region database can blend several upstream sources with
different terms (e.g. a Tier-1 national ENC plus Tier-3 OpenStreetMap
fill), `metadata.copyright` MUST be a complete, ready-to-display
attribution string covering every `data_sources` row with a non-null
`attribution_text`, and consumers SHOULD surface it in any UI that
displays the map or a generated route. `LICENSE-DATA.md` is the
authoritative per-source-type reference for what each source's terms
actually require (some, like OpenStreetMap's ODbL, carry share-alike
obligations beyond simple attribution) — this spec only requires that the
information be *present and complete* in the file; it does not restate
each source's legal terms.

## 9. Producer / Consumer

**Producer**: any pipeline that emits a schema-compatible SQLite file,
including [signalk-router-pipeline](https://github.com/marcelrv/signalk-router-pipeline).

**Consumer**: any routing engine, including
[SignalK Autoroute](https://github.com/marcelrv/signalk-autoroute), which
must implement at minimum: node/edge/poi loading and ID-based merging
(§§2.7, 2.10), the overlay merge (§4), tier-based routability (§5), and
either full or minimum-viable navmesh-region handling (§6) — a database
containing `navmesh_regions` must never be silently mis-routed by a
conformant consumer.
