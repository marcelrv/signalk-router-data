/* ==========================================================================
   Signal K Nautical Data — Interactive Catalog Explorer
   Zero-build static SPA for GitHub Pages (Leaflet + live JSON catalogs)
   ========================================================================== */

const REPO_BASE = 'https://github.com/marcelrv/signalk-router-data';
const RAW_BASE = 'https://raw.githubusercontent.com/marcelrv/signalk-router-data/main';

const TYPE_META = {
  routing:  { label: 'Routing DB',        short: 'Routing',  category: 'Navigable routing graph', order: 0 },
  grib2:    { label: 'Forecast (GRIB2)',  short: 'GRIB2',    category: 'Forecast currents (tide + weather)', order: 1 },
  utcef:    { label: 'Predicted (UTCEF)', short: 'UTCEF',    category: 'Predicted currents (astronomical)', order: 2 },
  harmonic: { label: 'Harmonic',          short: 'Harmonic', category: 'Predicted stations (astronomical)', order: 3 },
};

const BASEMAP = {
  url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
};

const SEAMARKS_URL = 'https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png';

// --------------------------------------------------------------------------
// State
// --------------------------------------------------------------------------

const state = {
  theme: 'dark',
  activeCatalog: 'tidal', // 'tidal' (default) | 'routing'
  catalogs: {
    tidal: { items: [], generated: null },
    routing: { items: [], generated: null },
  },
  filteredItems: [],
  activeTypes: new Set(), // empty = all (applies to tidal catalog)
  activeTags: new Set(),
  searchQuery: '',
  availableTags: [],
  seamarksEnabled: true,
  userLocation: null, // { lat, lng, source }
};

let map;
let basemapLayer;
let seamarksLayer;
let polygonGroup;
let clickMarker;
let userMarker;
let lastFocusedElement = null;
const layerById = new Map();

// --------------------------------------------------------------------------
// Initialization
// --------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  readUrlParams();
  initMap();
  bindUI();
  const loaded = await loadCatalogs();
  if (!loaded) return;
  syncCatalogUI();
  applyFilters({ fitMap: state.activeCatalog === 'routing' });
});

function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem('sk-data-theme');
  } catch {
    // Storage may be blocked in strict privacy / third-party cookie modes
  }
  const preferred = window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  setTheme(saved || preferred, false);
}

function setTheme(theme, persist = true) {
  state.theme = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', state.theme);
  if (persist) {
    try {
      localStorage.setItem('sk-data-theme', state.theme);
    } catch {
      // Ignore storage errors when site storage is disabled
    }
  }
  if (map) {
    restyleAllLayers();
  }
}

function getTypeColor(type) {
  const styles = getComputedStyle(document.documentElement);
  return styles.getPropertyValue(`--type-${type}`).trim() || '#38bdf8';
}

function initMap() {
  map = L.map('map', {
    center: [25, 0],
    zoom: 2,
    minZoom: 2,
    maxZoom: 16,
    maxBounds: [[-85, -180], [85, 180]],
    maxBoundsViscosity: 0.9,
  });

  basemapLayer = L.tileLayer(BASEMAP.url, {
    attribution: BASEMAP.attribution,
    maxZoom: 18,
    noWrap: true,
    className: 'basemap-tiles',
  }).addTo(map);

  seamarksLayer = L.tileLayer(SEAMARKS_URL, {
    attribution: '&copy; <a href="https://www.openseamap.org/">OpenSeaMap</a>',
    maxZoom: 18,
    noWrap: true,
    opacity: 0.9,
  });
  if (state.seamarksEnabled) {
    seamarksLayer.addTo(map);
  }

  polygonGroup = L.layerGroup().addTo(map);

  map.on('click', (e) => {
    handleMapClick(e.latlng);
  });
}

// --------------------------------------------------------------------------
// Data Loading & Normalization
// --------------------------------------------------------------------------

async function fetchJsonWithFallback(filename) {
  const candidates = [`./${filename}`, `../${filename}`];
  for (const url of candidates) {
    try {
      const res = await fetch(url);
      if (res.ok) return await res.json();
    } catch {
      // try next candidate
    }
  }
  throw new Error(`Could not load ${filename}`);
}

async function loadCatalogs() {
  const summaryEl = document.getElementById('results-summary');
  try {
    const [routingIndex, tideIndex] = await Promise.all([
      fetchJsonWithFallback('routing-index.json'),
      fetchJsonWithFallback('tide-current-index.json'),
    ]);

    const annotate = (items) => {
      for (const item of items) {
        item.bbox = normalizeBbox(item.bbox, item.geometry);
        item.geometry = fixAntimeridianGeometry(item.geometry, item.bbox);
        item.bboxArea = computeBboxArea(item.bbox);
        item.isGlobal = item.bboxArea > 40000;
      }
      return items;
    };

    state.catalogs.routing = {
      items: annotate(normalizeRoutingIndex(routingIndex)),
      generated: routingIndex.generated || null,
    };
    state.catalogs.tidal = {
      items: annotate(normalizeTideCurrentIndex(tideIndex)),
      generated: tideIndex.generated || null,
    };

    document.getElementById('tab-count-tidal').textContent = String(state.catalogs.tidal.items.length);
    document.getElementById('tab-count-routing').textContent = String(state.catalogs.routing.items.length);

    updateTidalTypeCounts();
    return true;
  } catch (err) {
    summaryEl.textContent = 'Failed to load catalog JSON files.';
    const listEl = document.getElementById('dataset-list');
    listEl.textContent = '';
    const box = el('div', 'empty-state');
    box.textContent = `Error loading catalogs: ${err.message}. Serve this site over HTTP alongside routing-index.json and tide-current-index.json.`;
    listEl.appendChild(box);
    return false;
  }
}

function getCurrentCatalog() {
  return state.catalogs[state.activeCatalog] || state.catalogs.tidal;
}

