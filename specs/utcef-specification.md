# Unified Tidal and Current Exchange Format (UTCEF) Specification

*   **Format Name:** Unified Tidal and Current Exchange Format (UTCEF)
*   **File Extension:** `.utcef` (e.g., `north_sea_delta.utcef`)
*   **Container / Distribution:** A `.utcef` file **is itself a standard ZIP archive** (PKZIP, DEFLATE compression) — the same "zip-with-a-known-inside" approach used by `.apk`, `.docx`, `.jar` and `.odt`. It contains a **single UTF-8 JSON payload member** whose name is `<basename>.json` (e.g. `north_sea_delta.utcef` contains `north_sea_delta.json`). Because it is an ordinary ZIP, it opens on virtually any computer by simply renaming the extension to `.zip`. Consuming engines extract the first `*.json` member and parse the JSON payload described below.
    *   **Reader robustness:** engines SHOULD detect the container by leading **magic bytes** (`50 4B` = ZIP) rather than by extension, and MAY additionally accept an uncompressed `.utcef` (raw JSON, first byte `{`) or the deprecated gzip form `.utcef.gz` (magic `1F 8B`) for backward compatibility.
*   **MIME Type:** `application/utcef+zip` (the payload member is `application/json`)
*   **Underlying Syntax:** JSON (RFC 8259) / GeoJSON (RFC 7946), packaged in a ZIP (PKWARE APPNOTE / ISO/IEC 21320-1) container

---

## 1. Introduction & Design Goals

The **Unified Tidal and Current Exchange Format (UTCEF)** is an open, machine-readable, lightweight format designed to store and exchange both static and dynamic tidal data. 

To bridge the gap between historical tabular lookup data and modern astronomical predictions, UTCEF supports three distinct prediction paradigms in a single file:
1.  **`relative_time_offset`**: Traditional "tidal stream panel" lookup tables (tidal diamonds) relative to a reference port's High Water.
2.  **`harmonic_constituents_currents`**: Complete 2D vector coordinates ($u$ and $v$ parameters) allowing full on-the-fly mathematical current velocity predictions.
3.  **`harmonic_constituents_heights`**: Astronomical harmonic parameters enabling on-the-fly water level (tide height) predictions, fully replacing legacy files like XTide `.tcd`.

To maintain strict compatibility with GIS ecosystems, the file uses a wrapped GeoJSON architecture. Geographic parsing libraries can read the payload as standard GeoJSON, while marine prediction engines can leverage the custom mathematical properties nested within.

---

## 2. Schema Versioning

UTCEF separates the version of the data file's structural layout from the version of the database's scientific values:

*   **`schema_version` (Semantic Versioning 2.0.0):** Declared in the top-level metadata.
    *   **Major updates (e.g., `1.0.0` to `2.0.0`)** denote breaking structural changes (e.g., key removals, changed data types). Consuming engines must reject unsupported major versions to prevent parser crashes.
    *   **Minor updates (e.g., `1.0.0` to `1.1.0`)** denote backward-compatible expansions (e.g., new optional fields).
    *   **Patch updates (e.g., `1.0.0` to `1.0.1`)** denote purely administrative metadata updates.
*   **`dataset_version`:** A unique identifier denoting the scientific revision of the underlying data points, updated whenever hydrographic surveys or model predictions are re-run.

---

## 3. Top-Level Structure

Every UTCEF file is structured as a single JSON object containing two main keys:

```json
{
  "metadata": {
    "schema_version": "1.0.0",
    "dataset_version": "2026.07.03",
    ...
  },
  "dataset": {
    "type": "FeatureCollection",
    "features": [ ... ]
  }
}
```

---

## 4. Key Schema References

### 4.1 The `metadata` Block

| Key | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `schema_version` | String | **Yes** | Standard SemVer for structural tracking (e.g., `"1.0.0"`). |
| `dataset_version` | String | **Yes** | Database content release tag. |
| `last_updated` | String | **Yes** | ISO 8601 UTC timestamp of creation/revision. |
| `title` | String | **Yes** | Descriptive name for user interfaces. |
| `description` | String | No | A short summary of the dataset scope. |
| `region` | Object | **Yes** | Geographic bounds including name and a 4-value bounding box `[min_lon, min_lat, max_lon, max_lat]`. |
| `data_sources` | Array | **Yes** | List of source organizations, agencies, or models (e.g., FES2014, UKHO). |
| `copyright` | String | **Yes** | Legal copyright/attribution statement. |
| `license` | String | **Yes** | Data-sharing terms (e.g., `"CC-BY-4.0"`, `"Proprietary"`). |
| `catalog` | Object | No | Optional, non-normative hints for catalog builders (ignored by prediction engines): `source` (provider code, e.g. `"noaa"`), `contributor`, `url` and `tags` for the generated catalog entry. Used by `scripts/tide-current/sources/utcef_regions.py`; absent → the collector's historical FES2014 defaults apply. |

