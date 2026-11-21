// Predict u/v at UTCEF stations with the signalk-tidal-currents plugin's own
// code, so the harmonic fit is checked against the engine that will use it.
// Usage: node plugin_predict.mjs <plugin-dist-dir> <utcef-dir> < request.json
//   request: {"ids": [...], "times_ms": [...]}  -> stdout {"<id>": [[u,v],...]}
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [dist, dir] = process.argv.slice(2);
const { loadUtcefDir, utcefSampleAt } = await import(pathToFileURL(path.join(dist, 'utcef.js')).href);
const req = JSON.parse(readFileSync(0, 'utf8'));
const data = loadUtcefDir(dir);
if (!data) throw new Error(`no UTCEF data in ${dir}`);
const byId = new Map(data.currentStations.map((s) => [s.id, s]));
const out = {};
for (const id of req.ids) {
  const s = byId.get(id);
  if (!s) continue;
  out[id] = req.times_ms.map((t) => {
    const r = utcefSampleAt(s, t);
    return [r.u, r.v];
  });
}
process.stdout.write(JSON.stringify(out));