function rebuildTagsForCurrentCatalog() {
  const { items } = getCurrentCatalog();
  const tagCounts = new Map();
  for (const item of items) {
    for (const t of item.tags) {
      tagCounts.set(t, (tagCounts.get(t) || 0) + 1);
    }
  }
  state.availableTags = [...tagCounts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([tag, count]) => ({ tag, count }));

  // Drop any active tags that don't exist in the newly selected catalog
  for (const t of [...state.activeTags]) {
    if (!tagCounts.has(t)) state.activeTags.delete(t);
  }
  renderTagsPopover();
}

function switchCatalog(catalog) {
  if (catalog !== 'tidal' && catalog !== 'routing') return;
  if (state.activeCatalog === catalog) return;
  state.activeCatalog = catalog;
  state.activeTypes.clear();
  state.activeTags.clear();
  if (clickMarker) {
    map.removeLayer(clickMarker);
    clickMarker = null;
  }
  syncCatalogUI();
  syncTypePillUI();
  applyFilters({ fitMap: true });
}

function syncCatalogUI() {
  const isTidal = state.activeCatalog === 'tidal';

  // Top tabs
  document.querySelectorAll('.catalog-tab').forEach((tab) => {
    const active = tab.dataset.catalog === state.activeCatalog;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
  });

  // Tidal type filter pills only shown for tidal catalog
  document.getElementById('tidal-type-pills').hidden = !isTidal;

  // Info banner
  const banner = document.getElementById('info-banner');
  banner.textContent = '';
  const strong = document.createElement('strong');
  const span = document.createElement('span');
  if (isTidal) {
    strong.textContent = 'Forecast vs. Predicted: ';
    span.textContent = 'GRIB2 forecasts combine tides + weather and refresh daily; UTCEF & Harmonic are static astronomical tide tables.';
  } else {
    strong.textContent = 'Routing Databases: ';
    span.textContent = 'Pre-compiled navigable waterway graphs for SignalK RouteIQ (depth, clearances, bridges, locks). Published as .sqlite.gz releases.';
  }
  banner.append(strong, span);

  // Map legend (only datasets rendered as polygons on the map)
  const legend = document.getElementById('map-legend');
  legend.textContent = '';
  const entries = isTidal
    ? [
        ['grib2', 'Forecast (GRIB2)'],
        ['utcef', 'Predicted (UTCEF)'],
      ]
    : [['routing', 'Routing DB']];
  for (const [type, label] of entries) {
    const item = el('div', 'legend-item');
    const dot = el('span', `dot dot-${type}`);
    item.append(dot, label);
    legend.appendChild(item);
  }

  // Updated timestamp
  const updatedEl = document.getElementById('catalog-updated');
  const { generated } = getCurrentCatalog();
  updatedEl.textContent = generated ? `Updated ${formatShortDate(generated)}` : '';

  rebuildTagsForCurrentCatalog();
}

function normalizeRoutingIndex(index) {
  const regions = index?.regions || [];
  return regions.map((r) => {
    const geom = r.boundary_geometry || bboxToPolygon(r.bounding_box);
    return {
      id: `routing:${r.id}`,
      rawId: r.id,
      type: 'routing',
      name: r.name || r.id,
      regionName: r.country || 'Routing Region',
      description: r.description || '',
      provider: r.contributor || 'SignalK RouteIQ',
      url: r.url || REPO_BASE,
      downloadUrl: r.download_url || null,
      sizeBytes: r.size_bytes || null,
      lastUpdate: r.last_update || r.deployed || null,
      stats: r.stats || null,
      tags: Array.isArray(r.tags) ? r.tags : [],
      geometry: geom,
      bbox: r.bounding_box || geometryToBbox(geom),
      plugin: 'signalk-routeiq',
      forecastHours: null,
      cycleHours: null,
      fileCount: 1,
    };
  });
}

function normalizeTideCurrentIndex(index) {
  const sources = index?.sources || [];
  const items = [];

  for (const s of sources) {
    const files = Array.isArray(s.files) ? s.files : [];

    // Multi-region GRIB2 sources: deduplicate files by region_id so each geographic
    // forecast area appears as its own selectable polygon on the map.
    const hasRegionFiles = s.type === 'grib2' && files.some((f) => f.region_id && f.boundary_geometry);

    if (hasRegionFiles) {
      const grouped = new Map();
      for (const f of files) {
        const key = f.region_id || f.filename || f.name;
        if (!grouped.has(key)) {
          grouped.set(key, {
            regionId: key,
            name: f.name || key,
            description: f.description || s.description || '',
            geometry: f.boundary_geometry || s.region?.boundary_geometry || bboxToPolygon(s.region?.bounding_box),
            forecastHours: new Set(),
            cycleHours: new Set(),
            fileTypes: new Set(),
            sampleUrl: f.url_template || f.url || null,
          });
        }
        const entry = grouped.get(key);
        for (const h of f.forecast_hours || []) entry.forecastHours.add(h);
        for (const c of f.cycle_hours || []) entry.cycleHours.add(c);
        if (f.type) entry.fileTypes.add(f.type);
      }

      for (const [key, g] of grouped.entries()) {
        const maxForecast = g.forecastHours.size ? Math.max(...g.forecastHours) : null;
        const cycles = [...g.cycleHours].sort();
        items.push({
          id: `tci:${s.id}:${key}`,
          rawId: `${s.id}/${key}`,
          parentName: s.name,
          type: s.type,
          name: g.name,
          regionName: s.name,
          description: g.description || s.description || '',
          provider: s.contributor || s.source || 'Unknown',
          url: s.url || REPO_BASE,
          downloadUrl: g.sampleUrl && g.sampleUrl.startsWith('http') && !g.sampleUrl.includes('{') ? g.sampleUrl : null,
          releaseHint: g.sampleUrl?.includes('/releases/download/') ? g.sampleUrl.split('/releases/download/')[0] + '/releases' : null,
          sizeBytes: null,
          lastUpdate: s.update_check?.latest_cycle || s.update_check?.last_checked || null,
          stats: null,
          tags: Array.isArray(s.tags) ? s.tags : [],
          geometry: g.geometry,
          bbox: geometryToBbox(g.geometry) || s.region?.bounding_box,
          plugin: 'signalk-tidal-currents',
          forecastHours: maxForecast,
          cycleHours: cycles.length ? cycles : null,
          fileCount: g.fileTypes.size || 1,
        });
      }
    } else {
      const geom = s.region?.boundary_geometry || bboxToPolygon(s.region?.bounding_box);
      const totalBytes = files.reduce((acc, f) => acc + (f.size_bytes || 0), 0) || null;
      const firstFile = files[0];
      let downloadUrl = null;
      if (firstFile?.url) {
        downloadUrl = firstFile.url;
      } else if (firstFile?.filename) {
        downloadUrl = `${RAW_BASE}/${firstFile.filename}`;
      }

      items.push({
        id: `tci:${s.id}`,
        rawId: s.id,
        type: s.type,
        name: s.name || s.id,
        regionName: s.region?.name || s.source || '',
        description: s.description || '',
        provider: s.contributor || s.source || 'Unknown',
        url: s.url || REPO_BASE,
        downloadUrl,
        sizeBytes: totalBytes,
        lastUpdate: s.update_check?.last_checked || null,
        stats: null,
        tags: Array.isArray(s.tags) ? s.tags : [],
        geometry: geom,
        bbox: s.region?.bounding_box || geometryToBbox(geom),
        plugin: 'signalk-tidal-currents',
        forecastHours: null,
        cycleHours: null,
        fileCount: files.length || 1,
      });
    }
  }

  return items;
}

