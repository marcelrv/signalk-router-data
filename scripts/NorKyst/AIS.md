# AIS traffic-density sources for leisure-boat point placement (Norway)

Checked 2026-11-22. All claims below were read on the pages cited; "not stated" means the page did not say.

## Comparison

| | Kystverket density grid (KYV/BW) | Kystverket HAIS / Kystdatahuset API | EMODnet Human Activities vessel density |
|---|---|---|---|
| Leisure category | Not discoverable: record says WMS is filterable by "ship group, length, GT" but capabilities list no ship-type dimension; and recreational craft <45 m are excluded from open AIS | Only via closed/authorised access; open data excludes recreational <45 m | Yes: 4 Sailing, 5 Pleasure Craft (separate layers) |
| Resolution | 1x1 km (record); "0.01 dd" for MarTraf layers | Raw positions | 1x1 km, hours/km2/month |
| Years | Record: monthly updates; WMS time: 2011-2021 (raster), 2020-01..2025-04 monthly / 2020..2025 yearly (MarTraf) | Full-res from 2005 (good from 2010/2016) | 2017-2024, monthly + annual average |
| Format | WMS 1.3.0 only (GetMap incl. image/tiff, but no WCS) | Parquet/GeoParquet/CSV (HAIS order); JSON API | GeoTIFF zips, File GDB, WMS, WCS |
| Access | Open, no login (WMS) | HAIS: order + download link; API: login needed for auth endpoints | Open, no login |
| Licence | NLOD (api swagger states NLOD 1.0; record: "open data", no named licence) | NLOD; recreational data restricted (see below) | CC-BY 4.0 |
| Publish derived? | Yes (NLOD) but unclear for leisure data | Not for recreational/small-fishing data | Yes (CC-BY 4.0) |

## 1. Kystverket traffic density grid

