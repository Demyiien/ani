import type { Place } from './data';

export type Area = GeoJSON.Feature<GeoJSON.Polygon | GeoJSON.MultiPolygon, Place>;
export type Areas = GeoJSON.FeatureCollection<GeoJSON.Polygon | GeoJSON.MultiPolygon, Place>;

export function pointInRing(lon: number, lat: number, ring: GeoJSON.Position[]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i], b = ring[j];
    if ((a[1] > lat) !== (b[1] > lat) && lon < ((b[0] - a[0]) * (lat - a[1])) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

export function findArea(areas: Areas, lon: number, lat: number): Area | undefined {
  return areas.features.find(f => {
    const b = f.bbox;
    if (b && (lon < b[0] || lon > b[2] || lat < b[1] || lat > b[3])) return false;
    const polygons = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
    return polygons.some(p => pointInRing(lon, lat, p[0]) && !p.slice(1).some(r => pointInRing(lon, lat, r)));
  });
}

/**
 * Resolve a user's live GPS/browser coordinates to a Philippine municipality.
 * Checks exact polygon containment first, then snaps to the nearest coastal/island municipality within ~65 km (0.6°).
 */
export function resolveUserPlace(areas: Areas, lon: number, lat: number): Place | undefined {
  const exact = findArea(areas, lon, lat);
  if (exact) {
    return { ...exact.properties, lat: +lat.toFixed(6), lon: +lon.toFixed(6) };
  }
  if (lon < 114 || lon > 128 || lat < 4 || lat > 22.5) return undefined;
  let best: Place | undefined;
  let bestDist2 = 0.6 * 0.6;
  for (const f of areas.features) {
    const p = f.properties;
    const dLat = p.lat - lat;
    const dLon = (p.lon - lon) * Math.cos((lat * Math.PI) / 180);
    const d2 = dLat * dLat + dLon * dLon;
    if (d2 < bestDist2) {
      bestDist2 = d2;
      best = p;
    }
  }
  return best;
}

export const normalize = (value: string) =>
  value
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();

const TOKEN_SYNONYMS: Record<string, string> = {
  sta: 'santa',
  sto: 'santo',
  st: 'saint',
  gen: 'general',
  mt: 'mount',
};

const QUERY_SHORTCUTS: Record<string, string> = {
  qc: 'quezon city',
  cdo: 'cagayan de oro city',
  gensan: 'general santos city',
  'gen san': 'general santos city',
  mnl: 'manila city',
  dvo: 'davao city',
  ceb: 'cebu city',
  bgy: 'baguio city',
};

const FEATURED_CITIES = [
  'City of Manila',
  'Quezon City',
  'City of Cebu',
  'City of Davao',
  'City of Cabanatuan',
  'City of Baguio',
  'City of Iloilo',
  'City of Cagayan de Oro',
  'City of Zamboanga',
  'City of General Santos',
  'City of Bacolod',
  'City of Legazpi',
  'City of Puerto Princesa',
];

function expandQuery(raw: string): string {
  const norm = normalize(raw);
  if (!norm) return '';
  if (QUERY_SHORTCUTS[norm]) return QUERY_SHORTCUTS[norm];
  return norm
    .split(' ')
    .filter(Boolean)
    .map(tok => TOKEN_SYNONYMS[tok] ?? tok)
    .join(' ');
}

/**
 * Convert PSGC formal names like "City of Cebu" or "Science City of Muñoz" into conversational "Cebu City" / "Muñoz City".
 */
export function friendlyPlaceName(name: string): string {
  const m = /^(?:Science\s+|Island\s+Garden\s+)?City\s+of\s+(.+)$/i.exec(name.trim());
  if (m) return `${m[1]} City`;
  return name;
}

function placeKeys(p: Place) {
  const official = normalize(p.name);
  const friendly = normalize(friendlyPlaceName(p.name));
  const isCity = /\bcity\b/.test(official);
  const core = official
    .replace(/^(?:science\s+|island\s+garden\s+)?city\s+of\s+/, '')
    .replace(/\s+city$/, '')
    .trim();
  const prov = normalize(p.province);
  const reg = normalize(p.region);
  const nameWords = `${official} ${friendly} ${core}`.split(' ').filter(Boolean);
  const fullText = `${official} ${friendly} ${core} ${prov} ${reg}`;
  return { official, friendly, core, isCity, prov, reg, nameWords, fullText };
}

export function searchPlaces(places: Place[], query: string): Place[] {
  const q = expandQuery(query);
  if (!q) {
    const featuredIdx = new Map(FEATURED_CITIES.map((n, i) => [n, i]));
    return [...places].sort((a, b) => {
      const fa = featuredIdx.get(a.name) ?? 999;
      const fb = featuredIdx.get(b.name) ?? 999;
      return fa - fb || a.name.localeCompare(b.name) || a.province.localeCompare(b.province);
    });
  }

  const tokens = q.split(' ').filter(Boolean);
  const scored: { place: Place; rank: number }[] = [];

  for (const p of places) {
    const k = placeKeys(p);
    if (!tokens.every(t => k.fullText.includes(t))) continue;

    let rank = 8;
    if (k.friendly === q || k.official === q || (k.core === q && k.isCity)) {
      rank = k.isCity ? 0 : 1;
    } else if (k.core === q) {
      rank = 2;
    } else if (k.friendly.startsWith(q) || k.core.startsWith(q) || k.official.startsWith(q)) {
      rank = k.isCity ? 3 : 4;
    } else if (tokens.every(t => k.nameWords.some(w => w.startsWith(t)))) {
      rank = 5;
    } else if (tokens.every(t => k.official.includes(t) || k.friendly.includes(t))) {
      rank = 6;
    } else {
      // Matched via province or region (e.g., searching "Nueva Ecija" or "Cebu"): surface cities in that province first.
      rank = k.isCity ? 7 : 8;
    }
    scored.push({ place: p, rank });
  }

  return scored
    .sort(
      (a, b) =>
        a.rank - b.rank ||
        friendlyPlaceName(a.place.name).localeCompare(friendlyPlaceName(b.place.name)) ||
        a.place.province.localeCompare(b.place.province),
    )
    .map(s => s.place);
}