// --------------------------------------------------------------------------
// Filtering & Rendering
// --------------------------------------------------------------------------

function applyFilters({ fitMap = false } = {}) {
  const q = state.searchQuery.trim().toLowerCase();
  const { items: sourceItems } = getCurrentCatalog();
  const isTidal = state.activeCatalog === 'tidal';

  const filtered = sourceItems.filter((item) => {
    if (isTidal && state.activeTypes.size > 0 && !state.activeTypes.has(item.type)) return false;
    if (state.activeTags.size > 0) {
      for (const t of state.activeTags) {
        if (!item.tags.includes(t)) return false;
      }
    }
    if (q) {
      const haystack = `${item.rawId || ''} ${item.name} ${item.regionName} ${item.provider} ${item.description} ${item.tags.join(' ')}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  // Sort list: type order, then alphabetically
  filtered.sort((a, b) => {
    const oa = TYPE_META[a.type]?.order ?? 9;
    const ob = TYPE_META[b.type]?.order ?? 9;
    if (oa !== ob) return oa - ob;
    return a.name.localeCompare(b.name);
  });

  state.filteredItems = filtered;
  renderList();
  renderMapPolygons(fitMap);
  renderActiveTags();
  syncUrlParams();

  const hasFilters = (isTidal && state.activeTypes.size > 0) || state.activeTags.size > 0 || q.length > 0;
  document.getElementById('clear-filters-btn').hidden = !hasFilters;
  const noun = isTidal ? 'tidal datasets' : (sourceItems.length === 1 ? 'routing database' : 'routing databases');
  document.getElementById('results-summary').textContent =
    `Showing ${filtered.length} of ${sourceItems.length} ${noun}`;
}

function updateTidalTypeCounts() {
  const items = state.catalogs.tidal.items;
  const counts = { all: items.length, grib2: 0, utcef: 0, harmonic: 0 };
  for (const item of items) {
    if (counts[item.type] !== undefined) counts[item.type]++;
  }
  for (const [k, v] of Object.entries(counts)) {
    const node = document.getElementById(`count-${k}`);
    if (node) node.textContent = String(v);
  }
}

function renderList() {
  const container = document.getElementById('dataset-list');
  container.textContent = '';

  if (state.filteredItems.length === 0) {
    const empty = el('div', 'empty-state');
    empty.textContent = 'No datasets match the current filters.';
    container.appendChild(empty);
    return;
  }

  const frag = document.createDocumentFragment();
  for (const item of state.filteredItems) {
    frag.appendChild(createDatasetCard(item));
  }
  container.appendChild(frag);
}

function createDatasetCard(item, { compact = false, onPick } = {}) {
  const btn = el('button', 'dataset-card');
  btn.type = 'button';
  btn.dataset.id = item.id;
  btn.setAttribute('role', 'listitem');

  const accent = el('div', 'card-accent');
  accent.style.background = `var(--type-${item.type})`;
  btn.appendChild(accent);

  const body = el('div', 'card-body');

  const top = el('div', 'card-top');
  const title = el('span', 'card-title');
  title.textContent = item.name;
  const badge = el('span', `badge badge-${item.type}`);
  badge.textContent = TYPE_META[item.type]?.short || item.type;
  top.append(title, badge);
  body.appendChild(top);

  const sub = el('div', 'card-sub');
  sub.textContent = item.regionName && item.regionName !== item.name
    ? `${item.regionName} · ${item.provider}`
    : item.provider;
  body.appendChild(sub);

  if (!compact) {
    const meta = el('div', 'card-meta');
    if (item.sizeBytes) meta.appendChild(metaSpan(formatBytes(item.sizeBytes)));
    if (item.forecastHours > 0) meta.appendChild(metaSpan(`+${item.forecastHours}h forecast`));
    if (item.cycleHours?.length) meta.appendChild(metaSpan(`${item.cycleHours.length}x/day`));
    if (item.stats?.nodes) meta.appendChild(metaSpan(`${item.stats.nodes.toLocaleString()} nodes`));
    if (item.isGlobal) meta.appendChild(metaSpan('Global coverage'));
    if (meta.childNodes.length) body.appendChild(meta);
  }

  btn.appendChild(body);

  if (!compact) {
    btn.addEventListener('mouseenter', () => highlightLayer(item.id, true));
    btn.addEventListener('mouseleave', () => highlightLayer(item.id, false));
  }

  btn.addEventListener('click', () => {
    if (onPick) {
      onPick(item);
    } else {
      flyToItem(item);
      openDetailModal(item);
    }
  });

  return btn;
}

function metaSpan(text) {
  const s = document.createElement('span');
  s.textContent = text;
  return s;
}

// --------------------------------------------------------------------------
// Map Polygon Rendering & Interaction
// --------------------------------------------------------------------------

function styleForItem(item, highlighted = false) {
  const color = getTypeColor(item.type);
  if (item.isGlobal) {
    return {
      color,
      weight: highlighted ? 2.5 : 1.2,
      dashArray: '6 6',
      fillColor: color,
      fillOpacity: highlighted ? 0.08 : 0.02,
    };
  }
  return {
    color,
    weight: highlighted ? 3 : 1.6,
    fillColor: color,
    fillOpacity: highlighted ? 0.34 : 0.13,
  };
}

function renderMapPolygons(fitMap = false) {
  polygonGroup.clearLayers();
  layerById.clear();

  // Draw larger polygons first so smaller coastal regions sit cleanly on top
  const ordered = [...state.filteredItems].sort((a, b) => b.bboxArea - a.bboxArea);
  const nonGlobalBounds = L.latLngBounds([]);

  for (const item of ordered) {
    if (!item.geometry || item.type === 'harmonic' || item.isGlobal) continue;
    const geoLayer = L.geoJSON(item.geometry, {
      style: () => styleForItem(item, false),
      interactive: true,
    });

    geoLayer.bindTooltip(buildTooltip(item), {
      sticky: true,
      direction: 'top',
      className: 'nautical-tooltip',
    });

    geoLayer.on('mouseover', () => {
      geoLayer.setStyle(styleForItem(item, true));
      geoLayer.bringToFront();
    });
    geoLayer.on('mouseout', () => {
      geoLayer.setStyle(styleForItem(item, false));
    });
    geoLayer.on('click', (e) => {
      L.DomEvent.stopPropagation(e);
      handleMapClick(e.latlng);
    });

    const b = geoLayer.getBounds();
    if (b.isValid()) nonGlobalBounds.extend(b);

    geoLayer.addTo(polygonGroup);
    layerById.set(item.id, { layer: geoLayer, item });
  }

  if (fitMap && nonGlobalBounds.isValid()) {
    map.fitBounds(nonGlobalBounds, { padding: [32, 32], maxZoom: 8 });
  }
}

function restyleAllLayers() {
  for (const { layer, item } of layerById.values()) {
    layer.setStyle(styleForItem(item, false));
  }
}

function highlightLayer(id, active) {
  const entry = layerById.get(id);
  if (!entry) return;
  entry.layer.setStyle(styleForItem(entry.item, active));
  if (active && !entry.item.isGlobal) entry.layer.bringToFront();
}

function buildTooltip(item) {
  const wrap = document.createElement('div');
  const title = document.createElement('div');
  title.style.fontWeight = '700';
  title.textContent = item.name;
  const sub = document.createElement('div');
  sub.style.opacity = '0.8';
  sub.textContent = `${TYPE_META[item.type]?.label || item.type} · ${item.provider}`;
  wrap.append(title, sub);
  return wrap;
}

function flyToItem(item) {
  const entry = layerById.get(item.id);
  if (!entry || item.isGlobal) return;
  const bounds = entry.layer.getBounds();
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [48, 48], maxZoom: 9 });
  }
}

// --------------------------------------------------------------------------
// Overlap-Aware Region Inspector (Click Anywhere on Map)
// --------------------------------------------------------------------------

function handleMapClick(latlng) {
  const lng = wrapLongitude(latlng.lng);
  const lat = latlng.lat;

  if (clickMarker) map.removeLayer(clickMarker);
  clickMarker = L.circleMarker([lat, lng], {
    radius: 6,
    color: getTypeColor('grib2'),
    weight: 2,
    fillColor: '#ffffff',
    fillOpacity: 0.9,
  }).addTo(map);

  const hits = state.filteredItems.filter((item) =>
    pointInGeometry([lng, lat], item.geometry)
  );

  // Sort: Routing -> GRIB2 -> UTCEF -> Harmonic, then smallest area first
  hits.sort((a, b) => {
    const oa = TYPE_META[a.type]?.order ?? 9;
    const ob = TYPE_META[b.type]?.order ?? 9;
    if (oa !== ob) return oa - ob;
    return a.bboxArea - b.bboxArea;
  });

  openInspectorModal(lat, lng, hits);
}

function openInspectorModal(lat, lng, hits, { nearby = [], subtitlePrefix = '' } = {}) {
  const backdrop = document.getElementById('inspector-backdrop');
  const title = document.getElementById('inspector-title');
  const coords = document.getElementById('inspector-coords');
  const body = document.getElementById('inspector-body');

  const totalCount = hits.length + nearby.length;
  if (hits.length > 0) {
    title.textContent = hits.length === 1
      ? '1 dataset covers this location'
      : `${hits.length} datasets cover this location`;
  } else if (nearby.length > 0) {
    title.textContent = `${nearby.length} nearby dataset${nearby.length === 1 ? '' : 's'} found`;
  } else {
    title.textContent = '0 datasets cover this location';
  }

  const coordStr = `${formatCoord(lat, 'N', 'S')}, ${formatCoord(lng, 'E', 'W')}`;
  coords.textContent = subtitlePrefix ? `${subtitlePrefix} · ${coordStr}` : coordStr;
  body.textContent = '';

  if (totalCount === 0) {
    const empty = el('div', 'empty-state');
    empty.textContent = 'No currently visible datasets cover this area. Try clearing type or tag filters, or switching catalog tabs.';
    body.appendChild(empty);
  } else {
    for (const item of hits) {
      body.appendChild(createDatasetCard(item, {
        compact: true,
        onPick: (picked) => {
          closeModal('inspector');
          openDetailModal(picked);
        },
      }));
    }
    if (nearby.length > 0) {
      const label = el('div', 'inspector-section-label');
      label.textContent = hits.length > 0 ? 'Also nearby (within ~150 km)' : 'Nearby (within ~150 km)';
      body.appendChild(label);
      for (const item of nearby) {
        body.appendChild(createDatasetCard(item, {
          compact: true,
          onPick: (picked) => {
            closeModal('inspector');
            flyToItem(picked);
            openDetailModal(picked);
          },
        }));
      }
    }
  }

  openModal('inspector');
}

// --------------------------------------------------------------------------
// "Near Me" Geolocation (Browser GPS with HTTPS/IP fallback)
// --------------------------------------------------------------------------

async function locateUser() {
  const btn = document.getElementById('near-me-btn');
  const origLabel = btn.textContent;
  btn.disabled = true;
  btn.textContent = '📍 Locating…';

  try {
    const loc = await getUserCoordinates();
    state.userLocation = loc;

    if (userMarker) map.removeLayer(userMarker);
    userMarker = L.circleMarker([loc.lat, loc.lng], {
      radius: 8,
      color: '#ffffff',
      weight: 2.5,
      fillColor: '#ef4444',
      fillOpacity: 0.95,
    })
      .bindTooltip(loc.source === 'gps' ? 'Your location' : 'Approximate location (via IP)', {
        direction: 'top',
        className: 'nautical-tooltip',
      })
      .addTo(map);

    map.flyTo([loc.lat, loc.lng], 7, { duration: 0.9 });

    const hits = state.filteredItems.filter((item) =>
      pointInGeometry([loc.lng, loc.lat], item.geometry)
    );
    const hitIds = new Set(hits.map((h) => h.id));

    // Also include non-global datasets whose bounding box is within ~1.5° (~150 km)
    // in case the user is slightly inland.
    const nearby = state.filteredItems.filter((item) => {
      if (hitIds.has(item.id) || item.isGlobal || !item.bbox) return false;
      return distanceToBboxDeg(loc.lat, loc.lng, item.bbox) <= 1.5;
    });

    const sortFn = (a, b) => {
      const oa = TYPE_META[a.type]?.order ?? 9;
      const ob = TYPE_META[b.type]?.order ?? 9;
      if (oa !== ob) return oa - ob;
      return a.bboxArea - b.bboxArea;
    };
    hits.sort(sortFn);
    nearby.sort(sortFn);

    const prefix = loc.source === 'gps' ? 'Near your location' : 'Near your approximate IP location';
    setTimeout(() => {
      openInspectorModal(loc.lat, loc.lng, hits, { nearby, subtitlePrefix: prefix });
    }, 650);
  } catch (err) {
    alert(`Could not determine your location: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = origLabel;
  }
}

async function getUserCoordinates() {
  // 1. Prefer native browser geolocation when in a secure context (HTTPS / localhost)
  if (navigator.geolocation && window.isSecureContext) {
    try {
      const pos = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: false,
          timeout: 6000,
          maximumAge: 300000,
        });
      });
      return { lat: pos.coords.latitude, lng: pos.coords.longitude, source: 'gps' };
    } catch (err) {
      // Respect explicit user denial: do not fall back to third-party IP lookup
      if (err && err.code === 1) {
        throw new Error('Location permission was denied.');
      }
      // Fall through to approximate IP lookup on timeout / position unavailable
    }
  }

  // 2. Fallback when browser geolocation is unavailable or times out (e.g. desktop without GPS)
  const res = await fetch('https://get.geojs.io/v1/ip/geo.json');
  if (!res.ok) throw new Error('Location service unavailable');
  const data = await res.json();
  const lat = Number.parseFloat(data.latitude);
  const lng = Number.parseFloat(data.longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    throw new Error('Could not resolve coordinates');
  }
  return { lat, lng, source: 'ip' };
}