### 4.2 Feature Identity & Properties Block (`dataset.features[i]`)

A UTCEF feature represents a single observation point. Its core parsing behavior is determined by the `prediction_method` parameter.

**Station identity.** The top-level GeoJSON **`Feature.id`** (a sibling of `geometry` and `properties`) is the **canonical, globally unique station identifier** and is **required**. All cross-references between features (e.g. a `reference_port`) resolve against `Feature.id`. The legacy `properties.station_id` is retained only as an **optional alias**; when both are present they MUST be identical.

**Direction convention.** Every bearing in a UTCEF file — current set (`direction`), flood/ebb axes, etc. — is expressed in **degrees true (°T)**, measured clockwise from true north (`0°` = N, `90°` = E), never magnetic.

| Key | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `station_id` | String | No | Optional alias of `Feature.id`; if present, MUST equal `Feature.id`. |
| `station_name` | String | No | Localized station name. |
| `prediction_method` | String | **Yes** | Must be one of: `relative_time_offset`, `harmonic_constituents_currents`, or `harmonic_constituents_heights`. |
| `representative_area` | Object | No | A standard GeoJSON `Polygon` mapping the geographical boundary where this station's predictions are highly valid. |

---

## 5. Prediction Method Specifications & Mathematical Formulations

### 5.0 Common Conventions (Normative)

The following conventions apply to **all** harmonic prediction methods (§5.2, §5.3) and MUST be honored by consuming engines. Ignoring them produces predictions shifted by hours.

*   **Time base:** The astronomical time argument $t$ (every $\omega_i t$ term) is evaluated strictly in **Coordinated Universal Time (UTC)**, as elapsed decimal hours from the astronomical epoch. Local or zone time is never used in the harmonic sum.
*   **Phase lags:** All phase values (`phase_g`, `u_phase_g`, `v_phase_g`) are **Greenwich phase lags** ($g$) — referenced to the Greenwich meridian — in degrees, `0°`–`360°`. They are *not* local (station-meridian) phase lags. This matches modern global models such as FES2014.
*   **Constituent catalog:** Constituent names (`M2`, `S2`, `K1`, …), their angular frequencies (speeds $\omega_i$), and the astronomical nodal corrections ($f_i$, $V_0(i) + u_i$) conform to the **IHO Standard List of Tidal Constituents** (IHO SP-13; Schureman, *Manual of Harmonic Analysis and Prediction of Tides*, 1958). These are derived internally by the engine and are **not** stored in the file.
*   **Direction:** All bearings are **degrees true (°T)**, clockwise from true north.

### 5.1 Method: `relative_time_offset`

Used for traditional hourly lookup tables (tidal diamonds) synced to a primary reference port.

#### Properties Block Additions:
*   **`reference_port`** (String, Required): The `Feature.id` of the reference tide-height station (a `harmonic_constituents_heights` feature) whose High Water this table is offset from.
*   **`hours_relative_to`** (String, Required): Must be `"high_water_at_reference_port"`.
*   **`data_unit_speed`** (String, Required): Typically `"knots"` or `"meters_per_second"`.
*   **`interpolation`** (Object, Required): Selects the interpolation algorithm for intermediate lunar days. Only the **`method`** key is normative and MUST be one of a fixed enum (currently: `"linear_range_ratio"`). Any **`formula`** / `note` string is **documentation-only (non-normative)** — see the security note below.
*   **`tidal_stream_table`** (Array, Required): A 13-item array of hourly entries for relative offset hours `-6` to `+6`. Each entry carries `direction` (set, **degrees true**), `spring_rate`, and `neap_rate` (in `data_unit_speed`).

> **⚠ Security (normative):** The `formula` string is human-readable documentation of the algorithm named by `method`. Consuming engines **MUST NOT** evaluate, `eval()`, or otherwise execute it. Engines implement each `method` enum value natively and reject files whose `method` they do not recognize.

#### Mathematical Formulation for Interpolation (`method: "linear_range_ratio"`):
Consuming applications calculate the current speed ($V_{current}$) on any day by computing the local **Range Ratio** at the reference port:

$$V_{current} = R_{neap} + (R_{spring} - R_{neap}) \times (\text{RangeRatio} - 0.5) \times 2$$

