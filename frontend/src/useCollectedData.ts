import { useCallback, useEffect, useState } from 'react';
import type { FreshnessState, LayerId } from './data';

export interface Observation {
  area_id: string;
  layer: LayerId;
  date: string;
  value: number | null;
  unit: string;
  source: string;
  kind: string;
  quality: string;
  fetched_at: string;
  sample_lat: number;
  sample_lon: number;
  status?: string;
  coverage?: number | null;
}

export interface LayerInventoryItem {
  layer: LayerId;
  coverage_start: string | null;
  coverage_end: string | null;
  latest_valid_date?: string | null;
  primary_source?: string | null;
  samples: number;
  valid_samples: number;
  cloud_masked_samples: number;
  areas?: number;
  rasters_count?: number;
  raster_dates?: string[];
  freshness: FreshnessState;
  freshness_reason: string;
}

export interface SourceInfo {
  id?: string;
  name: string;
  url: string;
  resolution: string;
  kind: string;
  layers?: string[];
  scope?: string;
  samples?: number;
  valid_samples?: number;
  cloud_masked_samples?: number;
  areas?: number;
  rasters_count?: number;
  coverage_start?: string | null;
  coverage_end?: string | null;
  latest_date?: string | null;
  upstream_latest?: string | null;
  last_collected?: string | null;
  last_error?: string | null;
  freshness?: FreshnessState;
  freshness_reason?: string;
}

export interface RasterMeta {
  layer: LayerId;
  source: string;
  source_name?: string;
  date: string;
  composite_date?: string;
  url: string;
  bounds: [number, number, number, number];
  coordinates: [[number, number], [number, number], [number, number], [number, number]];
  unit: string;
  scale_min: number;
  scale_max: number;
  observed_min: number | null;
  observed_max: number | null;
  valid_pct: number;
  cloud_masked_pct: number;
  land_cloud_masked_pct?: number;
  imagery_timestamp?: string;
}

export interface AdvisoryItem {
  id: string;
  product: string;
  title: string;
  issued_at: string;
  url: string;
  provinces: string[];
  excerpt: string;
  text_status: string;
}

export interface ProvinceAdvisoryStatus {
  province: string;
  severity: string;
  title: string;
  product: string;
  excerpt: string;
  issued_at: string;
}

export interface AdvisoriesPayload {
  collected: boolean;
  count: number;
  advisories: AdvisoryItem[];
  province_map: Record<string, ProvinceAdvisoryStatus>;
  source_url: string;
}

export interface CropQuarterItem {
  quarter: string;
  stage: string;
  palay_share_pct: number;
}

export interface CropProfile {
  province: string;
  region: string;
  reference_year: number | null;
  dominant_crop: string;
  palay_production_mt: number | null;
  palay_area_ha: number | null;
  palay_yield_mt_ha: number | null;
  irrigated_palay_mt: number | null;
  rainfed_palay_mt: number | null;
  irrigated_share_pct: number | null;
  corn_production_mt: number | null;
  corn_area_ha: number | null;
  corn_yield_mt_ha: number | null;
  quarterly_palay_shares_pct: Record<string, number>;
  quarterly_palay_mt: Record<string, number | null>;
  quarterly_corn_mt: Record<string, number | null>;
  ytd_2026_palay_mt: Record<string, number>;
  peak_harvest_quarter: string | null;
  wet_season_share_pct: number | null;
  dry_season_share_pct: number | null;
  calendar: {
    wet_season: { planting: string; harvest: string };
    dry_season: { planting: string; harvest: string };
    current_month: number;
    current_stage: string;
    quarters: CropQuarterItem[];
  };
  note: string | null;
}

export interface CropsPayload {
  collected: boolean;
  national: { palay_production_mt: number | null; corn_production_mt: number | null };
  provinces: Record<string, CropProfile>;
  source_citation: string;
}

interface PointData {
  dates: string[];
  observations: Observation[];
  layer_inventory?: Record<LayerId, LayerInventoryItem>;
  advisories?: AdvisoryItem[];
  source_info: Record<string, SourceInfo>;
  spatial_note?: string;
}

export interface Job {
  id: string;
  status: string;
  completed: number;
  total: number;
  failed: number;
  rows: number;
  errors: { source: string; error: string; area_id: string }[];
}

export type CollectMode = 'local' | 'national_feeds' | 'national_weather';

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw err;
    throw new Error('Backend unavailable. Start or restart python run.py on port 8000 and try again.');
  }
  if (!response.ok) {
    let message = 'Backend unavailable. Start the Python API and try again.';
    try {
      const data = await response.json();
      message = typeof data.detail === 'string' ? data.detail : data.detail?.message || message;
    } catch {
      /* Proxy errors may not be JSON. */
    }
    throw new Error(message);
  }
  return response.json();
}