function lonDistDeg(a, b) {
  return Math.abs((((a - b + 180) % 360) + 360) % 360 - 180);
}

function distanceToBboxDeg(lat, lng, bbox) {
  const dLat = lat < bbox.min_lat ? bbox.min_lat - lat : (lat > bbox.max_lat ? lat - bbox.max_lat : 0);
  const insideLon = bbox.min_lon <= bbox.max_lon
    ? (lng >= bbox.min_lon && lng <= bbox.max_lon)
    : (lng >= bbox.min_lon || lng <= bbox.max_lon);
  const dLon = insideLon ? 0 : Math.min(lonDistDeg(lng, bbox.min_lon), lonDistDeg(lng, bbox.max_lon));
  return Math.hypot(dLat, dLon);
}

// --------------------------------------------------------------------------
// Dataset Detail Modal
// --------------------------------------------------------------------------

function openDetailModal(item) {
  const backdrop = document.getElementById('detail-backdrop');
  const badges = document.getElementById('detail-badges');
  const title = document.getElementById('detail-title');
  const region = document.getElementById('detail-region');
  const body = document.getElementById('detail-body');
  const footer = document.getElementById('detail-footer');

  badges.textContent = '';
  const typeBadge = el('span', `badge badge-${item.type}`);
  typeBadge.textContent = TYPE_META[item.type]?.label || item.type;
  badges.appendChild(typeBadge);

  const catBadge = el('span', 'badge badge-neutral');
  catBadge.textContent = TYPE_META[item.type]?.category || '';
  badges.appendChild(catBadge);

  title.textContent = item.name;
  region.textContent = item.regionName && item.regionName !== item.name
    ? `${item.regionName} · ${item.provider}`
    : item.provider;

  body.textContent = '';

  if (item.description) {
    const p = el('p', 'detail-desc');
    p.textContent = item.description;
    body.appendChild(p);
  }

  // Metadata grid
  const grid = el('div', 'meta-grid');
  addMetaBox(grid, 'Data Source', item.provider);
  if (item.sizeBytes) addMetaBox(grid, 'Download Size', formatBytes(item.sizeBytes));
  if (item.forecastHours > 0) addMetaBox(grid, 'Forecast Horizon', `Up to +${item.forecastHours} hours`);
  if (item.cycleHours?.length) addMetaBox(grid, 'Update Cycles (UTC)', item.cycleHours.map((h) => `${h}:00`).join(', '));
  if (item.lastUpdate) addMetaBox(grid, 'Last Updated', formatShortDate(item.lastUpdate));
  if (item.stats) {
    addMetaBox(grid, 'Graph Network', `${item.stats.nodes?.toLocaleString() || 0} nodes · ${item.stats.edges?.toLocaleString() || 0} edges`);
    if (item.stats.pois) addMetaBox(grid, 'Points of Interest', item.stats.pois.toLocaleString());
  }
  if (item.bbox) {
    addMetaBox(
      grid,
      'Bounding Box',
      `${formatCoord(item.bbox.min_lat, 'N', 'S', 1)} – ${formatCoord(item.bbox.max_lat, 'N', 'S', 1)}, ${formatCoord(item.bbox.min_lon, 'E', 'W', 1)} – ${formatCoord(item.bbox.max_lon, 'E', 'W', 1)}`
    );
  }
  body.appendChild(grid);

  if (item.tags.length) {
    const tagRow = el('div', 'tag-row');
    for (const t of item.tags) {
      const chip = el('span', 'tag-pill');
      chip.textContent = `#${t}`;
      tagRow.appendChild(chip);
    }
    body.appendChild(tagRow);
  }

  // Plugin install hint
  const install = el('div', 'install-box');
  const strong = document.createElement('strong');
  if (item.plugin === 'signalk-routeiq') {
    strong.textContent = 'Install in SignalK RouteIQ: ';
    install.append(
      strong,
      'Open RouteIQ → Data Manager to download and update this routing database automatically.'
    );
  } else {
    strong.textContent = 'Install in SignalK Tidal Currents: ';
    install.append(
      strong,
      'Open the Tidal Currents webapp → Browse Catalog to subscribe, download, and auto-refresh this source.'
    );
  }
  body.appendChild(install);

  // Other marine apps instructions (tidal datasets only; routing DBs are RouteIQ-only)
  if (item.type === 'grib2' || item.type === 'harmonic' || item.type === 'utcef') {
    const other = el('div', 'other-apps-box');
    const oStrong = document.createElement('strong');

    if (item.type === 'grib2') {
      oStrong.textContent = 'Use in OpenCPN, qtVlm, XyGrib & other GRIB apps: ';
      other.append(
        oStrong,
        'Standard WMO GRIB2 surface current forecasts (u/v). Download the .grb2 file below and open it directly in your chartplotter or weather router.'
      );
      other.appendChild(document.createElement('br'));
      const guideLink = el('button', 'inline-guide-btn');
      guideLink.type = 'button';
      guideLink.textContent = 'Import instructions for OpenCPN, qtVlm, XyGrib, LuckGrib & Expedition →';
      guideLink.addEventListener('click', () => {
        closeModal('detail');
        openGuideModal();
      });
      other.appendChild(guideLink);
    } else if (item.type === 'harmonic') {
      oStrong.textContent = 'Use in OpenCPN & XTide: ';
      other.append(
        oStrong,
        'Standard OpenCPN/XTide harmonics pair. Download both the HARMONICS_* data file and its matching .IDX file into the same folder, then add the .IDX file under OpenCPN → Options → Charts → Tides & Currents → Add Dataset.'
      );
      other.appendChild(document.createElement('br'));
      const guideLink = el('button', 'inline-guide-btn');
      guideLink.type = 'button';
      guideLink.textContent = 'View full import guide →';
      guideLink.addEventListener('click', () => {
        closeModal('detail');
        openGuideModal();
      });
      other.appendChild(guideLink);
    } else if (item.type === 'utcef') {
      oStrong.textContent = 'Other apps & developers: ';
      other.append(
        oStrong,
        'Compact harmonic tidal-stream grid (.utcef). Native to SignalK Tidal Currents; see the '
      );
      const specLink = document.createElement('a');
      specLink.href = `${REPO_BASE}/tree/main/specs`;
      specLink.target = '_blank';
      specLink.rel = 'noopener';
      specLink.textContent = 'open format specification';
      other.append(specLink, ' for integrating UTCEF into other tools.');
    }

    body.appendChild(other);
  }

  // Footer actions
  footer.textContent = '';

  if (layerById.has(item.id)) {
    const zoomBtn = el('button', 'btn btn-secondary');
    zoomBtn.type = 'button';
    zoomBtn.textContent = 'Zoom on map';
    zoomBtn.addEventListener('click', () => {
      closeModal('detail');
      setMobileView('map');
      flyToItem(item);
    });
    footer.appendChild(zoomBtn);
  }

  if (item.url) {
    const srcLink = el('a', 'btn btn-secondary');
    srcLink.href = item.url;
    srcLink.target = '_blank';
    srcLink.rel = 'noopener';
    srcLink.textContent = 'Source info ↗';
    footer.appendChild(srcLink);
  }

  if (item.downloadUrl) {
    const dlLink = el('a', 'btn btn-primary');
    dlLink.href = item.downloadUrl;
    dlLink.target = '_blank';
    dlLink.rel = 'noopener';
    dlLink.textContent = item.type === 'grib2' ? 'Download .grb2 ⬇' : 'Download data ⬇';
    footer.appendChild(dlLink);
  } else if (item.releaseHint) {
    const relLink = el('a', 'btn btn-primary');
    relLink.href = item.releaseHint;
    relLink.target = '_blank';
    relLink.rel = 'noopener';
    relLink.textContent = 'View GitHub Releases ↗';
    footer.appendChild(relLink);
  }

  openModal('detail');
}

