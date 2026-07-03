# Unified Tidal and Current Exchange Format (UTCEF) Specification

*   **Format Name:** Unified Tidal and Current Exchange Format (UTCEF)
*   **File Extension:** `.utcef` (e.g., `north_sea_delta.utcef`)
*   **Distribution:** Files are distributed **gzip-compressed** as `.utcef.gz` (e.g., `north_sea_delta.utcef.gz`); decompress to obtain the plain UTF-8 JSON payload described below.
*   **MIME Type:** `application/utcef+json`
*   **Underlying Syntax:** JSON (RFC 8259) / GeoJSON (RFC 7946)

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

### 4.2 Properties Block (`dataset.features[i].properties`)

A UTCEF feature represents a single observation point. Its core parsing behavior is determined by the `prediction_method` parameter.

| Key | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `station_id` | String | **Yes** | Canonical, globally unique ID (e.g., Chart number + identifier). |
| `station_name` | String | No | Localized station name. |
| `prediction_method` | String | **Yes** | Must be one of: `relative_time_offset`, `harmonic_constituents_currents`, or `harmonic_constituents_heights`. |
| `representative_area` | Object | No | A standard GeoJSON `Polygon` mapping the geographical boundary where this station's predictions are highly valid. |

---

## 5. Prediction Method Specifications & Mathematical Formulations

### 5.1 Method: `relative_time_offset`

Used for traditional hourly lookup tables (tidal diamonds) synced to a primary reference port.

#### Properties Block Additions:
*   **`reference_port`** (String, Required): Reference tide height station.
*   **`hours_relative_to`** (String, Required): Must be `"high_water_at_reference_port"`.
*   **`data_unit_speed`** (String, Required): Typically `"knots"` or `"meters_per_second"`.
*   **`interpolation`** (Object, Required): Defines the mathematical algorithm for intermediate lunar days.
*   **`tidal_stream_table`** (Array, Required): A 13-item array representing relative offset hours from `-6` to `+6`.

#### Mathematical Formulation for Interpolation:
Consuming applications calculate the current speed ($V_{current}$) on any day by computing the local **Range Ratio** at the reference port:

$$V_{current} = R_{neap} + (R_{spring} - R_{neap}) \times (\text{RangeRatio} - 0.5) \times 2$$

Where:
*   $R_{spring}$ = The hourly `spring_rate` from the table.
*   $R_{neap}$ = The hourly `neap_rate` from the table.
*   $\text{RangeRatio} = \frac{\text{Today's Daily Tidal Range}}{\text{Mean Spring Tidal Range}}$ at the reference port.

---

### 5.2 Method: `harmonic_constituents_currents`

Used for on-the-fly 2D current vector calculation using astronomical tide harmonic constants.

#### Properties Block Additions:
*   **`data_unit_speed`** (String, Required): Typically `"meters_per_second"`.
*   **`harmonic_constituents`** (Object, Required): Map containing the East-West ($u$) and North-South ($v$) components of velocity for each astronomical constituent.

#### Mathematical Formulation:
To calculate the 2D current vector at a given epoch $t$, calculate the velocity components $u(t)$ and $v(t)$:

$$u(t) = U_0 + \sum_{i} f_i A_{u,i} \cos(V_0(i) + u_i + \omega_i t - g_{u,i})$$

$$v(t) = V_0 + \sum_{i} f_i A_{v,i} \cos(V_0(i) + u_i + \omega_i t - g_{v,i})$$

Where:
*   $U_0, V_0$ = Mean current offsets (residual flow vectors).
*   $A_{u,i}, A_{v,i}$ = Amplitudes of the $i$-th constituent in the $u$ and $v$ directions.
*   $g_{u,i}, g_{v,i}$ = Phase lags (in degrees) for the $u$ and $v$ directions.
*   $f_i, (V_0 + u_i), \omega_i$ = Astronomical nodal corrections, initial phases, and angular frequencies of constituent $i$ at epoch $t$ (derived from standard celestial mechanics).

The speed and heading (direction of set) are then computed as:

$$\text{Speed} = \sqrt{u(t)^2 + v(t)^2}$$

$$\text{Direction (set)} = \left( 90 - \text{atan2}(v(t), u(t)) \right) \pmod{360}$$

---

### 5.3 Method: `harmonic_constituents_heights`

Used to fully replace standard legacy XTide height files with a single geographic, readable JSON entity.

#### Properties Block Additions:
*   **`data_unit_height`** (String, Required): Typically `"meters"` or `"feet"`.
*   **`chart_datum`** (String, Optional): The vertical reference datum that `mean_sea_level` and all predicted heights $h(t)$ are expressed above (e.g., `"LAT"`, `"MLLW"`, `"CD"`, `"MSL"`). **Defaults to `"LAT"`** (Lowest Astronomical Tide) when omitted.
*   **`harmonic_constituents`** (Object, Required): Map containing height amplitude and phase lag parameters for each constituent.

#### Mathematical Formulation:
To calculate the water height $h(t)$ above the Chart Datum at any epoch $t$:

$$h(t) = H_{0} + \sum_{i} f_i A_i \cos(V_0(i) + u_i + \omega_i t - g_i)$$

Where:
*   $H_{0}$ = Mean Sea Level (MSL) height offset above the Chart Datum identified by `chart_datum` (defaults to LAT when the property is absent).
*   $A_i$ = Elevation amplitude of constituent $i$.
*   $g_i$ = Phase lag of constituent $i$ in degrees.
*   $f_i, (V_0 + u_i), \omega_i$ = Standard astronomical corrections and frequencies for constituent $i$ at time $t$.

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
          "station_id": "NL_02_BG",
          "station_name": "Brouwershavensche Gat 02",
          "prediction_method": "relative_time_offset",
          "reference_port": "Vlissingen",
          "hours_relative_to": "high_water_at_reference_port",
          "data_unit_speed": "knots",
          "interpolation": {
            "method": "linear_range_ratio",
            "formula": "rate = neap_rate + (spring_rate - neap_rate) * (range_ratio - 0.5) * 2"
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
          "station_id": "NL_03_OFF",
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
          "station_id": "NL_PORT_VLI",
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
