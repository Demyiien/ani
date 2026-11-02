import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { Bell, HelpCircle, LocateFixed, MapPin, Minus, Moon, Plus, RotateCcw, Sun, X } from 'lucide-react';
import type { Layer, Place } from './data';
import { reading } from './data';
import { findArea } from './geography';
import type { Areas } from './geography';
import type { CropProfile, Observation, ProvinceAdvisoryStatus, RasterMeta } from './useCollectedData';

export type MapOverlayMode = 'climate' | 'crops' | 'advisories';
export type ClimateRenderStyle = 'raster' | 'polygons';
export type CropMetricMode = 'palay' | 'corn';

const STATIC_FILL_OPACITY = 0.9;
const PALAY_RAMP = ['#edf3e8', '#c6dec0', '#8ec08c', '#4d935c', '#23633c'];
const CORN_RAMP = ['#f8f0d8', '#edd699', '#dfb05b', '#c48332', '#8f5319'];

function colorAreas(
  areas: Areas,
  layer: Layer,
  day: number,
  values: Record<string, Observation> | null,
  overlayMode: MapOverlayMode,
  cropProfiles: Record<string, CropProfile> | null,
  cropMetric: CropMetricMode,
  advisoryMap: Record<string, ProvinceAdvisoryStatus> | null,
) {
  if (overlayMode === 'crops') {
    const ramp = cropMetric === 'palay' ? PALAY_RAMP : CORN_RAMP;
    const scaleMax = cropMetric === 'palay' ? 600000 : 350000;
    return {
      ...areas,
      features: areas.features.map(f => {
        const prof = cropProfiles?.[f.properties.province];
        const rawVal = cropMetric === 'palay' ? prof?.palay_production_mt : prof?.corn_production_mt;
        const hasVal = rawVal != null && rawVal > 0;
        const bucket = hasVal ? Math.max(0, Math.min(4, Math.floor((rawVal / scaleMax) * 5))) : 0;
        return {
          ...f,
          properties: {
            ...f.properties,
            color: hasVal ? ramp[bucket] : '#c4cfcb',
            available: hasVal,
          },
        };
      }),
    };
  }

  if (overlayMode === 'advisories') {
    return {
      ...areas,
      features: areas.features.map(f => {
        const adv = advisoryMap?.[f.properties.province];
        const color = !adv
          ? '#c4cfcb'
          : adv.severity === 'warning'
            ? '#d08b3e'
            : '#55967e';
        return {
          ...f,
          properties: {
            ...f.properties,
            color,
            available: !!adv,
          },
        };
      }),
    };
  }

  return {
    ...areas,
    features: areas.features.map(f => {
      const obs = values === null ? null : values[f.properties.id];
      const value = values === null ? reading(layer.id, day, f.properties) : obs?.value ?? null;
      const isCloudMasked = values !== null && obs?.status === 'cloud-masked';
      const minVal = layer.id === 'temperature' ? 18 : 0;
      const color =
        value != null
          ? layer.colors[Math.max(0, Math.min(4, Math.floor(((value - minVal) / Math.max(1e-6, layer.max - minVal)) * 5)))]
          : isCloudMasked
            ? '#9aa8b6'
            : '#aab9b3';
      return {
        ...f,
        properties: {
          ...f.properties,
          color,
          available: value != null || isCloudMasked,
        },
      };
    }),
  };
}

const nationalBounds: maplibregl.LngLatBoundsLike = [[116.8, 4.5], [126.8, 21.3]];

function responsiveBoundsPadding() {
  if (typeof window === 'undefined' || window.innerWidth <= 760) {
    return { top: 96, bottom: 110, left: 18, right: 18 };
  }
  return { top: 68, bottom: 92, left: 245, right: 335 };
}