function openGuideModal() {
  openModal('guide');
}

function getFocusableElements(container) {
  return [...container.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
  )].filter((node) => node.offsetParent !== null);
}

function getActiveModalBackdrop() {
  for (const name of ['guide', 'detail', 'inspector']) {
    const node = document.getElementById(`${name}-backdrop`);
    if (node && !node.hidden) return node;
  }
  return null;
}

function openModal(name) {
  const backdrop = document.getElementById(`${name}-backdrop`);
  if (!backdrop) return;
  if (!getActiveModalBackdrop() && document.activeElement instanceof HTMLElement) {
    lastFocusedElement = document.activeElement;
  }
  backdrop.hidden = false;
  const focusables = getFocusableElements(backdrop);
  if (focusables.length) focusables[0].focus();
}

function addMetaBox(grid, label, value) {
  const box = el('div', 'meta-box');
  const l = el('div', 'meta-label');
  l.textContent = label;
  const v = el('div', 'meta-value');
  v.textContent = value;
  box.append(l, v);
  grid.appendChild(box);
}

function closeModal(name) {
  const elNode = document.getElementById(`${name}-backdrop`);
  if (elNode) elNode.hidden = true;
  if (!getActiveModalBackdrop() && lastFocusedElement && document.contains(lastFocusedElement)) {
    lastFocusedElement.focus();
    lastFocusedElement = null;
  }
}

