import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Bell,
  Calendar,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Cloud,
  CloudRain,
  Database,
  Droplets,
  ExternalLink,
  FileText,
  Info,
  Layers,
  Leaf,
  Map as MapIcon,
  MapPin,
  Radio,
  Search,
  Sprout,
  Thermometer,
  Wind,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { dates as demoDates, fullDate as demoFullDate, initialPlace, reading } from './data';
import type { FreshnessState, Layer, LayerId, Place } from './data';
import { friendlyPlaceName, resolveUserPlace, searchPlaces } from './geography';
import type { Areas } from './geography';
import { useCollectedData, useMapValues } from './useCollectedData';
import type { AdvisoryItem, CropProfile } from './useCollectedData';
import type { ClimateRenderStyle, CropMetricMode, MapOverlayMode } from './MapView';
import CollectionBar from './CollectionBar';

const MapView = lazy(() => import('./MapView'));

const icons: Record<LayerId, LucideIcon> = {
  rainfall: CloudRain,
  cloud: Cloud,
  temperature: Thermometer,
  evapotranspiration: Wind,
  vegetation: Leaf,
  soil: Droplets,
};

const sourceLinks: Record<LayerId, string> = {
  rainfall: 'https://www.chc.ucsb.edu/data/chirps',
  cloud: 'https://www.data.jma.go.jp/mscweb/en/himawari89/',
  temperature: 'https://open-meteo.com/en/docs/historical-weather-api',
  evapotranspiration: 'https://open-meteo.com/en/docs/historical-weather-api',
  vegetation: 'https://modis.gsfc.nasa.gov/data/dataprod/mod13.php',
  soil: 'https://smap.jpl.nasa.gov/',
};

const layerSubtitles: Record<LayerId, string> = {
  rainfall: 'Rainfall amount',
  cloud: 'Satellite view',
  temperature: 'Surface conditions',
  evapotranspiration: 'Crop water demand',
  vegetation: 'Crop greenness',
  soil: 'Water in the soil',
};

function freshnessText(state?: FreshnessState | string): string {
  if (state === 'available' || state === 'Available') return 'Available';
  if (state === 'stale' || state === 'Stale' || state === 'Delayed') return 'Stale';
  if (state === 'cloud-masked' || state === 'Cloud-masked' || state === 'Cloud-obscured') return 'Cloud-masked';
  return 'Missing';
}

function freshnessClass(label: string): string {
  if (label === 'Available') return 'ok';
  if (label === 'Stale') return 'warn';
  if (label === 'Cloud-masked') return 'masked';
  return 'missing';
}

function formatCoverage(start?: string | null, end?: string | null): string {
  if (!start && !end) return 'No stored dates';
  if (start && end && start !== end) return `${start} → ${end}`;
  return end ?? start ?? 'No stored dates';
}

function formatReading(val: number | null, id: LayerId): string {
  if (val === null) return '—';
  if (id === 'cloud' || id === 'temperature') return Number(val.toFixed(1)).toString();
  if (id === 'rainfall' || id === 'evapotranspiration') return Number(val.toFixed(2)).toString();
  return Number(val.toFixed(3)).toString();
}

function demoCropProfile(place: Place): CropProfile {
  return {
    province: place.province,
    region: place.region,
    reference_year: 2025,
    dominant_crop: 'Palay',
    palay_production_mt: 485200,
    palay_area_ha: 98400,
    palay_yield_mt_ha: 4.93,
    irrigated_palay_mt: 412000,
    rainfed_palay_mt: 73200,
    irrigated_share_pct: 84.9,
    corn_production_mt: 64300,
    corn_area_ha: 14800,
    corn_yield_mt_ha: 4.34,
    quarterly_palay_shares_pct: { Q1: 18.4, Q2: 31.2, Q3: 14.1, Q4: 36.3 },
    quarterly_palay_mt: { Q1: 89277, Q2: 151382, Q3: 68413, Q4: 176128 },
    quarterly_corn_mt: { Q1: 19200, Q2: 24100, Q3: 11200, Q4: 9800 },
    ytd_2026_palay_mt: { Q1: 91400, Q2: 154800, Q3: 70100 },
    peak_harvest_quarter: 'Q4',
    wet_season_share_pct: 50.4,
    dry_season_share_pct: 49.6,
    calendar: {
      wet_season: { planting: 'Jun – Jul', harvest: 'Oct – Dec (Peak Q4)' },
      dry_season: { planting: 'Dec – Jan', harvest: 'Mar – May (Q1–Q2)' },
      current_month: 11,
      current_stage: 'Peak wet-season palay harvest & dry-season seedbed prep (Demo)',
      quarters: [
        { quarter: 'Q1 (Jan–Mar)', stage: 'Dry-season vegetative growth & early harvest', palay_share_pct: 18.4 },
        { quarter: 'Q2 (Apr–Jun)', stage: 'Main dry-season harvest & wet-season land prep', palay_share_pct: 31.2 },
        { quarter: 'Q3 (Jul–Sep)', stage: 'Wet-season crop growth & early harvest', palay_share_pct: 14.1 },
        { quarter: 'Q4 (Oct–Dec)', stage: 'Main wet-season harvest & dry-season establishment', palay_share_pct: 36.3 },
      ],
    },
    note: 'Simulated crop profile for interface exploration.',
  };
}

const demoAdvisories: AdvisoryItem[] = [
  {
    id: 'demo-synopsis',
    product: 'Daily Weather Synopsis',
    title: 'PAGASA 24-Hour Public Weather Synopsis (Simulated)',
    issued_at: '2026-11-01T16:00:00+08:00',
    url: 'https://www.pagasa.dost.gov.ph/weather',
    provinces: [],
    excerpt: 'Northeast Monsoon affecting Extreme Northern Luzon. Easterlies bringing isolated afternoon rainshowers across Central and Southern Luzon.',
    text_status: 'active',
  },
  {
    id: 'demo-agri',
    product: 'Farm Weather Forecast',
    title: 'Farm Weather: Central & Eastern Luzon (Simulated)',
    issued_at: '2026-11-01T08:00:00+08:00',
    url: 'https://www.pagasa.dost.gov.ph/agri-weather',
    provinces: ['Nueva Ecija', 'Cagayan', 'Isabela', 'Aurora', 'Tarlac', 'Bulacan', 'Pampanga'],
    excerpt: 'Partly cloudy to cloudy skies with isolated light rains. Lowland temp: 23–33 °C · RH: 60–94% · Leaf wetness: 2–5 hrs. Favorable window for drying harvested wet-season palay.',
    text_status: 'advisory',
  },
];