export default function MapView({
  layer,
  day,
  dark,
  onToggleDark,
  place,
  onSelect,
  visible,
  areas,
  values,
  raster,
  renderStyle,
  overlayMode,
  cropProfiles,
  cropMetric,
  advisoryMap,
  mapLoading,
  dataError,
  detailsOpen,
  onToggleDetails,
  onOpenModal,
  userLocated = false,
}: {
  layer: Layer;
  day: number;
  dark: boolean;
  onToggleDark: () => void;
  place: Place;
  onSelect: (p: Place) => void;
  visible: boolean;
  areas: Areas;
  values: Record<string, Observation> | null;
  raster: RasterMeta | null;
  renderStyle: ClimateRenderStyle;
  overlayMode: MapOverlayMode;
  cropProfiles: Record<string, CropProfile> | null;
  cropMetric: CropMetricMode;
  advisoryMap: Record<string, ProvinceAdvisoryStatus> | null;
  mapLoading: boolean;
  dataError: string;
  detailsOpen: boolean;
  onToggleDetails: () => void;
  onOpenModal: (kind: 'help' | 'alerts') => void;
  userLocated?: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const select = useRef(onSelect);
  select.current = onSelect;
  const currentPlace = useRef(place);
  currentPlace.current = place;
  const previousPlace = useRef(place);
  const didCenterUser = useRef(false);
  const overview = useRef(true);
  const camera = useRef<{ center: [number, number]; zoom: number } | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [tileError, setTileError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [notice, setNotice] = useState('');
  const [noticeSeq, setNoticeSeq] = useState(0);
  const [noticeClosing, setNoticeClosing] = useState(false);
  const [hoverCoords, setHoverCoords] = useState<{ lat: number; lon: number } | null>(null);

  const showRaster =
    overlayMode === 'climate' &&
    renderStyle === 'raster' &&
    values !== null &&
    !!raster &&
    raster.source !== 'open_meteo_grid';

  useEffect(() => {
    if (!container.current) return;
    setFailed(false);
    setReady(false);
    setTileError(false);
    let instance: maplibregl.Map;
    try {
      instance = new maplibregl.Map({
        container: container.current,
        ...(camera.current && !overview.current
          ? { center: camera.current.center, zoom: camera.current.zoom }
          : { bounds: nationalBounds, fitBoundsOptions: { padding: responsiveBoundsPadding() } }),
        minZoom: 3.5,
        maxZoom: 17,
        maxBounds: [[110, 0], [134, 26]],
        attributionControl: { compact: true },
        style: dark ? '/basemap-dark.json' : '/basemap-light.json',
      });
    } catch {
      setFailed(true);
      return;
    }
    map.current = instance;
    const observer = new ResizeObserver(() => {
      const center = instance.getCenter();
      instance.resize();
      if (overview.current) instance.fitBounds(nationalBounds, { padding: responsiveBoundsPadding(), duration: 0 });
      else instance.setCenter(center);
    });
    observer.observe(container.current);

    instance.on('error', e => {
      if ((e as { sourceId?: string }).sourceId !== 'satellite-raster') setTileError(true);
    });
    instance.on('idle', () => {
      if (container.current) container.current.dataset.rendered = 'true';
    });
    instance.on('movestart', e => {
      if (container.current) container.current.dataset.rendered = 'false';
      if (e.originalEvent) overview.current = false;
    });
    instance.on('dataloading', () => {
      if (container.current) container.current.dataset.rendered = 'false';
    });
    instance.on('mousemove', e => {
      setHoverCoords({ lat: +e.lngLat.lat.toFixed(3), lon: +e.lngLat.lng.toFixed(3) });
    });
    instance.on('style.load', () => {
      instance.addSource('conditions', {
        type: 'geojson',
        data: colorAreas(areas, layer, day, values, overlayMode, cropProfiles, cropMetric, advisoryMap),
        attribution: '<a href="https://github.com/faeldon/philippines-json-maps">PH boundaries: NAMRIA/PSA</a>',
      });
      const firstLabel = instance.getStyle().layers.find(l => l.type === 'symbol')?.id;
      instance.addLayer(
        {
          id: 'conditions',
          type: 'fill',
          source: 'conditions',
          paint: {
            'fill-color': ['get', 'color'],
            'fill-opacity': ['case', ['get', 'available'], visible ? STATIC_FILL_OPACITY : 0, visible ? 0.22 : 0],
          },
        },
        firstLabel,
      );
      instance.addLayer(
        {
          id: 'municipality-borders',
          type: 'line',
          source: 'conditions',
          paint: { 'line-color': dark ? '#b8dcd3' : '#3d6857', 'line-width': 0.5, 'line-opacity': 0.36 },
        },
        firstLabel,
      );
      instance.addLayer(
        {
          id: 'selected-area',
          type: 'line',
          source: 'conditions',
          filter: ['==', ['get', 'id'], currentPlace.current.id],
          paint: { 'line-color': dark ? '#ecfff5' : '#1b503c', 'line-width': 2.2 },
        },
        firstLabel,
      );
      setReady(true);
    });
    instance.on('click', e => {
      const area = findArea(areas, e.lngLat.lng, e.lngLat.lat);
      if (!area) {
        setNoticeClosing(false);
        setNoticeSeq(s => s + 1);
        setNotice('No land coverage at this point. Select any Philippine island or municipality on land.');
        return;
      }
      setNotice('');
      setNoticeClosing(false);
      select.current({ ...area.properties, lat: +e.lngLat.lat.toFixed(6), lon: +e.lngLat.lng.toFixed(6) });
    });
    instance.on('mouseenter', 'conditions', () => {
      instance.getCanvas().style.cursor = 'pointer';
    });
    instance.on('mouseleave', 'conditions', () => {
      instance.getCanvas().style.cursor = '';
    });
    return () => {
      const c = instance.getCenter();
      camera.current = { center: [c.lng, c.lat], zoom: instance.getZoom() };
      observer.disconnect();
      instance.remove();
      map.current = null;
    };
  }, [dark, attempt, areas]);

  useEffect(() => {
    if (!ready || !map.current) return;
    if (mapLoading) {
      if (container.current) container.current.dataset.rendered = 'false';
      return;
    }
    if (container.current) container.current.dataset.rendered = 'false';
    const m = map.current;
    (m.getSource('conditions') as maplibregl.GeoJSONSource | undefined)?.setData(
      colorAreas(areas, layer, day, values, overlayMode, cropProfiles, cropMetric, advisoryMap),
    );

    // Every municipality polygon stays rendered at static 90% opacity when visible so zero areas are unhighlighted;
    // when Raster grid is active, the georeferenced satellite overlay renders at 90% directly above the land base and below borders.
    m.setPaintProperty('conditions', 'fill-opacity', [
      'case',
      ['get', 'available'],
      visible ? STATIC_FILL_OPACITY : 0,
      visible ? 0.22 : 0,
    ]);

    if (showRaster && raster) {
      const existing = m.getSource('satellite-raster') as maplibregl.ImageSource | undefined;
      if (existing) {
        existing.updateImage({ url: raster.url, coordinates: raster.coordinates });
      } else {
        m.addSource('satellite-raster', {
          type: 'image',
          url: raster.url,
          coordinates: raster.coordinates,
        });
        m.addLayer(
          {
            id: 'satellite-raster-layer',
            type: 'raster',
            source: 'satellite-raster',
            paint: {
              'raster-opacity': visible ? STATIC_FILL_OPACITY : 0,
              'raster-fade-duration': 0,
              'raster-resampling': 'linear',
            },
          },
          'municipality-borders',
        );
      }
      if (m.getLayer('satellite-raster-layer')) {
        m.setPaintProperty('satellite-raster-layer', 'raster-opacity', visible ? STATIC_FILL_OPACITY : 0);
      }
    } else if (m.getLayer('satellite-raster-layer')) {
      m.setPaintProperty('satellite-raster-layer', 'raster-opacity', 0);
    }
    m.triggerRepaint();
  }, [layer, day, visible, ready, areas, values, showRaster, raster, overlayMode, cropProfiles, cropMetric, advisoryMap, mapLoading]);

  useEffect(() => {
    if (!notice) return;
    let clearTimer: ReturnType<typeof setTimeout>;
    const fadeTimer = setTimeout(() => {
      setNoticeClosing(true);
      clearTimer = setTimeout(() => {
        setNotice('');
        setNoticeClosing(false);
      }, 280);
    }, 3000);
    return () => {
      clearTimeout(fadeTimer);
      clearTimeout(clearTimer);
    };
  }, [notice, noticeSeq]);

  const dismissNotice = () => {
    setNoticeClosing(true);
    setTimeout(() => {
      setNotice('');
      setNoticeClosing(false);
    }, 240);
  };

  useEffect(() => {
    if (!ready || !map.current) return;
    setNotice('');
    setNoticeClosing(false);
    const el = document.createElement('div');
    el.className = 'selected-marker';
    const marker = new maplibregl.Marker({ element: el }).setLngLat([place.lon, place.lat]).addTo(map.current);
    map.current.setFilter('selected-area', ['==', ['get', 'id'], place.id]);
    if (previousPlace.current !== place || (userLocated && !didCenterUser.current)) {
      if (userLocated) didCenterUser.current = true;
      overview.current = false;
      map.current.easeTo({
        center: [place.lon, place.lat],
        zoom: Math.max(userLocated ? 9.2 : 8.8, map.current.getZoom()),
        duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 500,
      });
    }
    previousPlace.current = place;
    return () => {
      marker.remove();
    };
  }, [place, ready, userLocated]);

  const reset = () => {
    overview.current = true;
    map.current?.fitBounds(nationalBounds, { padding: responsiveBoundsPadding(), duration: 400 });
  };

  const selectedCrop = cropProfiles?.[place.province] ?? null;
  const selectedAdv = advisoryMap?.[place.province] ?? null;

  const legendTitle = !visible
    ? 'Overlay hidden'
    : overlayMode === 'crops'
      ? cropMetric === 'palay'
        ? 'Provincial palay'
        : 'Provincial corn'
      : overlayMode === 'advisories'
        ? 'PAGASA outlook'
        : layer.name;
  const legendUnit =
    overlayMode === 'crops' ? 'MT / yr' : overlayMode === 'advisories' ? 'Status' : layer.unit;
  const legendRamp =
    overlayMode === 'crops'
      ? (cropMetric === 'palay' ? PALAY_RAMP : CORN_RAMP).join(',')
      : overlayMode === 'advisories'
        ? '#55967e,#55967e 50%,#d08b3e 50%,#d08b3e'
        : layer.colors.join(',');

  const coords = hoverCoords ?? { lat: +place.lat.toFixed(3), lon: +place.lon.toFixed(3) };
  const areaCount = values === null ? 1642 : Object.keys(values).length;

  return (
    <div className="map-wrap">
      <div ref={container} className="map-canvas" aria-label="Interactive map of the Philippines" />

      {!ready && !failed && <div className="map-loading">Loading nationwide map…</div>}
      {failed && (
        <div className="map-failure">
          <strong>The interactive map couldn’t load</strong>
          <span>Enable WebGL in your browser, or choose a location from the search bar above.</span>
          <button className="secondary-button" onClick={() => setAttempt(a => a + 1)}>
            Retry map
          </button>
        </div>
      )}
      {tileError && !failed && (
        <div className="map-network">
          Basemap tiles unavailable. Philippine boundaries and satellite overlays remain active.
        </div>
      )}
      {dataError && (
        <div className="map-network" role="status">
          Couldn’t load stored map values. Refresh collected data to retry.
        </div>
      )}
      {notice && (
        <div key={noticeSeq} className="coverage-notice" role="status" data-closing={noticeClosing ? '' : undefined}>
          <span>{notice}</span>
          <button
            type="button"
            className="toast-close"
            onClick={dismissNotice}
            aria-label="Dismiss coverage notice"
            title="Dismiss"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {overlayMode === 'crops' && selectedCrop && (
        <div className="map-context-card" role="region" aria-label="Selected province crop calendar">
          <strong>{place.province} · Crop calendar</strong>
          <span>{selectedCrop.calendar.current_stage}</span>
          <small>
            Wet: {selectedCrop.calendar.wet_season.harvest} · Dry: {selectedCrop.calendar.dry_season.harvest}
            {selectedCrop.palay_production_mt != null ? ` · ${Math.round(selectedCrop.palay_production_mt).toLocaleString()} MT` : ''}
          </small>
        </div>
      )}

      {overlayMode === 'advisories' && selectedAdv && (
        <div className="map-context-card" role="region" aria-label="Selected province PAGASA advisory">
          <strong>{place.province} · {selectedAdv.product}</strong>
          <span>{selectedAdv.excerpt}</span>
        </div>
      )}

      {/* Right-edge Zoom Earth / Google Maps floating circular tool rail */}
      <div className="hud-right-rail" aria-label="Map and workspace controls">
        <button
          className="hud-circle-btn"
          onClick={onToggleDark}
          aria-label={dark ? 'Switch to light mode' : 'Switch to dark mode'}
          title={dark ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {dark ? <Sun size={17} /> : <Moon size={17} />}
        </button>
        <button
          className={`hud-circle-btn ${detailsOpen ? 'active' : ''}`}
          onClick={onToggleDetails}
          aria-label="Location details"
          title={detailsOpen ? 'Hide location card' : 'Show location card'}
        >
          <MapPin size={17} />
        </button>
        <button
          className="hud-circle-btn"
          aria-label="Center selected location"
          title="Center selected location"
          onClick={() => map.current?.easeTo({ center: [place.lon, place.lat], zoom: 10 })}
        >
          <LocateFixed size={17} />
        </button>
        <button
          className="hud-circle-btn"
          aria-label="Show all Philippines"
          title="Show all Philippines"
          onClick={reset}
        >
          <RotateCcw size={16} />
        </button>
        <button
          className="hud-circle-btn"
          onClick={() => onOpenModal('alerts')}
          aria-label="About alerts"
          title="About alerts"
        >
          <Bell size={17} />
        </button>
        <button
          className="hud-circle-btn"
          onClick={() => onOpenModal('help')}
          aria-label="Help"
          title="Help & guide"
        >
          <HelpCircle size={17} />
        </button>
      </div>

      {/* Bottom-right zoom capsule */}
      <div className="map-tools">
        <button aria-label="Zoom in" title="Zoom in" onClick={() => map.current?.zoomIn()}>
          <Plus size={17} />
        </button>
        <button aria-label="Zoom out" title="Zoom out" onClick={() => map.current?.zoomOut()}>
          <Minus size={17} />
        </button>
      </div>

      {/* Bottom-left scale legend + live coordinates readout */}
      <div className="hud-bottom-left">
        <div className="map-legend">
          <div>
            <strong>{legendTitle}</strong>
            <span>{legendUnit}</span>
          </div>
          <div className="legend-ramp" style={{ background: `linear-gradient(to right,${legendRamp})` }} />
          <div className="legend-labels">
            {overlayMode === 'crops' ? (
              <>
                <span>0</span>
                <span>{cropMetric === 'palay' ? '300k' : '175k'}</span>
                <span>{cropMetric === 'palay' ? '600k+' : '350k+'}</span>
              </>
            ) : overlayMode === 'advisories' ? (
              <>
                <span>Light / fair</span>
                <span>Scattered rain</span>
              </>
            ) : (
              <>
                <span>{layer.id === 'temperature' ? 18 : 0}</span>
                <span>{layer.id === 'temperature' ? 28 : layer.max / 2}</span>
                <span>{layer.max}+</span>
              </>
            )}
          </div>
        </div>
        <span className="map-demo-label">
          {coords.lat.toFixed(2)}°N {coords.lon.toFixed(2)}°E ·{' '}
          {values === null
            ? 'Demo · 90% fill'
            : overlayMode === 'crops'
              ? 'PSA OpenSTAT · 90% fill'
              : overlayMode === 'advisories'
                ? 'DOST-PAGASA · 90% fill'
                : showRaster
                  ? `${raster?.source_name?.split('(')[0].trim() ?? raster?.source} · ${areaCount.toLocaleString()} areas · 90%`
                  : `${areaCount.toLocaleString()} areas · 90% fill`}
        </span>
      </div>
    </div>
  );
}