// --------------------------------------------------------------------------
// UI Event Bindings
// --------------------------------------------------------------------------

function bindUI() {
  // Catalog tabs (Tidal & Currents vs Routing Databases)
  document.querySelectorAll('.catalog-tab').forEach((tab) => {
    tab.addEventListener('click', () => switchCatalog(tab.dataset.catalog));
  });

  // How to use guide button
  document.getElementById('guide-open-btn').addEventListener('click', () => openGuideModal());

  // Theme toggle
  document.getElementById('theme-toggle').addEventListener('click', () => {
    setTheme(state.theme === 'dark' ? 'light' : 'dark');
  });

  // Search input
  const searchInput = document.getElementById('search-input');
  searchInput.value = state.searchQuery;
  searchInput.addEventListener('input', (e) => {
    state.searchQuery = e.target.value;
    applyFilters();
  });

  // Type filter pills
  const typePills = document.querySelectorAll('.filter-pills .pill');
  typePills.forEach((pill) => {
    pill.addEventListener('click', () => {
      const type = pill.dataset.type;
      if (type === 'all') {
        state.activeTypes.clear();
      } else {
        if (state.activeTypes.has(type)) state.activeTypes.delete(type);
        else state.activeTypes.add(type);
      }
      syncTypePillUI();
      applyFilters();
    });
  });
  syncTypePillUI();

  // Tags popover
  const tagsBtn = document.getElementById('tags-btn');
  const tagsPopover = document.getElementById('tags-popover');
  tagsBtn.addEventListener('click', () => {
    const open = tagsPopover.hidden;
    tagsPopover.hidden = !open;
    tagsBtn.setAttribute('aria-expanded', String(open));
  });
  document.getElementById('tags-close-btn').addEventListener('click', () => {
    tagsPopover.hidden = true;
    tagsBtn.setAttribute('aria-expanded', 'false');
  });

  // Clear all filters
  document.getElementById('clear-filters-btn').addEventListener('click', () => {
    state.activeTypes.clear();
    state.activeTags.clear();
    state.searchQuery = '';
    searchInput.value = '';
    syncTypePillUI();
    renderTagsPopover();
    applyFilters();
  });

  // Near me geolocation
  document.getElementById('near-me-btn').addEventListener('click', () => locateUser());

  // Seamarks overlay toggle
  const seamarksBtn = document.getElementById('seamarks-toggle');
  seamarksBtn.addEventListener('click', () => {
    state.seamarksEnabled = !state.seamarksEnabled;
    seamarksBtn.classList.toggle('active', state.seamarksEnabled);
    seamarksBtn.setAttribute('aria-pressed', String(state.seamarksEnabled));
    if (state.seamarksEnabled) seamarksLayer.addTo(map);
    else map.removeLayer(seamarksLayer);
  });

  // Fit visible bounds
  document.getElementById('fit-all-btn').addEventListener('click', () => {
    renderMapPolygons(true);
  });

  // Mobile view switcher
  document.getElementById('view-map-btn').addEventListener('click', () => setMobileView('map'));
  document.getElementById('view-list-btn').addEventListener('click', () => setMobileView('list'));

  // Modal close handlers
  document.querySelectorAll('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => closeModal(btn.dataset.close));
  });
  for (const name of ['inspector', 'detail', 'guide']) {
    const backdrop = document.getElementById(`${name}-backdrop`);
    backdrop.addEventListener('click', (e) => {
      if (e.target === backdrop) closeModal(name);
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeModal('guide');
      closeModal('detail');
      closeModal('inspector');
      tagsPopover.hidden = true;
      tagsBtn.setAttribute('aria-expanded', 'false');
      return;
    }
    if (e.key === 'Tab') {
      const activeBackdrop = getActiveModalBackdrop();
      if (!activeBackdrop) return;
      const focusables = getFocusableElements(activeBackdrop);
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  });
}

