# Netherlands routing database — changelog

## 0.4.0 — 2026-11-16

Requires signalk-routeiq 0.1.0 or higher: the file uses database format version 4 (the POI model), which older plugin versions cannot read correctly. Routes, depths and notices are unchanged from 0.3.1 except for the clearances below.

- POIs: locks and bridges are wholes with parts. 380 locks with their 444 chambers (`lock_chamber`), 3,473 bridges with their 6,239 spans (`bridge_span`), linked by `parent_id`. Replaces `structure` and `edges.lock_id`.
- POIs: ids are stable from one release to the next.
- Edges: new `edge_pois` table lists the locks and bridges each edge passes, so a route reports the structures on it.
- Bridge POIs: the name comes from VNDS instead of the span id (e.g. "Leinebrug", not "Doorvaartopening (beweegbaar) Leinebrug"; "Platvoetbrug", not "B175").
- Bridge POIs: `kind`, `height` and `height_unknown` moved from `properties` to the new `info` column.
- Bridges: an opening span without a charted closed clearance takes the VNDS one (1,576 spans, was 1,173; e.g. Haringvlietbrug 8.27 m), so a low boat can pass closed instead of always waiting.
- Overhead cables: an NAP-referenced cable takes the design high water of the nearest river bridge instead of a flat 15 m margin (e.g. Haringvliet 24 → 37.39 m). Cables now carry `underside_nap`, `mhw_nap` and `clearance_reference` like river bridges (585 spans, was 545), and are never grouped with a bridge. 184 edges changed clearance.
- Waiting times: opening spans inside an operated lock add no wait of their own.
- Validation: all 76 reference routes pass, none worse than 0.3.1.

## 0.3.1 — 2026-11-10

- Bridges: fixed-bridge clearances are checked against VNDS. A missing, placeholder or wrong chart height now uses the VNDS value (e.g. Zeelandbrug 11.0 → 8.3 m, Krammer locks 0.1 → 14.88 m).
- Bridges: a bridge drawn twice in the chart is now one POI.
- Bridge POIs: new `height_source` and `chart_height` properties.

## 0.3.0 — 2026-11-10

- Locks: every lock chamber is now a POI, grouped per lock complex (`structure`). Edges through a chamber carry `lock_id`.
- Locks: 33 locks that stand open have no waiting time; Grote Kolksluis (Zwartsluis) is closed.
- Depth: lock sills, lock-section depths and the VNDS fairway depth now raise shallow chart depths (572 edges, none lowered).
- Depth: the Dokkumerdiep, Visjagersgaatje and the Enkhuizen–Den Oever fairway (incl. the Den Oever approach and harbour) now follow the published VNDS depth.
- Depth: only the latest edition of each chart cell is used (Rijkswaterstaat IENC, week 35 2026, 25–28 Aug). Superseded editions had made depths too shallow (e.g. Den Oever, Boontjes).
- Bridges: fixed bridges that were missing a height or category now get it from VNDS (e.g. Sliksloot, 1.13 m).
- Bridges: a fixed bridge no longer limits the wrong waterway (Van Brienenoord, Bartlehiem), and a storm-surge barrier beside a lock no longer limits the lock (Algerasluis, Urkersluis).
- Bridges: river bridges now carry their underside level and design high water (`underside_nap`, `mhw_nap`).
- Bridge POIs: new `kind` (bridge, cable, weir, flood_barrier, …) and `height_unknown` properties.
- Validation: all 76 reference routes pass, including the national "staande mast route".

## 0.2.0 — 2026-11-09

- Depth: "depth not given" on the chart is treated as uncharted, no longer as 0 m.
- Depth: survey bathymetry (Rijkswaterstaat 1 m, IJsselmeer area) and EMODnet (open North Sea only) where the chart has no depth.
- Depth: VNDS fairway depths applied along each edge.
- Locks: commercial lock chambers are penalised where yachts have their own lock (e.g. IJmuiden).
- Bridges: bascule, swing and draw bridges no longer limit air draft when open; lift bridges keep their open height.
- Bridges: a low fixed span beside an opening span no longer limits the route through the opening.
- Bridges: where a bridge has several openings side by side, the highest one counts (e.g. Prinsenbrug, Haarlem).
- Bridge POIs: new `structure` (shared by the spans of one bridge) and `closed_height` (opening spans) properties.
- Network: fewer dead ends and gaps at region seams.

## 0.1.1 — 2026-11-03

- Network: restored waterways missing from 0.1.0; junctions joined smoothly instead of with sharp turns.
- Network: fewer nodes on open-sea lines.

## 0.1.0 — 2026-11-02

- First release, replaces the Zeeland database: inland waterways, IJsselmeer, Wadden Sea, Delta and coastal approaches.