- Record "AIS Trafikktetthetsrutenett": https://www.geonorge.no/geonetworktest/srv/api/records/90f2e393-c145-4039-b36b-36deb335b905 (test-environment record; confirm on kartkatalog.geonorge.no).
- Abstract: 1x1 km grid, cell value = number of voyages through the cell; tracks reconstructed from AIS; WMS filterable by ship group, length and gross tonnage; monthly updates; bbox N72 S57 E33 W2 (covers our area). Cell x 0.7 = approx km sailed.
- WMS: https://kystdatahuset.no/ws/api/raster-frequency/mapserver?map=/etc/mapserver/barentswatch.map&service=WMS&request=GetCapabilities (fetched; WMS 1.3.0, GetMap formats include image/tiff, no WCS). Layers: ais_raster_32633 / _3575 (+_6min), martraf_monthly_density_wms_t (2020-01-01/2025-04-01/P1M), martraf_yearly_density_wms_t (2020..2025). Capabilities expose only a time dimension, no ship-type dimension; the filter mechanism (SLD/sld endpoint `/api/raster-frequency/ext/sld`) is not documented in what I could read, so a leisure category was NOT verified.
- Licence: record gives no named licence, only INSPIRE "no limitations on public access / no conditions apply" and "Åpne data". Swagger of the API (https://kystdatahuset.no/ws/swagger/v1/swagger.json) names "NLOD 1.0" (https://data.norge.no/nlod/en/1.0). Provider/owner: Kystverket. Contact on record: Kystverket.
- Caveat: underlying open AIS excludes recreational craft <45 m and fishing <15 m, so any density from the open pipeline likely under-represents the boats we care about. Unverified whether the density grid is built from the full (closed) data set.

## 2. Kystverket historical AIS

- HAIS record: https://www.geonorge.no/geonetworktest/srv/api/records/6a58c9e5-302c-454b-b229-2a317b1ac902 ; service https://hais.kystverket.no/ ; contact geodatatjenesten@kystverket.no.
- Coverage: Norwegian AIS network (shore + LEO satellite), Norwegian economic zone plus Jan Mayen/Svalbard zones; full resolution from 2005, much better from 2010 and 2016. Filter by area, time, ship, ship type; link delivered after order. Formats Parquet/GeoParquet, CSV. INSPIRE: no limitations on access/use, but "fishing vessels under 15 m and recreational vessels under 45 m are exempt from public disclosure".
- Kystdatahuset API (swagger above; terms https://kystdatahuset.no/artikkel/api-tilgang, SPA page, text not readable by me): open + authorised endpoints; e.g. POST /api/ais/positions/within-bbox-time, /within-geom-time, /for-mmsis-time, /api/ais/static-messages, /api/ais/sailed-distance/* (by ship type/length/GT; municipality level). Auth via kystinfo.no login (JWT).
- Closed access rules (https://www.kystverket.no/en/sea-transport-and-ports/ais/access-to-ais-data/): open AIS under NLOD, no registration, but excludes fishing <15 m and recreational <45 m. Full data needs a registration form with purpose, mainly for authorities/ports; "Data from small fishing vessels and recreational craft is restricted: it may only be used for the stated purpose and cannot be shared with third parties." Request via post@kystverket.no.
- Class B coverage, ship-type field values and summer-season volume: not stated on the pages I could read (not verified). Open BarentsWatch API only gives last 14 days (https://developer.barentswatch.no/docs/AIS/live-ais-api), useless for long-term density.
- Implication: raw leisure positions are the restricted class. Publishing point positions chosen from them is a grey area ("cannot be shared with third parties"); would need written clearance from Kystverket.

## 3. EMODnet Human Activities vessel density

- Catalogue: https://emodnet.ec.europa.eu/geonetwork/srv/api/records/0f2f3ff1-30ef-49e1-96e7-8ca78d58a07c (record revised 2025-12-06).
- 1x1 km grid (EPSG:3035), hours per km2 per month; monthly 2017-2024 plus annual averages; 13 types incl. 4 Sailing, 5 Pleasure Craft, all types. 2024 is sparser: provider lost satellite data from June 2024 (use 2017-2023 / averages).
- Downloads (base https://ows.emodnet-humanactivities.eu/geonetwork/srv/api/records/0f2f3ff1-30ef-49e1-96e7-8ca78d58a07c/attachments/): EMODnet_HA_Vessel_Density_04Avg.zip, _05Avg.zip (annual avg, GeoTIFF), _04.zip, _05.zip (monthly stacks). WMS https://ows.emodnet-humanactivities.eu/wms? and WCS https://ows.emodnet-humanactivities.eu/wcs? , layers vesseldensity_04 / _05 / _04avg / _05avg (confirmed in WMS capabilities).
- Norway coverage: record extent 14.96-86.84 N, -87.7-97.6 E ("EU waters and some neighbouring areas"). Sample WMS GetFeatureInfo on vesseldensity_05avg returned values: ~4.3 h/km2/month at 10.6E 59.85N (Oslofjord) and ~589 at 5.2E 60.4N (Bergen); so data exist in Norwegian waters. Fjord-level completeness and Class B coverage not verified (method PDF https://emodnet.ec.europa.eu/sites/emodnet.ec.europa.eu/files/public/HumanActivities_20231101_VesselDensityMethod.pdf could not be text-extracted).
- Source AIS: CLS, ORBCOMM, vesseltracker.com (terrestrial + satellite); Kalman filter on satellite noise.
- Licence: "Creative Commons CC-BY 4.0". Suggested credit: "data downloaded from the EMODnet Portal (https://emodnet.ec.europa.eu/en/), originator Cogea Srl" (citation id EMODnet_HA_Vessel_DensityMap_20251206). CC-BY 4.0 permits derived products, same licence as our output.

## NLOD 2.0 reminder (https://data.norge.no/nlod/en/2.0/)

Licence grants copy, distribute, modify, combine "for any purpose"; attribution required: "Contains data under the Norwegian licence for Open Government data (NLOD) distributed by [Kystverket]", plus a note if changed; no endorsement use. NLOD is compatible with CC-BY (sect. 9), so derived points can be CC-BY-4.0.

## Caveats

- AIS is mandatory only for larger vessels; most Norwegian leisure boats <15 m carry no transponder or Class B only (type 36 sailing / 37 pleasure, self-set by owners and often unset = "Other/Unknown"). Densities are a biased proxy: skewed to larger yachts, charter fleets, and users of Class B.
- Satellite-dependent density is poor in narrow fjords; terrestrial coverage near coast is better.
- Use density only as a relative weight, combined with e.g. harbours/marinas and fairway proximity.

## Recommendation

Use EMODnet vessel density (layers 04 Sailing + 05 Pleasure craft, annual averages 2017-2023, plus 12 Unknown/0 Other as a weak extra), downloaded as GeoTIFF zips or via WCS. It is the only source verified to have a leisure category, open access, a clear CC-BY 4.0 licence and unrestricted derivative publication, and sample queries confirmed values in Oslofjord and Bergen. Treat as a relative weight and blend with a coastal prior. Kystverket's grid is a possible cross-check but its leisure filter is unverified; raw HAIS/Kystdatahuset leisure data is restricted and should not be used for published points without written permission from Kystverket.