function setMobileView(mode) {
  const ws = document.getElementById('workspace');
  ws.dataset.mobileView = mode;
  document.getElementById('view-map-btn').classList.toggle('active', mode === 'map');
  document.getElementById('view-list-btn').classList.toggle('active', mode === 'list');
  if (mode === 'map') {
    setTimeout(() => map.invalidateSize(), 50);
  }
}

function syncTypePillUI() {
  document.querySelectorAll('.filter-pills .pill').forEach((pill) => {
    const type = pill.dataset.type;
    const isActive = type === 'all' ? state.activeTypes.size === 0 : state.activeTypes.has(type);
    pill.classList.toggle('active', isActive);
    pill.setAttribute('aria-pressed', String(isActive));
  });
}

function renderTagsPopover() {
  const list = document.getElementById('tags-list');
  list.textContent = '';
  for (const { tag, count } of state.availableTags) {
    const btn = el('button', 'tag-option');
    btn.type = 'button';
    btn.textContent = `${tag} (${count})`;
    btn.classList.toggle('active', state.activeTags.has(tag));
    btn.addEventListener('click', () => {
      if (state.activeTags.has(tag)) state.activeTags.delete(tag);
      else state.activeTags.add(tag);
      btn.classList.toggle('active', state.activeTags.has(tag));
      applyFilters();
    });
    list.appendChild(btn);
  }
}

function renderActiveTags() {
  const countEl = document.getElementById('active-tags-count');
  const inline = document.getElementById('active-tags-inline');
  countEl.hidden = state.activeTags.size === 0;
  countEl.textContent = String(state.activeTags.size);

  inline.textContent = '';
  for (const tag of state.activeTags) {
    const chip = el('button', 'active-tag-chip');
    chip.type = 'button';
    chip.textContent = `#${tag} ×`;
    chip.title = `Remove tag ${tag}`;
    chip.addEventListener('click', () => {
      state.activeTags.delete(tag);
      renderTagsPopover();
      applyFilters();
    });
    inline.appendChild(chip);
  }
}

