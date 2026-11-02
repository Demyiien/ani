export type LayerId = 'rainfall' | 'cloud' | 'temperature' | 'evapotranspiration' | 'vegetation' | 'soil';
export type FreshnessState = 'available' | 'stale' | 'missing' | 'cloud-masked';
export interface Layer {
  id: LayerId;
  name: string;
  description: string;
  source: string;
  resolution: string;
  unit: string;
  max: number;
  colors: string[];
  status: string;
  freshness?: FreshnessState;
  freshnessReason?: string;
  coverageRange?: string;
  rastersCount?: number;
}
export interface Place { id: string; name: string; province: string; region: string; lat: number; lon: number }

export const initialPlace: Place = {
  id: '304903000',
  name: 'City of Cabanatuan',
  province: 'Nueva Ecija',
  region: 'Region III (Central Luzon)',
  lat: 15.486,
  lon: 120.967,
};

export const dates = ['Oct 26', 'Oct 27', 'Oct 28', 'Oct 29', 'Oct 30', 'Oct 31', 'Nov 1'];

// Municipal demo values use a stable area seed, so a polygon and its point details always agree in demo mode.
export function reading(id: LayerId, day: number, place: Place) {
  const seed = Number(place.id) || place.lat * 27 + place.lon * 13;
  const offset = Math.abs(Math.sin(seed));
  const wave = Math.sin(day * 1.2 + offset * 3);
  if (id === 'rainfall') return +(12 + offset * 12 + wave * 7).toFixed(1);
  if (id === 'temperature') return +(28 + offset * 3 + wave).toFixed(1);
  if (id === 'evapotranspiration') return +(3.6 + offset * 1.5 + wave * 0.55).toFixed(2);
  if (id === 'cloud') return Math.round(48 + offset * 23 + wave * 14);
  if (id === 'vegetation') return +(0.6 + offset * 0.19 + wave * 0.04).toFixed(2);
  return +(0.24 + offset * 0.1 + wave * 0.03).toFixed(2);
}

export const fullDate = (day: number) => `${dates[day]}, 2026`;
