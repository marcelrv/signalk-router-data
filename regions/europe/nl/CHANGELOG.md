# Netherlands routing database — changelog

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