function initialTheme() {
  try {
    return localStorage.getItem('ani-theme') === 'dark';
  } catch {
    return false;
  }
}

type WorkspaceView = 'map' | 'advisories' | 'crops' | 'sources';

export default function App() {
  const [dark, setDark] = useState(initialTheme);
  const [view, setView] = useState<WorkspaceView>('map');
  const [baseCatalog, setCatalog] = useState<Layer[]>([]);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const [active, setActive] = useState<LayerId>('rainfall');
  const [day, setDay] = useState(6);
  const [place, setPlace] = useState<Place>(initialPlace);
  const [userLocated, setUserLocated] = useState(false);
  const userChosePlace = useRef(false);
  const [visible, setVisible] = useState(true);
  const [renderStyle, setRenderStyle] = useState<ClimateRenderStyle>('raster');
  const [overlayMode, setOverlayMode] = useState<MapOverlayMode>('climate');
  const [cropMetric, setCropMetric] = useState<CropMetricMode>('palay');
  const [cropFilter, setCropFilter] = useState('');
  const [search, setSearch] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [modal, setModal] = useState<'help' | 'alerts' | null>(null);
  const [layersCollapsed, setLayersCollapsed] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [mobilePanel, setMobilePanel] = useState<'layers' | 'details' | null>(null);
  const [areas, setAreas] = useState<Areas | null>(null);

  const locations = useMemo(() => areas?.features.map(f => f.properties) ?? [], [areas]);
  const [demo, setDemo] = useState(false);
  const collected = useCollectedData(place.id, active, !demo);
  const dateKeys = collected.point?.dates ?? [];
  const dates = demo
    ? demoDates
    : dateKeys.map(d => new Date(d + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }));
  const fullDate = (i: number) =>
    demo
      ? demoFullDate(i)
      : dateKeys[i]
        ? new Date(dateKeys[i] + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
        : 'No collected dates';

  const mapData = useMapValues(active, dateKeys[day] ?? '', !demo, collected.version);

  const obsFor = (id: LayerId, index = day) => {
    if (demo || !collected.point) return undefined;
    const targetDate = dateKeys[index];
    if (!targetDate) return undefined;
    const exact = collected.point.observations.find(r => r.layer === id && r.date === targetDate);
    if (exact || id !== 'vegetation') return exact;
    return collected.point.observations
      .filter(r => r.layer === 'vegetation' && r.date <= targetDate)
      .sort((a, b) => a.date.localeCompare(b.date))
      .at(-1);
  };

  const valueFor = (id: LayerId, index = day): number | null =>
    demo ? reading(id, index, place) : obsFor(id, index)?.value ?? null;

  const catalog = useMemo(
    () =>
      baseCatalog.map(l => {
        if (demo) {
          const fLabel = freshnessText(l.status);
          return {
            ...l,
            status: fLabel,
            freshness: fLabel.toLowerCase() as FreshnessState,
            coverageRange: '2026-10-26 → 2026-11-01',
            rastersCount: 7,
          };
        }
        const localInv = collected.point?.layer_inventory?.[l.id];
        const natInv = collected.layerSummary[l.id];
        const inv = natInv && natInv.samples > 0 ? natInv : localInv;
        const fLabel = freshnessText(inv?.freshness ?? 'missing');
        return {
          ...l,
          status: fLabel,
          freshness: inv?.freshness ?? 'missing',
          freshnessReason: inv?.freshness_reason ?? localInv?.freshness_reason,
          coverageRange: formatCoverage(inv?.coverage_start ?? localInv?.coverage_start, inv?.coverage_end ?? localInv?.coverage_end),
          rastersCount: natInv?.rasters_count ?? 0,
        };
      }),
    [baseCatalog, demo, collected.point, collected.layerSummary],
  );

  const dialog = useRef<HTMLDialogElement>(null);
  const searchBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    try {
      localStorage.setItem('ani-theme', dark ? 'dark' : 'light');
    } catch {
      /* Theme still works without storage. */
    }
  }, [dark]);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    Promise.all(
      ['/catalog.json', '/philippines-municipalities.json'].map(url =>
        fetch(url).then(r => {
          if (!r.ok) throw Error();
          return r.json();
        }),
      ),
    )
      .then(([data, geography]) => {
        if (!Array.isArray(data) || data.length !== 6 || !geography.features?.length) throw Error();
        if (!cancelled) {
          setCatalog(data);
          setAreas(geography);
          const home = geography.features.find((f: { properties: Place }) =>
            f.properties.name.toLowerCase().includes('cabanatuan'),
          );
          if (home && !userChosePlace.current) setPlace(home.properties);

          if (typeof navigator !== 'undefined' && 'geolocation' in navigator && !userChosePlace.current) {
            navigator.geolocation.getCurrentPosition(
              pos => {
                if (cancelled || userChosePlace.current) return;
                const matched = resolveUserPlace(geography, pos.coords.longitude, pos.coords.latitude);
                if (matched) {
                  setPlace(matched);
                  setUserLocated(true);
                }
              },
              () => {
                /* Keep default municipality if browser geolocation is denied or unavailable. */
              },
              { enableHighAccuracy: false, timeout: 6000, maximumAge: 300000 },
            );
          }
        }
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  useEffect(() => {
    if (modal) dialog.current?.showModal();
    else dialog.current?.close();
  }, [modal]);

  useEffect(() => {
    function close(e: MouseEvent) {
      if (!searchBox.current?.contains(e.target as Node)) setSearchOpen(false);
    }
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  useEffect(() => {
    setDay(6);
  }, [place.id, active, demo, collected.point?.dates.join(',')]);

  const layer = catalog.find(l => l.id === active);
  const choose = (p: Place) => {
    userChosePlace.current = true;
    setPlace(p);
    setDetailsOpen(true);
    setSearch('');
    setSearchOpen(false);
  };

  const handleMapSelect = (p: Place) => {
    userChosePlace.current = true;
    setPlace(p);
    setDetailsOpen(true);
  };

  const allMatches = searchPlaces(locations, search);
  const matches = allMatches.slice(0, 50);
  const activeValue = valueFor(active);
  const selectedObservation = obsFor(active);
  const isCloudMaskedPoint = !demo && selectedObservation?.status === 'cloud-masked';
  const selectedSource = selectedObservation
    ? collected.point?.source_info[selectedObservation.source]
    : mapData.raster
      ? collected.point?.source_info[mapData.raster.source]
      : null;

  const series = dates.map((_, i) => valueFor(active, i));
  const max = Math.max(1, ...series.filter((v): v is number => v !== null)) * 1.15;
  const change =
    day > 0 && series[day] != null && series[day - 1] != null
      ? +(series[day]! - series[day - 1]!).toFixed(2)
      : null;

  const rainToday = valueFor('rainfall');
  const etToday = valueFor('evapotranspiration');
  const moistureBalance = rainToday != null && etToday != null ? +(rainToday - etToday).toFixed(2) : null;

  const activeAdvisories = useMemo(() => {
    const raw = demo ? demoAdvisories : collected.advisories?.advisories ?? [];
    return [...raw].sort((a, b) => b.issued_at.localeCompare(a.issued_at));
  }, [demo, collected.advisories]);

  const localAdvisories = useMemo(() => {
    if (demo) return [...demoAdvisories].sort((a, b) => b.issued_at.localeCompare(a.issued_at));
    return activeAdvisories.filter(
      a => a.text_status !== 'archive' && (a.provinces.length === 0 || a.provinces.includes(place.province)),
    );
  }, [demo, activeAdvisories, place.province]);

  const cropProfilesMap = useMemo(() => {
    if (demo) {
      const m: Record<string, CropProfile> = {};
      for (const loc of locations) {
        if (!m[loc.province]) m[loc.province] = demoCropProfile(loc);
      }
      return m;
    }
    return collected.crops?.provinces ?? null;
  }, [demo, locations, collected.crops]);

  const selectedCropProfile = cropProfilesMap?.[place.province] ?? (demo ? demoCropProfile(place) : null);

  const rankedProvinces = useMemo(() => {
    if (!cropProfilesMap) return [];
    const q = cropFilter.trim().toLowerCase();
    return Object.values(cropProfilesMap)
      .filter(p => !q || p.province.toLowerCase().includes(q) || p.region.toLowerCase().includes(q))
      .sort((a, b) => (b.palay_production_mt ?? -1) - (a.palay_production_mt ?? -1));
  }, [cropProfilesMap, cropFilter]);

  const selectProvinceOnMap = (provinceName: string, nextOverlay?: MapOverlayMode) => {
    const match = locations.find(l => l.province === provinceName);
    if (match) {
      userChosePlace.current = true;
      setPlace(match);
    }
    if (nextOverlay) setOverlayMode(nextOverlay);
    setDetailsOpen(true);
    setView('map');
  };

  const toggleDetailsCard = () => {
    if (typeof window !== 'undefined' && window.innerWidth <= 760) {
      setMobilePanel(m => (m === 'details' ? null : 'details'));
    } else {
      setDetailsOpen(o => !o);
    }
  };

  const dateStamp = active === 'vegetation' ? 'Oct 31, 2026' : 'Nov 1, 2026';

  return (
    <div className="app-shell">
      {error ? (
        <div className="error-state fullscreen-error">
          <Database size={30} />
          <h2>Nationwide workspace couldn’t load</h2>
          <p>Check your connection and try again.</p>
          <button className="primary-button" onClick={() => setReload(r => r + 1)}>
            Try again
          </button>
        </div>
      ) : !layer || !areas ? (
        <div className="error-state fullscreen-error">Loading nationwide boundaries and satellite layers…</div>
      ) : (
        <>
          {/* Full-bleed edge-to-edge interactive map canvas */}
          <main id="main" className="map-stage" aria-label="Climate data explorer">
            <Suspense fallback={<div className="map-loading">Loading nationwide map…</div>}>
              <MapView
                layer={layer}
                day={day}
                dark={dark}
                onToggleDark={() => setDark(d => !d)}
                place={place}
                onSelect={handleMapSelect}
                visible={visible}
                areas={areas}
                values={demo ? null : mapData.values}
                raster={demo ? null : mapData.raster}
                renderStyle={renderStyle}
                overlayMode={overlayMode}
                cropProfiles={cropProfilesMap}
                cropMetric={cropMetric}
                advisoryMap={demo ? null : collected.advisories?.province_map ?? null}
                mapLoading={!demo && (mapData.loading || (collected.loading && !dateKeys.length))}
                dataError={mapData.error}
                detailsOpen={detailsOpen || mobilePanel === 'details'}
                onToggleDetails={toggleDetailsCard}
                onOpenModal={setModal}
                userLocated={userLocated}
              />
            </Suspense>
          </main>

          {/* Top-left Zoom-Earth-style brand pill + floating collapsible layers dock */}
          <div className="hud-left-stack">
            <div className="hud-brand-pill">
              <a href="#main" className="brand-mark" aria-label="Ani home">
                <img src="/logo.jpg" alt="" />
                <span className="wordmark-text">ani</span>
              </a>
              <span className="hud-divider" />
              <div className="region-tag">
                <span className={`mode-dot ${demo ? 'demo' : 'live'}`} />
                <div>
                  <strong>{place.province}</strong>
                  <span>{place.name}</span>
                </div>
              </div>
            </div>

            <div className="mobile-map-actions">
              <button onClick={() => setMobilePanel(m => (m === 'layers' ? null : 'layers'))}>
                <Layers size={15} />
                Map layers
              </button>
            </div>

            <aside className={`layer-panel ${layersCollapsed ? 'collapsed' : ''} ${mobilePanel === 'layers' ? 'mobile-open' : ''}`}>
              <div className="panel-heading">
                <h2>Map layers</h2>
                <button
                  className="desktop-collapse"
                  aria-label={layersCollapsed ? 'Expand layers' : 'Collapse layers'}
                  onClick={() => setLayersCollapsed(c => !c)}
                >
                  {layersCollapsed ? <ChevronDown size={15} /> : <ChevronUp size={15} />}
                </button>
                <button className="mobile-close" aria-label="Close layers" onClick={() => setMobilePanel(null)}>
                  <X size={17} />
                </button>
              </div>

              {!layersCollapsed && (
                <div className="layer-panel-body">
                  <div className="overlay-mode-pills" role="group" aria-label="Map thematic mode">
                    <button className={overlayMode === 'climate' ? 'active' : ''} onClick={() => setOverlayMode('climate')}>
                      Satellite
                    </button>
                    <button className={overlayMode === 'crops' ? 'active' : ''} onClick={() => setOverlayMode('crops')}>
                      Crops
                    </button>
                    <button className={overlayMode === 'advisories' ? 'active' : ''} onClick={() => setOverlayMode('advisories')}>
                      PAGASA
                    </button>
                  </div>

                  {overlayMode === 'crops' && (
                    <div className="submode-row" role="group" aria-label="Crop choropleth metric">
                      <button className={cropMetric === 'palay' ? 'active' : ''} onClick={() => setCropMetric('palay')}>
                        Palay (MT)
                      </button>
                      <button className={cropMetric === 'corn' ? 'active' : ''} onClick={() => setCropMetric('corn')}>
                        Corn (MT)
                      </button>
                    </div>
                  )}

                  <div className="layer-list">
                    {catalog.map(l => {
                      const Icon = icons[l.id];
                      const isSelected = active === l.id && overlayMode === 'climate';
                      return (
                        <button
                          key={l.id}
                          className={`layer-option ${isSelected ? 'active' : ''}`}
                          aria-pressed={isSelected}
                          onClick={() => {
                            setActive(l.id);
                            setOverlayMode('climate');
                            setVisible(true);
                          }}
                        >
                          <span className={`layer-icon ${l.id}`}>
                            <Icon size={16} />
                          </span>
                          <span>
                            <strong>{l.name}</strong>
                            <small>{layerSubtitles[l.id]}</small>
                          </span>
                          <span className="radio-indicator">{isSelected && <span />}</span>
                        </button>
                      );
                    })}
                  </div>

                  <div className="overlay-settings">
                    {overlayMode === 'climate' && !demo && (
                      <div className="render-toggle" role="group" aria-label="Spatial display style">
                        <button
                          className={renderStyle === 'raster' ? 'active' : ''}
                          onClick={() => setRenderStyle('raster')}
                          title="Satellite raster + 90% municipal land base"
                        >
                          Raster grid
                        </button>
                        <button
                          className={renderStyle === 'polygons' ? 'active' : ''}
                          onClick={() => setRenderStyle('polygons')}
                          title="Municipality choropleth at 90% fill"
                        >
                          Municipal fill
                        </button>
                      </div>
                    )}
                    <button className="switch-row" aria-pressed={visible} onClick={() => setVisible(v => !v)}>
                      <span>Show on map</span>
                      <span className={`switch ${visible ? 'on' : ''}`}>
                        <span />
                      </span>
                    </button>
                  </div>

                  <div className="layer-source">
                    <span className="tiny-label">Active feed · 90% fill</span>
                    <strong>{demo ? layer.source : selectedSource?.name ?? mapData.raster?.source_name ?? layer.source}</strong>
                    <span>
                      {demo ? layer.resolution : selectedSource?.resolution ?? layer.resolution} ·{' '}
                      <em className={`inline-freshness ${freshnessClass(layer.status)}`}>{layer.status}</em>
                    </span>
                    <button onClick={() => setView('sources')}>
                      View data sources
                      <ArrowUpRight size={13} />
                    </button>
                  </div>
                </div>
              )}
            </aside>
          </div>

          {/* Top floating capsules: Google-Maps search + workspace view pills + data sync */}
          <header className="hud-top-bar">
            <div className="view-tabs" role="tablist" aria-label="Workspace view">
              <button role="tab" aria-selected={view === 'map'} className={view === 'map' ? 'active' : ''} onClick={() => setView('map')}>
                <MapIcon size={14} /> Explore map
              </button>
              <button
                role="tab"
                aria-selected={view === 'advisories'}
                className={view === 'advisories' ? 'active' : ''}
                onClick={() => setView('advisories')}
              >
                <Radio size={14} /> PAGASA advisories{' '}
                {activeAdvisories.length > 0 && <span className="count">{activeAdvisories.length}</span>}
              </button>
              <button
                role="tab"
                aria-selected={view === 'crops'}
                className={view === 'crops' ? 'active' : ''}
                onClick={() => setView('crops')}
              >
                <Calendar size={14} /> Crops &amp; stats
              </button>
              <button
                role="tab"
                aria-selected={view === 'sources'}
                className={view === 'sources' ? 'active' : ''}
                onClick={() => setView('sources')}
              >
                <Database size={14} /> Data sources <span className="count">{demo ? 6 : collected.sources.length || 6}</span>
              </button>
            </div>

            <CollectionBar demo={demo} setDemo={setDemo} data={collected} placeName={place.name} />

            <div className="search" ref={searchBox}>
              <Search size={15} />
              <input
                aria-label="Find a place in the Philippines"
                placeholder="Find a place in the Philippines…"
                value={search}
                onChange={e => {
                  setSearch(e.target.value);
                  setSearchOpen(true);
                }}
                onFocus={() => setSearchOpen(true)}
                onKeyDown={e => {
                  if (e.key === 'Escape') setSearchOpen(false);
                  if (e.key === 'Enter' && matches[0]) {
                    choose(matches[0]);
                  }
                }}
                aria-expanded={searchOpen}
                aria-controls="search-results"
              />
              {search && (
                <button aria-label="Clear search" onClick={() => setSearch('')}>
                  <X size={14} />
                </button>
              )}
              {searchOpen && (
                <div className="search-results" id="search-results">
                  <small>
                    {search.trim()
                      ? `${allMatches.length.toLocaleString()} matching places · Press Enter for top match`
                      : `Suggested cities · ${locations.length.toLocaleString()} cities & municipalities`}
                  </small>
                  {matches.map(p => {
                    const friendly = friendlyPlaceName(p.name);
                    return (
                      <button key={p.id} onClick={() => choose(p)}>
                        <MapPin size={14} />
                        <span>
                          {friendly}
                          <small>
                            {friendly !== p.name ? `${p.name} · ` : ''}
                            {p.province} · {p.region}
                          </small>
                        </span>
                        {place.id === p.id && <Check size={15} />}
                      </button>
                    );
                  })}
                  {allMatches.length > 50 && (
                    <p>Showing 50 of {allMatches.length} matches. Add a province or more of the place name to narrow your search.</p>
                  )}
                  {!matches.length && <p>No matching place. Try a city, municipality, or province name, or click land on the map.</p>}
                </div>
              )}
            </div>
          </header>

          {/* Floating Google-Maps-style place inspector card */}
          {view === 'map' && (
            <aside className={`details-panel ${detailsOpen ? 'desktop-open' : 'desktop-closed'} ${mobilePanel === 'details' ? 'mobile-open' : ''}`}>
              <div className="detail-eyebrow">
                <MapPin size={13} /> Selected location
                <button
                  className="card-close"
                  aria-label="Close details"
                  onClick={() => {
                    setDetailsOpen(false);
                    setMobilePanel(null);
                  }}
                >
                  <X size={15} />
                </button>
              </div>
              <h2>{place.name}</h2>
              <p className="coordinates">
                {place.lat.toFixed(3)}° N, {place.lon.toFixed(3)}° E · {place.region}
              </p>
              <span className="crop-tag">
                <MapPin size={13} /> {place.province} <span>·</span>{' '}
                {demo ? 'Demo area' : selectedSource ? selectedSource.name.split('(')[0].trim() : 'Satellite / grid pixel'}
              </span>

              <div className="measurement">
                <div className="measurement-label">
                  {layer.name} · {demo ? 'Municipal average' : 'Observed pixel'}
                  <span title={demo ? 'Simulated data' : 'Observed satellite or reanalysis grid pixel'}>
                    <Info size={13} />
                  </span>
                </div>
                <div className="measurement-value">
                  {collected.loading && !demo ? '…' : formatReading(activeValue, active)}
                  <span>{layer.unit}</span>
                </div>
                <p>
                  {isCloudMaskedPoint
                    ? 'Cloud-masked optical composite pixel (no synthetic fill)'
                    : activeValue === null
                      ? 'No collected value for this date'
                      : change === null
                        ? moistureBalance !== null && (active === 'rainfall' || active === 'evapotranspiration')
                          ? `Moisture balance (Rain − ET₀): ${moistureBalance >= 0 ? '+' : ''}${moistureBalance} mm`
                          : 'Latest available observation'
                        : `${change >= 0 ? '+' : ''}${change} ${layer.unit} from previous day`}
                </p>
              </div>

              <div className="chart-heading">
                <strong>Past 7 days</strong>
                <span>{layer.unit}</span>
              </div>
              <div
                className="bar-chart"
                role="img"
                aria-label={`${layer.name} over the displayed dates: ${series.join(', ')} ${layer.unit}`}
              >
                {series.map((v, i) => (
                  <button
                    key={i}
                    className={i === day ? 'current' : ''}
                    onClick={() => setDay(i)}
                    aria-label={`${dates[i]}: ${v} ${layer.unit}`}
                    style={{ height: `${v === null ? 6 : Math.max(6, (v / max) * 100)}%`, opacity: v === null ? 0.22 : 1 }}
                  >
                    <span>{v ?? 'No data'}</span>
                  </button>
                ))}
              </div>
              <div className="chart-axis">
                <span>{dates[0] ?? 'No dates'}</span>
                <span>{dates[dates.length - 1] ?? ''}</span>
              </div>

              <div className="conditions-list">
                <h3>At this location</h3>
                {catalog
                  .filter(l => l.id !== active)
                  .map(l => {
                    const Icon = icons[l.id];
                    const val = valueFor(l.id);
                    const obs = obsFor(l.id);
                    return (
                      <button
                        key={l.id}
                        onClick={() => {
                          setActive(l.id);
                          setOverlayMode('climate');
                        }}
                      >
                        <Icon size={14} />
                        <span>{l.name}</span>
                        <strong>
                          {!demo && obs?.status === 'cloud-masked' ? (
                            <small>Cloud-masked</small>
                          ) : (
                            <>
                              {formatReading(val, l.id)}
                              <small> {l.unit}</small>
                            </>
                          )}
                        </strong>
                      </button>
                    );
                  })}
              </div>

              {selectedCropProfile && (
                <div className="mini-context-links">
                  <button onClick={() => setView('crops')}>
                    <Sprout size={13} />
                    <span>
                      <strong>{selectedCropProfile.dominant_crop}</strong> · {selectedCropProfile.calendar.current_stage}
                    </span>
                    <ArrowUpRight size={12} />
                  </button>
                  {localAdvisories[0] && (
                    <button onClick={() => setView('advisories')}>
                      <Radio size={13} />
                      <span>
                        <strong>PAGASA:</strong> {localAdvisories[0].title}
                      </span>
                      <ArrowUpRight size={12} />
                    </button>
                  )}
                </div>
              )}

              <div className={`data-quality ${(demo ? layer.status === 'Available' : activeValue !== null) ? '' : 'warning'}`}>
                <span className="live-dot" />
                <div>
                  <strong>
                    {demo
                      ? layer.status === 'Available'
                        ? 'Sample data available'
                        : layer.status
                      : isCloudMaskedPoint
                        ? 'Cloud-masked pixel'
                        : activeValue === null
                          ? 'No data for this date'
                          : selectedSource?.name ?? 'Collected data'}
                  </strong>
                  <span>
                    {demo
                      ? layer.status === 'Cloud-masked'
                        ? 'Clouds can hide parts of the crop canopy.'
                        : `Sample source update: ${dateStamp}`
                      : selectedObservation
                        ? `${selectedObservation.date} · ${selectedObservation.quality}`
                        : 'Select a valid date or refresh collected data.'}
                  </span>
                </div>
              </div>
            </aside>
          )}

          {/* Bottom-center Zoom-Earth-style floating timeline dock */}
          {view === 'map' && (
            <div className="timeline" role="region" aria-label="Observation timeline">
              <div className="timeline-heading">
                <span>
                  <span className="calendar-icon">
                    {demo ? (day === 6 ? '01' : 26 + day) : dateKeys[day]?.slice(-2) ?? '—'}
                  </span>
                  <strong>{fullDate(day)}</strong>
                </span>
                <span className="timeline-period">
                  {demo
                    ? 'Simulated 7-day window'
                    : mapData.compositeDate && mapData.compositeDate !== dateKeys[day]
                      ? `Composite ${mapData.compositeDate}`
                      : selectedSource?.name.split('(')[0].trim() ?? '7-day window'}
                </span>
              </div>
              <div className="timeline-control">
                <button
                  aria-label="Previous day"
                  disabled={day === 0 || (!demo && !dateKeys.length)}
                  onClick={() => setDay(d => d - 1)}
                >
                  <ChevronLeft size={16} />
                </button>
                <div className="timeline-track">
                  <input
                    aria-label="Observation date"
                    type="range"
                    disabled={!demo && !dateKeys.length}
                    min="0"
                    max={Math.max(0, (dates.length || 7) - 1)}
                    value={day}
                    onChange={e => setDay(+e.target.value)}
                    aria-valuetext={fullDate(day)}
                  />
                  <div className="date-labels">
                    {dates.map((date, i) => (
                      <button
                        key={date}
                        className={i === day ? 'selected' : ''}
                        onClick={() => setDay(i)}
                        aria-label={`Show ${fullDate(i)}`}
                      >
                        {date}
                      </button>
                    ))}
                  </div>
                </div>
                <button
                  aria-label="Next day"
                  disabled={day >= (dates.length || 7) - 1 || (!demo && !dateKeys.length)}
                  onClick={() => setDay(d => d + 1)}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          )}

          {/* Floating overlay sheets over the live map for Advisories, Crops & stats, and Data sources */}
          {view !== 'map' && (
            <div
              className="floating-sheet-backdrop"
              onClick={e => {
                if (e.target === e.currentTarget) setView('map');
              }}
            >
              {view === 'sources' ? (
                <section className="sources-page" aria-label="Data inventory">
                  <div className="section-heading">
                    <div>
                      <h2>Complete Phase 1 data inventory.</h2>
                      <p>Explicit date coverage ranges, georeferenced rasters, and live freshness states across all 1,642 municipalities.</p>
                    </div>
                    <div className="heading-actions">
                      <span className="subtle-badge">{demo ? 'Sample catalog' : 'Live inventory · Nov 1, 2026'}</span>
                      {!demo && (
                        <button
                          className="secondary-button"
                          disabled={collected.running || collected.submitting}
                          onClick={() => collected.collect('national_weather')}
                        >
                          Collect nationwide weather
                        </button>
                      )}
                      <button className="secondary-button" onClick={() => setView('map')}>
                        <X size={14} /> Back to map
                      </button>
                    </div>
                  </div>

                  <div className="source-notice">
                    <Info size={16} />
                    <p>
                      {demo
                        ? 'These records demonstrate the Phase 1 catalog with simulated values.'
                        : 'Freshness states are evaluated from stored dates and optical quality flags: Available (within upstream cadence), Stale (behind expected publication lag), Cloud-masked (optical land pixel obscured by clouds), or Missing (not yet collected).'}
                    </p>
                  </div>

                  <h3 className="inventory-subheading">Map layers ({catalog.length})</h3>
                  <div tabIndex={0} role="region" aria-label="Map layer coverage inventory" className="table-scroll">
                    <table
                      style={
                        {
                          '--label-2': '"Primary source"',
                          '--label-3': '"Resolution"',
                          '--label-4': '"Coverage range"',
                          '--label-5': '"Rasters"',
                          '--label-6': '"Freshness"',
                        } as React.CSSProperties
                      }
                    >
                      <thead>
                        <tr>
                          <th>Map layer</th>
                          <th>{demo ? 'Planned source' : 'Primary source'}</th>
                          <th>Resolution</th>
                          <th>Coverage range</th>
                          <th>Rasters</th>
                          <th>Freshness</th>
                          <th>
                            <span className="sr-only">Actions</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {catalog.map(l => {
                          const Icon = icons[l.id];
                          return (
                            <tr key={l.id}>
                              <th scope="row">
                                <span className="table-layer">
                                  <Icon size={16} />
                                  <strong>{l.name}</strong>
                                </span>
                              </th>
                              <td>
                                <a href={sourceLinks[l.id]} target="_blank" rel="noreferrer">
                                  {l.source}
                                  <ExternalLink size={12} />
                                </a>
                              </td>
                              <td>{l.resolution}</td>
                              <td className="mono-dates">{l.coverageRange}</td>
                              <td>{l.rastersCount ? `${l.rastersCount} PNG` : 'Point only'}</td>
                              <td>
                                <span className={`status-pill ${freshnessClass(l.status)}`} title={l.freshnessReason}>
                                  {l.status}
                                </span>
                              </td>
                              <td>
                                <button
                                  className="text-button"
                                  onClick={() => {
                                    setActive(l.id);
                                    setOverlayMode('climate');
                                    setView('map');
                                  }}
                                >
                                  View layer
                                  <ArrowUpRight size={14} />
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  {!demo && collected.sources.length > 0 && (
                    <>
                      <h3 className="inventory-subheading">Upstream satellite, reanalysis &amp; agency feeds ({collected.sources.length})</h3>
                      <div tabIndex={0} role="region" aria-label="Upstream data sources inventory" className="table-scroll">
                        <table
                          style={
                            {
                              '--label-2': '"Type & resolution"',
                              '--label-3': '"Coverage range"',
                              '--label-4': '"Stored records"',
                              '--label-5': '"Freshness"',
                            } as React.CSSProperties
                          }
                        >
                          <thead>
                            <tr>
                              <th>Upstream source</th>
                              <th>Type &amp; resolution</th>
                              <th>Coverage range</th>
                              <th>Stored records</th>
                              <th>Freshness</th>
                            </tr>
                          </thead>
                          <tbody>
                            {collected.sources.map(src => {
                              const fLabel = freshnessText(src.freshness);
                              return (
                                <tr key={src.id ?? src.name}>
                                  <th scope="row">
                                    <a href={src.url} target="_blank" rel="noreferrer">
                                      <strong>{src.name}</strong>
                                      <ExternalLink size={12} />
                                    </a>
                                  </th>
                                  <td>
                                    {src.kind} · {src.resolution}
                                  </td>
                                  <td className="mono-dates">{formatCoverage(src.coverage_start, src.coverage_end)}</td>
                                  <td>
                                    {(src.samples ?? 0).toLocaleString()} records · {src.areas ?? 0} areas
                                    {src.rasters_count ? ` · ${src.rasters_count} rasters` : ''}
                                    {src.cloud_masked_samples ? ` · ${src.cloud_masked_samples.toLocaleString()} cloud-masked` : ''}
                                  </td>
                                  <td>
                                    <div className="freshness-cell">
                                      <span className={`status-pill ${freshnessClass(fLabel)}`}>{fLabel}</span>
                                      {src.freshness_reason && <small>{src.freshness_reason}</small>}
                                    </div>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </section>
              ) : view === 'advisories' ? (
                <section className="sources-page" aria-label="Official PAGASA and DOST advisories">
                  <div className="section-heading">
                    <div>
                      <h2>Official DOST-PAGASA weather &amp; farm advisories.</h2>
                      <p>Public weather synopsis, regional hazard outlooks, and agro-meteorological bulletins for {place.province}.</p>
                    </div>
                    <div className="heading-actions">
                      <button
                        className="secondary-button"
                        onClick={() => {
                          setOverlayMode('advisories');
                          setView('map');
                        }}
                      >
                        <MapIcon size={14} /> Show advisory zones on map
                      </button>
                      <button className="secondary-button" onClick={() => setView('map')}>
                        <X size={14} /> Back to map
                      </button>
                    </div>
                  </div>

                  {localAdvisories.length > 0 && (
                    <div className="local-advisory-banner">
                      <div className="banner-eyebrow">
                        <MapPin size={14} /> Active guidance for <strong>{place.name}, {place.province}</strong>
                      </div>
                      <div className="advisory-grid">
                        {localAdvisories.map(adv => (
                          <article key={adv.id} className="advisory-card">
                            <div className="advisory-meta">
                              <span className={`status-pill ${adv.text_status === 'warning' ? 'warn' : 'ok'}`}>{adv.product}</span>
                              <small>{adv.issued_at.slice(0, 16).replace('T', ' ')}</small>
                            </div>
                            <h3>{adv.title}</h3>
                            <p>{adv.excerpt}</p>
                            <a href={adv.url} target="_blank" rel="noreferrer" className="text-button">
                              Official PAGASA source <ExternalLink size={12} />
                            </a>
                          </article>
                        ))}
                      </div>
                    </div>
                  )}

                  <h3 className="inventory-subheading">All collected PAGASA forecasts &amp; bulletins</h3>
                  {!activeAdvisories.length ? (
                    <div className="source-notice">
                      <Info size={16} />
                      <p>No PAGASA advisories stored yet. Click “Sync satellites &amp; advisories” above to retrieve live DOST-PAGASA bulletins.</p>
                    </div>
                  ) : (
                    <div tabIndex={0} role="region" aria-label="PAGASA advisories table" className="table-scroll">
                      <table
                        style={
                          {
                            '--label-2': '"Bulletin"',
                            '--label-3': '"Issued"',
                            '--label-4': '"Summary & conditions"',
                            '--label-5': '"Source"',
                          } as React.CSSProperties
                        }
                      >
                        <thead>
                          <tr>
                            <th>Product</th>
                            <th>Coverage / Title</th>
                            <th>Issued</th>
                            <th>Summary &amp; farm conditions</th>
                            <th>Source</th>
                          </tr>
                        </thead>
                        <tbody>
                          {activeAdvisories.map(adv => (
                            <tr key={adv.id}>
                              <th scope="row">
                                <span className={`status-pill ${adv.text_status === 'warning' ? 'warn' : adv.text_status === 'archive' ? 'masked' : 'ok'}`}>
                                  {adv.product}
                                </span>
                              </th>
                              <td>
                                <strong>{adv.title}</strong>
                                {adv.provinces.length > 0 && adv.provinces.length < 30 && (
                                  <small className="cell-sub">
                                    {adv.provinces.slice(0, 6).join(', ')}
                                    {adv.provinces.length > 6 ? ` +${adv.provinces.length - 6} more` : ''}
                                  </small>
                                )}
                              </td>
                              <td className="mono-dates">{adv.issued_at.slice(0, 16).replace('T', ' ')}</td>
                              <td className="wrap-cell">{adv.excerpt}</td>
                              <td>
                                <a href={adv.url} target="_blank" rel="noreferrer" className="text-button">
                                  <FileText size={13} /> Open <ExternalLink size={12} />
                                </a>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              ) : (
                <section className="sources-page" aria-label="Crop calendars and farm statistics">
                  <div className="section-heading">
                    <div>
                      <h2>Crop calendar &amp; PSA farm statistics.</h2>
                      <p>Quarterly palay and corn production, harvested hectarage, and DA/PhilRice planting-harvest windows.</p>
                    </div>
                    <div className="heading-actions">
                      <button
                        className="secondary-button"
                        onClick={() => {
                          setOverlayMode('crops');
                          setView('map');
                        }}
                      >
                        <MapIcon size={14} /> View provincial choropleth
                      </button>
                      <button className="secondary-button" onClick={() => setView('map')}>
                        <X size={14} /> Back to map
                      </button>
                    </div>
                  </div>

                  {selectedCropProfile && (
                    <div className="crop-province-hero">
                      <div className="crop-summary-main">
                        <span className="subtle-badge">
                          {selectedCropProfile.province} · {selectedCropProfile.region}
                        </span>
                        <h3>{selectedCropProfile.calendar.current_stage}</h3>
                        <p className="crop-windows-line">
                          <strong>Wet season:</strong> Plant {selectedCropProfile.calendar.wet_season.planting} · Harvest{' '}
                          {selectedCropProfile.calendar.wet_season.harvest} &nbsp;|&nbsp; <strong>Dry season:</strong> Plant{' '}
                          {selectedCropProfile.calendar.dry_season.planting} · Harvest {selectedCropProfile.calendar.dry_season.harvest}
                        </p>
                        {selectedCropProfile.note && <p className="crop-note">{selectedCropProfile.note}</p>}

                        <div className="crop-metrics-strip">
                          <div>
                            <span>Annual palay ({selectedCropProfile.reference_year ?? 'PSA'})</span>
                            <strong>
                              {selectedCropProfile.palay_production_mt != null
                                ? `${Math.round(selectedCropProfile.palay_production_mt).toLocaleString()} MT`
                                : '—'}
                            </strong>
                            <small>
                              {selectedCropProfile.palay_area_ha != null
                                ? `${Math.round(selectedCropProfile.palay_area_ha).toLocaleString()} ha · ${selectedCropProfile.palay_yield_mt_ha ?? '—'} MT/ha`
                                : 'No palay hectarage'}
                            </small>
                          </div>
                          <div>
                            <span>Irrigated palay share</span>
                            <strong>
                              {selectedCropProfile.irrigated_share_pct != null ? `${selectedCropProfile.irrigated_share_pct}%` : '—'}
                            </strong>
                            <small>
                              Peak harvest: {selectedCropProfile.peak_harvest_quarter ?? '—'} (Wet {selectedCropProfile.wet_season_share_pct ?? '—'}%)
                            </small>
                          </div>
                          <div>
                            <span>Annual corn ({selectedCropProfile.reference_year ?? 'PSA'})</span>
                            <strong>
                              {selectedCropProfile.corn_production_mt != null
                                ? `${Math.round(selectedCropProfile.corn_production_mt).toLocaleString()} MT`
                                : '—'}
                            </strong>
                            <small>
                              {selectedCropProfile.corn_area_ha != null
                                ? `${Math.round(selectedCropProfile.corn_area_ha).toLocaleString()} ha · ${selectedCropProfile.corn_yield_mt_ha ?? '—'} MT/ha`
                                : 'No corn hectarage'}
                            </small>
                          </div>
                        </div>
                      </div>

                      <div className="quarter-calendar">
                        <h4>Seasonal harvest distribution &amp; crop stages</h4>
                        {selectedCropProfile.calendar.quarters.map(q => {
                          const qKey = q.quarter.slice(0, 2);
                          const ytd = selectedCropProfile.ytd_2026_palay_mt[qKey];
                          return (
                            <div key={q.quarter} className={`quarter-row ${qKey === 'Q4' ? 'active-quarter' : ''}`}>
                              <div className="quarter-head">
                                <strong>{q.quarter}</strong>
                                <span>
                                  {q.palay_share_pct}% of annual palay
                                  {ytd != null ? ` · 2026: ${Math.round(ytd).toLocaleString()} MT` : ''}
                                </span>
                              </div>
                              <div className="quarter-bar">
                                <div style={{ width: `${Math.min(100, q.palay_share_pct * 1.8)}%` }} />
                              </div>
                              <small>{q.stage}</small>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  <div className="crop-table-header">
                    <h3 className="inventory-subheading">Provincial production &amp; harvest calendar comparison ({rankedProvinces.length})</h3>
                    <input
                      type="search"
                      className="inline-filter"
                      placeholder="Filter province or region…"
                      aria-label="Filter provinces"
                      value={cropFilter}
                      onChange={e => setCropFilter(e.target.value)}
                    />
                  </div>

                  <div tabIndex={0} role="region" aria-label="Provincial crop statistics table" className="table-scroll">
                    <table
                      style={
                        {
                          '--label-2': '"Palay (MT)"',
                          '--label-3': '"Yield & irrigation"',
                          '--label-4': '"Corn (MT)"',
                          '--label-5': '"Harvest windows"',
                          '--label-6': '"Map"',
                        } as React.CSSProperties
                      }
                    >
                      <thead>
                        <tr>
                          <th>Province</th>
                          <th>Palay volume (MT)</th>
                          <th>Yield &amp; irrigated</th>
                          <th>Corn volume (MT)</th>
                          <th>Wet / Dry harvest windows</th>
                          <th>
                            <span className="sr-only">Explore</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {rankedProvinces.slice(0, 40).map(p => (
                          <tr key={p.province} className={p.province === place.province ? 'selected-row' : ''}>
                            <th scope="row">
                              <strong>{p.province}</strong>
                              <small className="cell-sub">{p.region}</small>
                            </th>
                            <td className="mono-dates">
                              {p.palay_production_mt != null ? Math.round(p.palay_production_mt).toLocaleString() : '—'}
                            </td>
                            <td>
                              {p.palay_yield_mt_ha != null ? `${p.palay_yield_mt_ha} MT/ha` : '—'}
                              {p.irrigated_share_pct != null ? ` · ${p.irrigated_share_pct}% irr.` : ''}
                            </td>
                            <td className="mono-dates">
                              {p.corn_production_mt != null ? Math.round(p.corn_production_mt).toLocaleString() : '—'}
                            </td>
                            <td>
                              Wet: {p.calendar.wet_season.harvest} · Dry: {p.calendar.dry_season.harvest}
                            </td>
                            <td>
                              <button className="text-button" onClick={() => selectProvinceOnMap(p.province, 'crops')}>
                                On map <ArrowUpRight size={13} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
            </div>
          )}
        </>
      )}

      <dialog
        ref={dialog}
        onCancel={() => setModal(null)}
        onClick={e => {
          if (e.target === e.currentTarget) setModal(null);
        }}
      >
        <button className="dialog-close" aria-label="Close dialog" onClick={() => setModal(null)}>
          <X size={20} />
        </button>
        {modal === 'help' ? (
          <>
            <div className="dialog-icon">
              <Sprout size={26} />
            </div>
            <h2>A clearer view, one layer at a time.</h2>
            <p>Ani brings together satellite earth observation, agro-meteorology, and official Philippine farm statistics on a full-screen map.</p>
            <ol>
              <li>
                <strong>Explore live satellite &amp; gridded layers.</strong> Switch between CHIRPS &amp; IMERG rainfall, Himawari-9 infrared cloud imagery, SMAP soil moisture, MODIS NDVI, temperature, and evapotranspiration across all 1,642 municipalities at 90% fill.
              </li>
              <li>
                <strong>Compare official guidance.</strong> Open floating PAGASA advisories and PSA provincial crop calendars without leaving the map.
              </li>
              <li>
                <strong>Audit data freshness.</strong> Open Data sources for explicit date coverage and Available, Stale, Cloud-masked, or Missing states.
              </li>
            </ol>
            <div className="source-notice">
              <Info size={18} />
              <p>
                Cloud-masked optical pixels are explicitly flagged rather than filled with synthetic values. Administrative boundaries use PSGC 3Q 2026.
              </p>
            </div>
          </>
        ) : (
          <>
            <div className="dialog-icon">
              <Bell size={26} />
            </div>
            <h2>Field updates, closer to you.</h2>
            <p>Telegram and Messenger alerts are planned for Phase 6 so farmers receive actionable advisories without opening the dashboard.</p>
            <div className="alert-preview">
              <span className="subtle-badge">Illustrative message</span>
              <strong>Field update · {place.name}</strong>
              <p>New satellite rainfall and PAGASA farm weather updates are available for {place.province}.</p>
            </div>
            <p className="dialog-note">Subscriptions and bot message delivery are scheduled for Phase 6.</p>
          </>
        )}
        <button className="primary-button" onClick={() => setModal(null)}>
          Back to exploring
        </button>
      </dialog>
    </div>
  );
}