Where:
*   $R_{spring}$ = The hourly `spring_rate` from the table.
*   $R_{neap}$ = The hourly `neap_rate` from the table.
*   $\text{RangeRatio} = \frac{\text{Today's Daily Tidal Range}}{\text{Mean Spring Tidal Range}}$ at the reference port.

---

### 5.2 Method: `harmonic_constituents_currents`

Used for on-the-fly 2D current vector calculation using astronomical tide harmonic constants. All time, phase, and constituent conventions of §5.0 apply.

#### Properties Block Additions:
*   **`data_unit_speed`** (String, Required): Typically `"meters_per_second"`.
*   **`mean_offset`** (Object, Optional): Structural (non-tidal) residual current offset. Contains `u_residual` (eastward, $U_{residual}$) and `v_residual` (northward, $V_{residual}$), both in the unit given by `data_unit_speed`. Each component defaults to `0` when omitted.
*   **`harmonic_constituents`** (Object, Required): Map keyed by constituent name; each value carries the East-West ($u$) and North-South ($v$) amplitude and **Greenwich** phase lag of that constituent (`u_amplitude`, `u_phase_g`, `v_amplitude`, `v_phase_g`).

#### Mathematical Formulation:
To calculate the 2D current vector at epoch $t$ (evaluated in **UTC**, using **Greenwich phase lags** — see §5.0), compute the velocity components $u(t)$ and $v(t)$:

$$u(t) = U_{residual} + \sum_{i} f_i A_{u,i} \cos\big(V_0(i) + u_i + \omega_i t - g_{u,i}\big)$$

$$v(t) = V_{residual} + \sum_{i} f_i A_{v,i} \cos\big(V_0(i) + u_i + \omega_i t - g_{v,i}\big)$$

Where:
*   $t$ = Time in **UTC**, as elapsed decimal hours from the astronomical epoch.
*   $U_{residual}, V_{residual}$ = Mean residual current offsets (eastward and northward) from `mean_offset` (`0` when absent).
*   $A_{u,i}, A_{v,i}$ = Amplitudes of the $i$-th constituent in the $u$ and $v$ directions.
*   $g_{u,i}, g_{v,i}$ = **Greenwich phase lags** (degrees) for the $u$ and $v$ directions.
*   $f_i, \big(V_0(i) + u_i\big), \omega_i$ = Nodal correction factor, Greenwich equilibrium argument, and angular frequency (speed) of constituent $i$, per the IHO constituent catalog (§5.0).

The speed and heading (direction of set, in **degrees true**) are then computed as:

$$\text{Speed} = \sqrt{u(t)^2 + v(t)^2}$$

$$\text{Direction (set)} = \left( 90 - \text{atan2}(v(t), u(t)) \right) \pmod{360}$$

---

### 5.3 Method: `harmonic_constituents_heights`

Used to fully replace standard legacy XTide height files with a single geographic, readable JSON entity. All time, phase, and constituent conventions of §5.0 apply.

#### Properties Block Additions:
*   **`data_unit_height`** (String, Required): Typically `"meters"` or `"feet"`.
*   **`chart_datum`** (String, Optional): The vertical reference datum that `mean_sea_level` and all predicted heights $h(t)$ are expressed above (e.g., `"LAT"`, `"MLLW"`, `"CD"`, `"MSL"`). **Defaults to `"LAT"`** (Lowest Astronomical Tide) when omitted.
*   **`harmonic_constituents`** (Object, Required): Map keyed by constituent name; each value carries the elevation `amplitude` and **Greenwich** phase lag `phase_g` of that constituent.

#### Mathematical Formulation:
To calculate the water height $h(t)$ above the Chart Datum at epoch $t$ (evaluated in **UTC**, using **Greenwich phase lags** — see §5.0):

$$h(t) = H_{0} + \sum_{i} f_i A_i \cos\big(V_0(i) + u_i + \omega_i t - g_i\big)$$

Where:
*   $H_{0}$ = Mean Sea Level (MSL) height offset above the Chart Datum identified by `chart_datum` (defaults to LAT when the property is absent).
*   $A_i$ = Elevation amplitude of constituent $i$.
*   $g_i$ = **Greenwich phase lag** of constituent $i$ in degrees.
*   $f_i, \big(V_0(i) + u_i\big), \omega_i$ = Nodal correction factor, Greenwich equilibrium argument, and angular frequency of constituent $i$, per the IHO constituent catalog (§5.0).

---

## 6. Complete `.utcef` Reference File Implementation

Below is a structurally complete `.utcef` implementation demonstrating how all three methods cleanly coexist within a single standardized spatial database.

```json
{
  "metadata": {
    "schema_version": "1.0.0",
    "dataset_version": "2026.07.03",
    "title": "UTCEF Validation Dataset - Southern North Sea Delta",
    "description": "Unified database illustrating three distinct tidal prediction methodologies in a single file schema.",
    "last_updated": "2026-07-03T11:45:00Z",
    "region": {
      "name": "Netherlands SW Delta & Approaches",
      "bbox": [3.1024, 51.1256, 4.8951, 53.4851]
    },
    "data_sources": [
      {
        "name": "Rijkswaterstaat Hydrographic Service",
        "url": "https://www.rijkswaterstaat.nl"
      },
      {
        "name": "FES2014 Global Tidal Model",
        "details": "Processed derivative coordinates used for validation"
      }
    ],
    "copyright": "Copyright © 2026 OceanData Project.",
    "license": "CC-BY-4.0"
  },
  "dataset": {
    "type": "FeatureCollection",
    "features": [
      {
        "type": "Feature",
        "id": "NL_02_BG",
        "geometry": {
          "type": "Point",
          "coordinates": [3.68437, 51.65315]
        },
        "properties": {
          "station_name": "Brouwershavensche Gat 02",
          "prediction_method": "relative_time_offset",
          "reference_port": "NL_PORT_VLI",
          "hours_relative_to": "high_water_at_reference_port",
          "data_unit_speed": "knots",
          "interpolation": {
            "method": "linear_range_ratio",
            "formula": "rate = neap_rate + (spring_rate - neap_rate) * (range_ratio - 0.5) * 2",
            "note": "The 'formula' field is informational only. Consuming engines MUST NOT execute this string; they implement calculations natively based on the 'method' key."
          },
          "representative_area": {
            "type": "Polygon",
            "coordinates": [
              [
                [3.50, 51.75],
                [3.85, 51.75],
                [3.85, 51.55],
                [3.50, 51.55],
                [3.50, 51.75]
              ]
            ]
          },
          "tidal_stream_table": [
            {"hour": -6, "direction": 296, "spring_rate": 1.4, "neap_rate": 0.7},
            {"hour": -5, "direction": 294, "spring_rate": 0.6, "neap_rate": 0.1},
            {"hour": -4, "direction": 123, "spring_rate": 0.2, "neap_rate": 0.5},
            {"hour": -3, "direction": 117, "spring_rate": 0.8, "neap_rate": 0.8},
            {"hour": -2, "direction": 115, "spring_rate": 1.0, "neap_rate": 0.9},
            {"hour": -1, "direction": 109, "spring_rate": 1.5, "neap_rate": 1.0},
            {"hour": 0,  "direction": 101, "spring_rate": 1.2, "neap_rate": 0.7},
            {"hour": 1,  "direction": 100, "spring_rate": 0.8, "neap_rate": 0.1},
            {"hour": 2,  "direction": 326, "spring_rate": 0.2, "neap_rate": 0.8},
            {"hour": 3,  "direction": 298, "spring_rate": 1.4, "neap_rate": 1.3},
            {"hour": 4,  "direction": 295, "spring_rate": 1.9, "neap_rate": 1.4},
            {"hour": 5,  "direction": 296, "spring_rate": 1.9, "neap_rate": 1.3},
            {"hour": 6,  "direction": 295, "spring_rate": 1.6, "neap_rate": 1.0}
          ]
        }
      },
      {
        "type": "Feature",
        "id": "NL_03_OFF",
        "geometry": {
          "type": "Point",
          "coordinates": [3.2145, 51.9821]
        },
        "properties": {
          "station_name": "Zeeland Offshore 03",
          "prediction_method": "harmonic_constituents_currents",
          "data_unit_speed": "meters_per_second",
          "mean_offset": {
            "u_residual": 0.012,
            "v_residual": -0.004
          },
          "harmonic_constituents": {
            "M2": {
              "u_amplitude": 0.452, "u_phase_g": 112.4,
              "v_amplitude": 0.121, "v_phase_g": 345.1
            },
            "S2": {
              "u_amplitude": 0.151, "u_phase_g": 145.2,
              "v_amplitude": 0.042, "v_phase_g": 12.3
            },
            "K1": {
              "u_amplitude": 0.052, "u_phase_g": 85.1,
              "v_amplitude": 0.018, "v_phase_g": 264.3
            }
          }
        }
      },
      {
        "type": "Feature",
        "id": "NL_PORT_VLI",
        "geometry": {
          "type": "Point",
          "coordinates": [3.5681, 51.4422]
        },
        "properties": {
          "station_name": "Vlissingen Reference Port",
          "prediction_method": "harmonic_constituents_heights",
          "data_unit_height": "meters",
          "chart_datum": "LAT",
          "mean_sea_level": 1.95,
          "harmonic_constituents": {
            "M2": {
              "amplitude": 1.745,
              "phase_g": 105.8
            },
            "S2": {
              "amplitude": 0.512,
              "phase_g": 154.2
            },
            "K1": {
              "amplitude": 0.114,
              "phase_g": 62.4
            }
          }
        }
      }
    ]
  }
}
```