export function useCollectedData(areaId: string, layer: LayerId, enabled: boolean) {
  const [point, setPoint] = useState<PointData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [layerSummary, setLayerSummary] = useState<Record<string, LayerInventoryItem>>({});
  const [advisories, setAdvisories] = useState<AdvisoriesPayload | null>(null);
  const [crops, setCrops] = useState<CropsPayload | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [jobError, setJobError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [version, setVersion] = useState(0);

  const refresh = useCallback(() => setVersion(v => v + 1), []);

  const dismissNotice = useCallback(() => {
    setError('');
    setJobError('');
    setJob(prev => (prev && ['queued', 'running'].includes(prev.status) ? prev : null));
  }, []);

  useEffect(() => {
    if (!enabled) {
      setPoint(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api<PointData>(`/api/point?area_id=${areaId}&layer=${layer}`, { signal: controller.signal })
      .then(setPoint)
      .catch(e => {
        if (!controller.signal.aborted) {
          setPoint(null);
          setError(e.message);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    api<{ sources: SourceInfo[]; layers?: Record<string, LayerInventoryItem> }>('/api/sources', { signal: controller.signal })
      .then(d => {
        setSources(d.sources);
        if (d.layers) setLayerSummary(d.layers);
      })
      .catch(() => {});

    return () => controller.abort();
  }, [areaId, layer, enabled, version]);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    api<AdvisoriesPayload>('/api/advisories', { signal: controller.signal }).then(setAdvisories).catch(() => {});
    api<CropsPayload>('/api/crops', { signal: controller.signal }).then(setCrops).catch(() => {});
    return () => controller.abort();
  }, [enabled, version]);

  useEffect(() => {
    api<Job[]>('/api/jobs')
      .then(jobs => setJob(jobs.find(j => ['queued', 'running'].includes(j.status)) ?? null))
      .catch(() => {});
  }, []);

  const running = !!job && ['queued', 'running'].includes(job.status);

  useEffect(() => {
    if (!running || !job) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api<Job>(`/api/jobs/${job.id}`, { signal: controller.signal });
        setJob(next);
        if (!['queued', 'running'].includes(next.status)) {
          refresh();
          return;
        }
      } catch (e) {
        if (!controller.signal.aborted) setJobError((e as Error).message);
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 2500);
    };
    timer = setTimeout(poll, 1000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [running, job?.id, refresh]);

  const collect = async (mode: CollectMode | boolean = 'local') => {
    const resolvedMode: CollectMode = typeof mode === 'boolean' ? (mode ? 'national_weather' : 'local') : mode;
    setSubmitting(true);
    setJobError('');
    try {
      const payload =
        resolvedMode === 'national_weather'
          ? { area_ids: [areaId], all_areas: true, sources: ['open_meteo'] }
          : resolvedMode === 'national_feeds'
            ? { area_ids: [], all_areas: false, sources: ['chirps', 'imerg', 'himawari', 'smap', 'gibs_ndvi', 'open_meteo_grid', 'pagasa', 'psa'] }
            : { area_ids: [areaId], all_areas: false, sources: ['open_meteo', 'modis', 'nasa_power'] };
      const init: RequestInit = {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      };
      // Use RESTful POST /api/jobs first because privacy extensions (uBlock/Brave/EasyPrivacy) block '/api/collect' as analytics telemetry.
      let res: Response;
      try {
        res = await fetch('/api/jobs', init);
      } catch {
        throw new Error('Backend unavailable. Start or restart python run.py on port 8000 and try again.');
      }
      if (res.status === 405 || res.status === 404) {
        try {
          setJob(await api<Job>('/api/collect', init));
          return;
        } catch (fallbackErr) {
          const healthOk = await fetch('/api/health').then(r => r.ok).catch(() => false);
          if (healthOk) {
            throw new Error(
              'Your browser ad/privacy blocker blocked /api/collect. Restart python run.py once in backend/ so sync uses /api/jobs instead.',
            );
          }
          throw fallbackErr;
        }
      }
      if (!res.ok) {
        let message = 'Sync request could not start.';
        try {
          const data = await res.json();
          message = typeof data.detail === 'string' ? data.detail : data.detail?.message || message;
        } catch {
          /* Ignore non-JSON error body. */
        }
        throw new Error(message);
      }
      setJob(await res.json());
    } catch (e) {
      setJobError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async () => {
    if (!job) return;
    try {
      setJob(await api<Job>(`/api/jobs/${job.id}/cancel`, { method: 'POST' }));
      refresh();
    } catch (e) {
      setJobError((e as Error).message);
    }
  };

  return {
    point,
    error,
    loading,
    sources,
    layerSummary,
    advisories,
    crops,
    job,
    jobError,
    submitting,
    running,
    refresh,
    dismissNotice,
    collect,
    cancel,
    version,
  };
}

export function useMapValues(layer: LayerId, date: string, enabled: boolean, version: number) {
  const [loaded, setLoaded] = useState<{
    key: string;
    values: Record<string, Observation>;
    raster: RasterMeta | null;
    compositeDate: string | null;
  }>({ key: '', values: {}, raster: null, compositeDate: null });
  const [error, setError] = useState('');

  const targetKey = enabled && date ? `${layer}:${date}:${version}` : '';
  const loading = Boolean(targetKey && loaded.key !== targetKey);

  useEffect(() => {
    setError('');
    if (!enabled || !date) {
      setLoaded({ key: '', values: {}, raster: null, compositeDate: null });
      return;
    }
    const reqKey = `${layer}:${date}:${version}`;
    const controller = new AbortController();
    api<{ values: Record<string, Observation>; raster?: RasterMeta | null; composite_date?: string | null }>(
      `/api/map?layer=${layer}&observation_date=${date}`,
      { signal: controller.signal },
    )
      .then(d => {
        setLoaded({
          key: reqKey,
          values: d.values,
          raster: d.raster ?? null,
          compositeDate: d.composite_date ?? null,
        });
      })
      .catch(e => {
        if (!controller.signal.aborted) {
          setLoaded({ key: reqKey, values: {}, raster: null, compositeDate: null });
          setError(e.message);
        }
      });
    return () => controller.abort();
  }, [layer, date, enabled, version]);

  return {
    values: loaded.values,
    raster: loaded.raster,
    compositeDate: loaded.compositeDate,
    loading,
    error,
  };
}
