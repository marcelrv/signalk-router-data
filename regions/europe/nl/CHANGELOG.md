# Netherlands routing database — changelog

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
