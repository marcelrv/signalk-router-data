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
[SignalK RouteIQ](https://github.com/marcelrv/signalk-routeiq) is the
reference implementation, not the only one.

## 1. Schema Version

This document specifies **`metadata.schema_version = 3`**. Each version
adds to the one before; nothing was removed or renamed:

| `schema_version` | Adds |
|---|---|
| `1` | The initial format: the tables in §2.1–§2.11. |
| `2` | `metadata.depth_unknown_sentinel` (§2.1). A file that sets it uses that value (the reference producer writes `-999.0`) for "depth unknown" in `edges.min_depth` and `nodes.node_depth`, and writes any other negative depth as it is charted: a drying height (e.g. `-2.0` = dries 2 m). |
| `3` | The `notices`, `edge_notices` and `node_notices` tables (§2.12): charted hazards and restricted areas linked to the edges and nodes they affect. A producer that builds without hazard data writes `2`. Version 3 files may also hold the edge-sequence and water-body tables (§2.13), with `edges.edge_sequence_id`, `edges.depth_quality_id` and `metadata.edge_sequence_params`; they are optional. |

- Consumers must not infer behaviour from the number. Read
  `depth_unknown_sentinel` itself to interpret negative depths: when it is
  absent or `NULL`, every negative depth means unknown (the convention
  before version 2, which floored drying heights to `0`). Look for the
  §2.12 and §2.13 tables themselves rather than for `schema_version >= 3`. Some early
  builds wrote `3` before either addition existed.

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
| `schema_version` | INTEGER | Yes | `1`–`3`, per §1 |
| `depth_unknown_sentinel` | REAL | No (version 2+) | The value that means "depth unknown" in `edges.min_depth` and `nodes.node_depth`; the reference producer writes `-999.0`. `NULL` or absent: any negative depth is unknown. See §1. |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Yes | Unique per-region ID. Referenced by `nodes.region_id`, `edges` (indirectly via node), `pois.region_id`, and `navmesh_regions.region_id` — despite the FK columns on those tables being named `region_id`, the column on `metadata` itself is `id`, not `region_id`. |
| `license` | TEXT | No | Data-sharing terms for this specific compiled region (e.g. `"CC-BY-NC-4.0"`). Falls back to the repository-wide default in `LICENSE-DATA.md` when absent. |
| `copyright` | TEXT | No | Attribution statement to surface in a consuming UI (e.g. "© NOAA, © OpenStreetMap contributors, 2026"). |
| `architecture` | TEXT | No | See §1. |
| `edge_sequence_params` | TEXT (JSON) | No (§2.13) | How the derived `edge_sequences` columns were computed from the edges, e.g. `{"distance": "sum", "min_depth": "min of known", "max_air_draft": "min", "min_width": "min", "cost": "sum of distance * cost_factor per direction", "crosses_obstacle": "any"}`. |
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
| `node_depth` | REAL | No, default `-1` | Charted depth at this node in metres; negative = drying height when `metadata.depth_unknown_sentinel` is set. Unknown: the sentinel value, or `-1` in files without it. Distinct from an edge's `min_depth` (§2.8), which is the shallowest point *along* an edge, not at either endpoint. |
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
| `min_depth` | REAL | No, default `99.0` | Shallowest charted depth (metres) anywhere along the edge — the primary draft-constraint gate. `99.0` means "not sampled against charted depth data," not "confirmed 99m deep"; a consumer should not treat the default as a safety guarantee. Negative values and "unknown" follow `metadata.depth_unknown_sentinel` (§1). |
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
| `edge_sequence_id` | INTEGER | No (§2.13) | FK `edge_sequences.id`: the sequence this edge belongs to. Both direction rows carry the same value. |
| `depth_quality_id` | INTEGER | No, default `0` (§2.13) | FK `depth_quality_enum.id`: how `min_depth` was obtained. Description only, never a routing cost. |
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
| `id` | INTEGER | Yes | Deterministic: the first 13 hex digits of the MD5 of `"{poi_type}_{round(lat,5)}_{round(lon,5)}"`, read as an integer (52 bits, so it is exact in a JavaScript number). `poi_type` is the `poi_type_enum` name, e.g. `bridge`. The same POI gets the same id in every database; `INSERT OR IGNORE` handles duplicates across overlapping regions. |
| `type_id` | INTEGER | Yes | FK `poi_type_enum` (§2.3) |
| `name` | TEXT | No | Display name |
| `properties` | TEXT (JSON object) | No | The keys in §2.10.1, which a consumer may use for routing behaviour, plus any other attributes of the source feature (producer-defined, display-only). Every key is optional: a consumer must work when any of them is missing. |
| `lat` | REAL | Yes | Latitude |
| `lon` | REAL | Yes | Longitude |
| `region_id` | INTEGER | Yes | FK `metadata.id` |
| `source_tier` | INTEGER | No, default `1` | FK to the domain in §2.4. |
| `source_id` | INTEGER | No | FK to `data_sources.id`. |

#### 2.10.1 Recognised `properties` keys

Rule for every key: **unknown = key absent**. A producer never writes `null`,
`""`, `0` or a `999` placeholder for a value it does not know. A consumer
ignores a key whose value has the wrong type or is out of range, as if it
were absent.

| Key | POI type | Type | Meaning |
|---|---|---|---|
| `subtype` | bridge | string | `"opening"` (a span that opens: bascule, swing, lift, …) or `"fixed"`. |
| `height` | bridge, `fixed` only | number, metres | Vertical clearance under the fixed span (S-57 `VERCLR`). For display: the routing limit is on the edges (`edges.max_air_draft`). |
| `closed_height` | bridge, `opening` only | number, metres, > 0 | Vertical clearance under the span while it is closed (S-57 `VERCCL`). Lets a consumer skip the wait for a vessel that fits under the closed span. Never an air-draft limit. |
| `typical_wait_minutes` | bridge, lock | number, minutes, >= 0 | Typical wait at this bridge or lock; overrides the consumer's default. |
| `structure` | bridge | string, non-empty | Identifies one physical structure: every span of the same bridge carries the same value, and no other structure in the database uses it. A route that passes a structure is one crossing, reported as the span it goes through. |

Notes on `structure`:

- Needed where the source names each span on its own ("Span 1 (fixed) X",
  "Span 2 (lift) X"), so the names do not show which spans belong
  together. Where it is absent a
  consumer groups spans by `name`, which suits S-57, where all spans of one
  bridge share `OBJNAM`.
- The reference producer writes the lowest `pois.id` among the structure's
  spans, as a decimal string. Since `pois.id` is derived from position, the
  value is the same in every database that holds the whole structure.
  Consumers must treat it as an opaque string and compare it only for
  equality.

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

### 2.12 `notices`, `edge_notices`, `node_notices` (version 3)

Charted hazards (wrecks, obstructions, rocks) and restricted areas, kept as
information a consumer can show or act on rather than baked into edge costs.
The reference producer's extraction and severity rules are in
[`docs/SPEC-HAZARDS-AND-NOTICES.md`](https://github.com/marcelrv/signalk-router-pipeline/blob/main/docs/SPEC-HAZARDS-AND-NOTICES.md)
of signalk-router-pipeline.

`notices`, one row per charted object:

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | INTEGER PRIMARY KEY | Yes | Deterministic (first 13 hex digits of an MD5 of class, chart reference and geometry, as an integer), so one chart object has the same id in every database. |
| `source_class` | TEXT | Yes | S-57 object class, e.g. `RESARE`, `WRECKS`, `OBSTRN`. |
| `source_ref`, `source_cell` | TEXT | No | S-57 `LNAM` and cell file name, for tracing back to the chart. |
| `severity` | INTEGER | Yes | `0` info, `1` caution, `2` depth, `3` prohibited. |
| `depth_m` | REAL | No | Least depth behind a severity-2 notice; `NULL` otherwise or unknown. |
| `name`, `text` | TEXT | No | Charted name and information text, as charted. |
| `period_start`, `period_end` | TEXT | No | Recurring window, raw S-57 (`'--1101'`..`'--0515'`; a start later in the year than the end wraps over New Year). |
| `valid_from`, `valid_until` | TEXT | No | The object's validity, raw S-57 dates; `NULL` = open. |
| `period_source` | TEXT | No | `'chart'`, `'text-reviewed'`, or `NULL` (no time information). |
| `text_review` | INTEGER | Yes | `1`: a prohibited area whose text mentions times or conditions that have not been reviewed; it stays prohibited. |
| `attributes` | TEXT (JSON) | Yes | Every non-empty S-57 attribute. |
| `geometry` | TEXT (GeoJSON) | Yes | For drawing. |
| `min_lat`, `min_lon`, `max_lat`, `max_lon` | REAL | No | Bounding box. |

`edge_notices (source, target, notice_id, severity, reason)` links a notice
to an edge, one row per direction, with the severity that applies to that
edge and an optional `reason` (e.g. `'seasonal'`, `'boundary graze'`).
`node_notices (node_id, notice_id, severity)` links a notice to a node.

A notice is active on a date inside `[valid_from, valid_until]` and, when a
period is set, inside that window. An edge whose only severity-3 links are
time-limited is closed inside the window and open outside it; a permanent
severity-3 link normally also sets `edges.crosses_obstacle` (the reference
producer leaves it off on opening-bridge and lock edges).

### 2.13 Edge sequences and water bodies (optional, version 3)

Which water an edge lies in, how its line was made and how its depth was
obtained, so a consumer can describe a route by the waters it runs through
("23 km on one canal, then across a lake", by name) and warn about depths
that were never surveyed. None of it is a routing input: the edge columns a
router loads are unchanged, and a consumer reads these tables only for the
edges of a route it has found.

Rules:

- **Descriptions, not costs.** `water_body_kind_enum` and
  `depth_quality_enum` must never be used as a routing cost or preference.
- **Extendable enums.** Producers may append enum ids; a consumer reads an
  id it does not know as `other` (`unknown` for depth quality). Ids are
  never renumbered.
- **Stable ids.** Edge sequence and water body ids are at most 53 bits and
  are the same in every database for the same thing, so databases can be
  merged. A merge must check that an id it already has means the same thing
  (a sequence: the same end nodes; a water body: the same name and kind)
  and fail otherwise.

`edge_sequences`, an unbranched chain of edges between two break nodes:

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | INTEGER PRIMARY KEY | Yes | 52-bit hash of the end nodes and the first edge. |
| `node_a`, `node_b` | INTEGER | Yes | End nodes, `node_a < node_b`. |
| `water_body_id` | INTEGER | No | FK `water_bodies.id`; `NULL` when the water has no name. |
| `origin_id` | INTEGER | Yes, default `0` | FK `origin_enum.id`: how the line was made. |
| `distance`, `min_depth`, `depth_quality_id`, `max_air_draft`, `min_width`, `crosses_obstacle` | | No | Derived from the sequence's edges, per `metadata.edge_sequence_params`. |
| `cost_ab`, `cost_ba` | REAL | No | Cost from `node_a` to `node_b` and back. |

A sequence ends at every node whose degree is not 2, where one-way
behaviour changes, and where the water body or origin changes. A producer
may break sequences elsewhere too; a consumer must not read meaning into a
break beyond "a sequence ends here". Locks and bridges do not break a
sequence.

`water_bodies`, a named water (canal, river, lake, sea area, channel,
harbour basin):

| Column | Type | Required | Description |
|---|---|---|---|
| `id` | INTEGER PRIMARY KEY | Yes | Assigned once by the producer's gazetteer and kept; never derived from the name, so a rename does not change it. One water across a border (a river through several countries) has one id, with its names in each language. |
| `name` | TEXT | Yes | Default name, normally the local official name. |
| `kind_id` | INTEGER | No | FK `water_body_kind_enum.id`. |
| `parent_id` | INTEGER | No | FK `water_bodies.id`, e.g. a channel inside a lake. |
| `country` | TEXT | No | ISO 3166-1 alpha-2; `NULL` for a water shared by several countries. |

`water_body_names (water_body_id, lang, name, kind)`, primary key
`(water_body_id, lang, name)`: every name of a water body. `lang` is a
BCP-47 tag (`en`, `fr`, `de-CH`, …); `kind` is `official` (default),
`alternative`, `short` or `historic`. To name a water in a language, take
an `official` name in that language, then `alternative`, then `short`; then
the same for the base language (`de` for `de-CH`); then `water_bodies.name`.

Enums, with the values defined so far:

| Table | Values |
|---|---|
| `origin_enum (id, description)` | `0` other, `1` pipeline (generated), `2` imported (an outside axis copied as is), `3` agent (drawn by an AI agent), `4` manual (drawn or edited by a person), `5` community |
| `depth_quality_enum (id, description, rank)` | `0` unknown (rank 0), `1` community (10), `2` class_norm (20, a fairway-class draught norm, never surveyed), `3` published_minimum (30, a published minimum along the fairway), `4` charted (40, chart depth areas or soundings), `5` surveyed (35), `6` survey_raster (32), `7` bathymetry_model (25) |
| `water_body_kind_enum (id, description)` | `0` other, `1` canal, `2` river, `3` lake, `4` channel, `5` sea_area, `6` harbour, `7` estuary |

`rank` orders depth qualities by trust (higher = more trusted). Compare
ranks, never ids.

## 3. Complete Example (abridged)

```sql
INSERT INTO metadata (id, country, name, schema_version, depth_unknown_sentinel, architecture, license, copyright)
VALUES (1, 'US', 'Chesapeake Bay', 3, -999.0, 'navmesh-hybrid', 'CC-BY-NC-4.0',
        '© NOAA, © OpenStreetMap contributors, © GEBCO, 2026');
-- id is normally left to AUTOINCREMENT; shown explicitly here only so the
-- nodes/edges rows below can reference region_id = 1 unambiguously.

INSERT INTO data_sources (id, name, source_type, url, license, default_tier)
VALUES
  (1, 'NOAA ENC',      'enc',        'https://nauticalcharts.noaa.gov', 'Public domain', 1),
  (2, 'OpenStreetMap', 'osm',        'https://www.openstreetmap.org',   'ODbL-1.0',      3),
  (3, 'GEBCO',         'bathymetry', 'https://www.gebco.net',           'GEBCO terms',   4);

INSERT INTO nodes (id, lat, lon, region_id, node_kind_id, source_tier, source_id)
VALUES (123456789012, 38.97842, -76.48163, 1, 2, 1, 1);  -- a supernode from Tier-1 data

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
[SignalK RouteIQ](https://github.com/marcelrv/signalk-routeiq), which
must implement at minimum: node/edge/poi loading and ID-based merging
(§§2.7, 2.10), the overlay merge (§4), tier-based routability (§5), and
either full or minimum-viable navmesh-region handling (§6) — a database
containing `navmesh_regions` must never be silently mis-routed by a
conformant consumer.