// --------------------------------------------------------------------------
// URL Query State
// --------------------------------------------------------------------------

function readUrlParams() {
  const params = new URLSearchParams(window.location.search);
  const catalog = params.get('catalog');
  if (catalog === 'routing' || catalog === 'tidal') {
    state.activeCatalog = catalog;
  }
  const types = params.get('type');
  if (types && state.activeCatalog === 'tidal') {
    for (const t of types.split(',')) {
      if (t === 'grib2' || t === 'utcef' || t === 'harmonic') state.activeTypes.add(t);
    }
  }
  const tags = params.get('tags');
  if (tags) {
    for (const t of tags.split(',')) {
      if (t) state.activeTags.add(t);
    }
  }
  const q = params.get('q');
  if (q) state.searchQuery = q;
}

function syncUrlParams() {
  const params = new URLSearchParams();
  if (state.activeCatalog !== 'tidal') params.set('catalog', state.activeCatalog);
  if (state.activeCatalog === 'tidal' && state.activeTypes.size) {
    params.set('type', [...state.activeTypes].join(','));
  }
  if (state.activeTags.size) params.set('tags', [...state.activeTags].join(','));
  if (state.searchQuery.trim()) params.set('q', state.searchQuery.trim());
  const qs = params.toString();
  const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  window.history.replaceState(null, '', newUrl);
}

// --------------------------------------------------------------------------
// Geometry & Formatting Helpers
// --------------------------------------------------------------------------

function bboxToPolygon(bbox) {
  if (!bbox) return null;
  const { min_lon, min_lat, max_lon, max_lat } = bbox;
  return {
    type: 'Polygon',
    coordinates: [[
      [min_lon, min_lat],
      [max_lon, min_lat],
      [max_lon, max_lat],
      [min_lon, max_lat],
      [min_lon, min_lat],
    ]],
  };
}

function geometryToBbox(geom) {
  if (!geom) return null;
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  const visit = (coords) => {
    if (typeof coords[0] === 'number') {
      const [lon, lat] = coords;
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    } else {
      for (const c of coords) visit(c);
    }
  };
  if (geom.coordinates) visit(geom.coordinates);
  if (!Number.isFinite(minLon)) return null;
  return { min_lon: minLon, min_lat: minLat, max_lon: maxLon, max_lat: maxLat };
}

function computeBboxArea(bbox) {
  if (!bbox) return 0;
  let lonSpan = bbox.max_lon - bbox.min_lon;
  if (lonSpan < 0) lonSpan += 360;
  else if (lonSpan > 180 && lonSpan < 350) lonSpan = 360 - lonSpan;
  return Math.abs(lonSpan) * Math.abs(bbox.max_lat - bbox.min_lat);
}

/**
 * Normalizes wide Pacific bboxes stored with min_lon < max_lon spanning > 180°
 * (e.g. -75..110 -> min_lon: 110, max_lon: -75 across the antimeridian) so
 * distanceToBboxDeg, detail display, and polygon splitting all agree.
 */
function normalizeBbox(bbox, geom) {
  if (!bbox) return bbox;
  const { min_lon, max_lon } = bbox;
  const isSimpleRect =
    geom?.type === 'Polygon' &&
    geom.coordinates.length === 1 &&
    geom.coordinates[0].length === 5;
  if (isSimpleRect && (max_lon - min_lon) > 180 && (max_lon - min_lon) < 350) {
    return { ...bbox, min_lon: max_lon, max_lon: min_lon };
  }
  return bbox;
}

/**
 * Splits antimeridian-crossing polygons (e.g. Alaska 177.4°E to -128.15°W,
 * Bering Sea, Fiji, Central South Pacific) into a clean MultiPolygon on
 * [east, 180] and [-180, west] so Leaflet never draws horizontal bands
 * wrapping across the globe.
 */
function fixAntimeridianGeometry(geom, bbox) {
  if (!geom || !bbox) return geom;
  const { min_lat, max_lat, min_lon, max_lon } = bbox;
  const isGlobal = (max_lon - min_lon) >= 350;
  if (isGlobal) return geom;

  if (min_lon > max_lon) {
    return splitBboxAtAntimeridian(min_lat, max_lat, min_lon, max_lon);
  }

  return geom;
}

function splitBboxAtAntimeridian(minLat, maxLat, eastLon, westLon) {
  const rect = (w, e) => [[
    [w, minLat],
    [e, minLat],
    [e, maxLat],
    [w, maxLat],
    [w, minLat],
  ]];
  return {
    type: 'MultiPolygon',
    coordinates: [
      rect(eastLon, 180),
      rect(-180, westLon),
    ],
  };
}

function pointInRing(pt, ring) {
  const [x, y] = pt;
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const intersect = ((yi > y) !== (yj > y)) &&
      (x < ((xj - xi) * (y - yi)) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function pointInPolygonCoords(pt, rings) {
  if (!rings.length || !pointInRing(pt, rings[0])) return false;
  for (let i = 1; i < rings.length; i++) {
    if (pointInRing(pt, rings[i])) return false; // inside hole
  }
  return true;
}

function pointInGeometry(pt, geom) {
  if (!geom) return false;
  if (geom.type === 'Polygon') {
    return pointInPolygonCoords(pt, geom.coordinates);
  }
  if (geom.type === 'MultiPolygon') {
    return geom.coordinates.some((poly) => pointInPolygonCoords(pt, poly));
  }
  return false;
}

function wrapLongitude(lng) {
  return ((((lng + 180) % 360) + 360) % 360) - 180;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let val = bytes / 1024;
  let idx = 0;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx++;
  }
  return `${val.toFixed(val >= 10 ? 1 : 2)} ${units[idx]}`;
}

function formatShortDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 10);
}

function formatCoord(val, pos, neg, decimals = 3) {
  return `${Math.abs(val).toFixed(decimals)}° ${val >= 0 ? pos : neg}`;
}

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}
