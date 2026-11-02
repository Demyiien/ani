"""Provider adapters. Values are never synthesized; invalid/missing/cloud-masked samples stay explicitly marked."""
from __future__ import annotations
import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
import os
import re
import ssl
from statistics import mean
import time
from zoneinfo import ZoneInfo
import httpx
import numpy as np
import truststore
from . import advisories_crops, config, db, rasters

SOURCES = {
    'chirps': {
        'name': 'CHIRPS v3.0 Prelim (UCSB CHC)',
        'url': 'https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/prelim/sat/',
        'resolution': '0.05° (~5 km)',
        'kind': 'Satellite + station daily raster',
        'layers': ['rainfall'],
        'scope': 'national',
        'stale_after_days': 8,
        'key_required': False,
    },
    'imerg': {
        'name': 'NASA GPM IMERG (via GIBS)',
        'url': 'https://gpm.nasa.gov/data/imerg',
        'resolution': '0.1° (~10 km)',
        'kind': 'Satellite precipitation raster',
        'layers': ['rainfall'],
        'scope': 'national',
        'stale_after_days': 3,
        'key_required': False,
    },
    'himawari': {
        'name': 'JMA Himawari-9 AHI Band 13 (via GIBS)',
        'url': 'https://www.data.jma.go.jp/mscweb/en/himawari89/',
        'resolution': '2 km / 10-min',
        'kind': 'Geostationary IR satellite imagery',
        'layers': ['cloud'],
        'scope': 'national',
        'stale_after_days': 2,
        'key_required': False,
    },
    'smap': {
        'name': 'NASA SMAP L3/L4 Soil Moisture (via GIBS)',
        'url': 'https://smap.jpl.nasa.gov/',
        'resolution': '9 km EASE-Grid',
        'kind': 'L-band radiometer + L4 surface raster',
        'layers': ['soil'],
        'scope': 'national',
        'stale_after_days': 5,
        'key_required': False,
    },
    'gibs_ndvi': {
        'name': 'MODIS Terra 8-Day NDVI (via NASA GIBS)',
        'url': 'https://modis.gsfc.nasa.gov/data/dataprod/mod13.php',
        'resolution': '250 m / 8-day composite',
        'kind': 'Optical vegetation raster',
        'layers': ['vegetation'],
        'scope': 'national',
        'stale_after_days': 10,
        'key_required': False,
    },
    'modis': {
        'name': 'MODIS MOD13Q1 via ORNL DAAC',
        'url': 'https://modis.ornl.gov/data/modis_webservice.html',
        'resolution': '250 m / 16-day composite',
        'kind': 'Satellite composite pixel',
        'layers': ['vegetation'],
        'scope': 'area',
        'stale_after_days': 24,
        'key_required': False,
    },
    'open_meteo': {
        'name': 'ERA5-Seamless / ECMWF IFS (Open-Meteo)',
        'url': 'https://open-meteo.com/en/docs/historical-weather-api',
        'resolution': '0.25° (~25 km)',
        'kind': 'Reanalysis + NRT analysis point',
        'layers': ['rainfall', 'temperature', 'evapotranspiration', 'cloud', 'soil'],
        'scope': 'area',
        'stale_after_days': 2,
        'key_required': False,
    },
    'open_meteo_grid': {
        'name': 'ERA5-Seamless Gridded Raster (Open-Meteo)',
        'url': 'https://open-meteo.com/en/docs/historical-weather-api',
        'resolution': '0.5° archipelago grid',
        'kind': 'Reanalysis + ECMWF IFS national grid',
        'layers': ['temperature', 'evapotranspiration', 'rainfall', 'soil', 'cloud'],
        'scope': 'national',
        'stale_after_days': 2,
        'key_required': False,
    },
    'nasa_power': {
        'name': 'NASA POWER (MERRA-2)',
        'url': 'https://power.larc.nasa.gov/',
        'resolution': '0.5° × 0.625°',
        'kind': 'MERRA-2 agroclimatology grid',
        'layers': ['rainfall', 'temperature', 'evapotranspiration'],
        'scope': 'area',
        'stale_after_days': 5,
        'key_required': False,
    },
    'pagasa': {
        'name': 'DOST-PAGASA Public & Farm Advisories',
        'url': 'https://www.pagasa.dost.gov.ph/agri-weather',
        'resolution': 'Regional / provincial bulletin',
        'kind': 'Official meteorological advisory',
        'layers': [],
        'scope': 'national',
        'stale_after_days': 3,
        'key_required': False,
    },
    'psa': {
        'name': 'PSA OpenSTAT & DA/PhilRice Crop Calendar',
        'url': 'https://openstat.psa.gov.ph/',
        'resolution': 'Provincial / quarterly',
        'kind': 'Official agricultural statistics',
        'layers': [],
        'scope': 'national',
        'stale_after_days': 120,
        'key_required': False,
    },
}

GIBS_WMS_URL = 'https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi'
GIBS_WMTS_CAPS_URL = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi?SERVICE=WMTS&REQUEST=GetCapabilities'
GIBS_COLORMAPS = {
    'imerg': 'https://gibs.earthdata.nasa.gov/colormaps/v1.3/GPM_Precipitation_Rate.xml',
    'himawari': 'https://gibs.earthdata.nasa.gov/colormaps/v1.3/Clean_Longwave_Infrared_Window_Band.xml',
    'smap_l3': 'https://gibs.earthdata.nasa.gov/colormaps/v1.3/SMAP_Soil_Moisture.xml',
    'smap_l4': 'https://gibs.earthdata.nasa.gov/colormaps/v1.3/SMAP_Analyzed_Soil_Moisture.xml',
    'gibs_ndvi': 'https://gibs.earthdata.nasa.gov/colormaps/v1.3/MODIS_NDVI.xml',
}

_last_request = 0.0
_colormap_mem: dict[str, dict] = {}


def today() -> date:
    return datetime.now(ZoneInfo('Asia/Manila')).date()


def valid(value, low, high) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high


def tls_context():
    bundle = os.getenv('ANI_CA_BUNDLE')
    return ssl.create_default_context(cafile=bundle) if bundle else truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


async def _throttle(min_gap: float = 0.35):
    global _last_request
    await asyncio.sleep(max(0.0, min_gap - (time.monotonic() - _last_request)))
    _last_request = time.monotonic()


async def get_json(client, url, params):
    # The hash excludes credentials; neither logs nor raw filenames contain API keys.
    safe = {k: v for k, v in params.items() if k != 'apikey'}
    key = hashlib.sha256(json.dumps([url, safe], sort_keys=True).encode()).hexdigest()
    with db.connection() as conn:
        cached = conn.execute('SELECT * FROM cache WHERE key=?', (key,)).fetchone()
    if cached and datetime.fromisoformat(cached['fetched_at']) > datetime.now(timezone.utc) - timedelta(hours=12):
        return json.loads(cached['payload'])
    for attempt in range(3):
        await _throttle(0.8)
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    retry = response.headers.get('Retry-After', '')
                    await asyncio.sleep(min(60, float(retry)) if retry.isdigit() else 2 ** (attempt + 1))
                    continue
            if response.status_code >= 400:
                raise RuntimeError(f'Provider returned HTTP {response.status_code}; check its availability or subscription settings.')
            payload = response.json()
            if not isinstance(payload, (dict, list)):
                raise RuntimeError('Provider returned an unexpected response.')
            serialized = json.dumps(payload, allow_nan=False)
            raw = config.DATA_DIR / 'raw'
            raw.mkdir(exist_ok=True)
            with gzip.open(raw / f'{key}.json.gz', 'wt', encoding='utf8') as file:
                file.write(serialized)
            with db.connection() as conn:
                conn.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, serialized, db.now()))
            return payload
        except (httpx.TransportError, ValueError) as exc:
            if attempt == 2:
                raise RuntimeError('Provider connection or response failed. Check network access and trusted certificates.') from exc
            await asyncio.sleep(2 ** (attempt + 1))
    raise RuntimeError('Provider unavailable after retries.')


async def get_bytes(client, url: str, params: dict | None = None, ttl_hours: float = 24.0) -> bytes:
    """Fetch binary/XML/GeoTIFF payloads with compressed disk cache in data/raw/."""
    safe = {k: v for k, v in (params or {}).items() if k != 'apikey'}
    key = hashlib.sha256(json.dumps(['bytes', url, safe], sort_keys=True).encode()).hexdigest()
    raw = config.DATA_DIR / 'raw'
    raw.mkdir(exist_ok=True)
    blob_path = raw / f'{key}.bin.gz'
    if blob_path.exists():
        age_sec = time.time() - blob_path.stat().st_mtime
        if age_sec < ttl_hours * 3600:
            with gzip.open(blob_path, 'rb') as f:
                return f.read()

    for attempt in range(3):
        await _throttle(0.25)
        try:
            response = await client.get(url, params=params, headers={'Accept': '*/*'})
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
            if response.status_code >= 400:
                raise RuntimeError(f'Upstream source returned HTTP {response.status_code}.')
            content = response.content
            with gzip.open(blob_path, 'wb') as f:
                f.write(content)
            return content
        except httpx.TransportError as exc:
            if attempt == 2:
                raise RuntimeError('Upstream binary download failed; check network connectivity.') from exc
            await asyncio.sleep(2 ** (attempt + 1))
    raise RuntimeError('Upstream source unavailable after retries.')


async def get_colormap(client, name: str) -> dict:
    if name in _colormap_mem:
        return _colormap_mem[name]
    xml_bytes = await get_bytes(client, GIBS_COLORMAPS[name], ttl_hours=168)
    parsed = rasters.parse_gibs_colormap(xml_bytes.decode('utf-8', errors='replace'))
    _colormap_mem[name] = parsed
    return parsed


def _expand_iso_dates(values: list[str], max_date: date, limit: int = 14) -> list[str]:
    """Expand WMTS Time <Value> intervals (YYYY-MM-DD/YYYY-MM-DD/PnD) into sorted dates <= max_date."""
    found: set[str] = set()
    cutoff = max_date - timedelta(days=45)
    for item in values:
        parts = item.strip().split('/')
        if len(parts) == 1 and len(parts[0]) >= 10:
            d_str = parts[0][:10]
            if d_str <= max_date.isoformat():
                found.add(d_str)
            continue
        if len(parts) >= 2:
            s_str, e_str = parts[0][:10], parts[1][:10]
            step_m = re.match(r'P(\d+)D', parts[2]) if len(parts) == 3 else None
            step_days = int(step_m.group(1)) if step_m else 1
            try:
                s_dt = date.fromisoformat(s_str)
                e_dt = min(date.fromisoformat(e_str), max_date)
            except ValueError:
                continue
            if e_dt < cutoff:
                continue
            if step_days == 1:
                cur = max(s_dt, cutoff)
            else:
                skip = max(0, (cutoff - s_dt).days // step_days)
                cur = s_dt + timedelta(days=skip * step_days)
            while cur <= e_dt:
                found.add(cur.isoformat())
                cur += timedelta(days=step_days)
    return sorted(found)[-limit:]


async def gibs_layer_times(client) -> dict[str, dict]:
    """Retrieve and parse GIBS WMTS Capabilities time ranges for our target satellite layers."""
    target_layers = {
        'IMERG_Precipitation_Rate',
        'Himawari_AHI_Band13_Clean_Infrared',
        'SMAP_L3_Passive_Enhanced_Day_Soil_Moisture',
        'SMAP_L4_Analyzed_Surface_Soil_Moisture',
        'MODIS_Terra_NDVI_8Day',
    }
    raw_caps = config.DATA_DIR / 'raw' / 'gibs_layer_times.json'
    if raw_caps.exists() and (time.time() - raw_caps.stat().st_mtime) < 6 * 3600:
        try:
            return json.loads(raw_caps.read_text(encoding='utf-8'))
        except ValueError:
            pass

    xml_text = (await get_bytes(client, GIBS_WMTS_CAPS_URL, ttl_hours=6)).decode('utf-8', errors='replace')
    info: dict[str, dict] = {}
    cur_today = today()
    for block in re.findall(r'<Layer>(.*?)</Layer>', xml_text, flags=re.S):
        id_m = re.search(r'<ows:Identifier>([^<]+)</ows:Identifier>', block)
        if not id_m or id_m.group(1) not in target_layers:
            continue
        lid = id_m.group(1)
        dflt_m = re.search(r'<Default>([^<]+)</Default>', block)
        vals = re.findall(r'<Value>([^<]+)</Value>', block)
        info[lid] = {
            'default': dflt_m.group(1) if dflt_m else None,
            'dates': _expand_iso_dates(vals, cur_today, limit=14),
        }
    raw_caps.parent.mkdir(parents=True, exist_ok=True)
    raw_caps.write_text(json.dumps(info), encoding='utf-8')
    return info


async def fetch_gibs_wms(client, layer_id: str, time_str: str, width: int = 500, height: int = 840) -> bytes:
    west, south, east, north = rasters.PH_BOUNDS
    params = {
        'SERVICE': 'WMS',
        'VERSION': '1.3.0',
        'REQUEST': 'GetMap',
        'LAYERS': layer_id,
        'CRS': 'EPSG:4326',
        'BBOX': f'{south},{west},{north},{east}',
        'WIDTH': str(width),
        'HEIGHT': str(height),
        'FORMAT': 'image/png',
        'TIME': time_str,
        'STYLES': '',
    }
    data = await get_bytes(client, GIBS_WMS_URL, params=params, ttl_hours=24)
    if not data.startswith(b'\x89PNG'):
        raise RuntimeError(f'GIBS WMS did not return a PNG image for {layer_id} at {time_str}.')
    return data


# --- Point adapters (backward-compatible with existing unit tests) ---

def parse_weather(payload):
    rows = []
    daily = payload.get('daily', {})
    cur_today = today().isoformat()
    era5_cutoff = (today() - timedelta(days=5)).isoformat()
    for layer, key, unit, limits in [
        ('rainfall', 'rain_sum', 'mm', (0, 2000)),
        ('temperature', 'temperature_2m_mean', '°C', (-80, 65)),
        ('evapotranspiration', 'et0_fao_evapotranspiration', 'mm', (0, 30)),
    ]:
        for day, value in zip(daily.get('time', []), daily.get(key, [])):
            if valid(value, *limits):
                qual = ('ECMWF IFS same-day analysis/nowcast' if day >= cur_today
                        else 'ECMWF IFS / ERA5T recent analysis' if day > era5_cutoff
                        else 'ERA5 finalized reanalysis')
                rows.append({'layer': layer, 'date': day, 'value': round(float(value), 3), 'unit': unit, 'quality': qual})
    hourly = payload.get('hourly', {})
    for layer, key, unit, limits in [
        ('cloud', 'cloud_cover', '%', (0, 100)),
        ('soil', 'soil_moisture_0_to_7cm', 'm³/m³', (0, 1)),
    ]:
        grouped = defaultdict(list)
        for timestamp, value in zip(hourly.get('time', []), hourly.get(key, [])):
            if valid(value, *limits):
                grouped[timestamp[:10]].append(value)
        # Do not call a partial day a daily average.
        for day, values in grouped.items():
            if len(values) == 24:
                qual = ('ECMWF IFS same-day analysis' if day >= cur_today
                        else 'ECMWF IFS / ERA5T recent analysis' if day > era5_cutoff
                        else 'ERA5 finalized reanalysis')
                rows.append({'layer': layer, 'date': day, 'value': round(mean(values), 4), 'unit': unit, 'quality': qual})
    return [{**r, 'source': 'open_meteo', 'kind': 'reanalysis', 'lat': payload.get('latitude'), 'lon': payload.get('longitude')} for r in rows]


async def weather(client, area):
    # Clamp archive end_date to current UTC date so 00:00–08:00 Asia/Manila requests never exceed Open-Meteo's UTC archive ceiling.
    end = min(today(), datetime.now(timezone.utc).date())
    start = end - timedelta(days=13)
    params = {
        'latitude': area['lat'],
        'longitude': area['lon'],
        'start_date': start.isoformat(),
        'end_date': end.isoformat(),
        'daily': 'rain_sum,temperature_2m_mean,et0_fao_evapotranspiration',
        'hourly': 'cloud_cover,soil_moisture_0_to_7cm',
        'timezone': 'Asia/Manila',
    }
    key = os.getenv('OPEN_METEO_API_KEY', '')
    host = 'customer-archive-api.open-meteo.com' if key else 'archive-api.open-meteo.com'
    if key:
        params['apikey'] = key
    payload = await get_json(client, f'https://{host}/v1/archive', params)
    rows = parse_weather(payload)
    if rows:
        db.record_run('open_meteo', upstream_latest=max(r['date'] for r in rows))
    return rows


def parse_power(payload):
    rows = []
    params = payload.get('properties', {}).get('parameter', {})
    for layer, param, unit, limits, scale, qual in [
        ('rainfall', 'PRECTOTCORR', 'mm', (0, 2000), 1.0, 'valid; UTC daily aggregate'),
        ('temperature', 'T2M', '°C', (-80, 65), 1.0, 'valid; UTC daily aggregate'),
        ('evapotranspiration', 'EVPTRNS', 'mm', (0, 75), 1.0 / 2.45, 'MERRA-2 ET flux ÷ 2.45 MJ/kg (mm eq.); UTC daily'),
    ]:
        for day, value in params.get(param, {}).items():
            if valid(value, *limits):
                rows.append({
                    'layer': layer,
                    'date': datetime.strptime(day, '%Y%m%d').date().isoformat(),
                    'value': round(float(value) * scale, 3) if scale != 1.0 else value,
                    'unit': unit,
                    'source': 'nasa_power',
                    'kind': 'reanalysis',
                    'quality': qual,
                })
    return rows


async def power(client, area):
    # Query up to yesterday; NASA POWER fills unpublished recent days with -999 which valid() skips automatically.
    end = today() - timedelta(days=1)
    start = end - timedelta(days=13)
    lat = round(area['lat'] / .5) * .5
    lon = round(area['lon'] / .625) * .625
    payload = await get_json(client, 'https://power.larc.nasa.gov/api/temporal/daily/point', {
        'latitude': lat,
        'longitude': lon,
        'start': start.strftime('%Y%m%d'),
        'end': end.strftime('%Y%m%d'),
        'parameters': 'T2M,PRECTOTCORR,EVPTRNS',
        'community': 'AG',
        'format': 'JSON',
        'time-standard': 'UTC',
    })
    rows = [{**r, 'lat': lat, 'lon': lon} for r in parse_power(payload)]
    if rows:
        db.record_run('nasa_power', upstream_latest=max(r['date'] for r in rows))
    return rows


def parse_modis(payload, include_masked: bool = False):
    subsets = payload.get('subset', [])
    quality = {r['calendar_date']: r.get('data', []) for r in subsets if r.get('band') == '250m_16_days_pixel_reliability'}
    rows = []
    for r in subsets:
        if r.get('band') != '250m_16_days_NDVI':
            continue
        cdate = r['calendar_date']
        if cdate not in quality:
            continue
        flags = quality.get(cdate, [])
        raw_data = r.get('data', [])
        values = [v * .0001 for i, v in enumerate(raw_data) if valid(v, -2000, 10000) and i < len(flags) and flags[i] == 0]
        if values:
            rows.append({
                'layer': 'vegetation',
                'date': cdate,
                'value': round(mean(values), 4),
                'unit': 'NDVI',
                'source': 'modis',
                'kind': 'satellite composite',
                'quality': '16-day composite; reliability flag 0 only',
                'status': 'valid',
            })
        elif include_masked and any(f in (1, 2, 3) for f in flags):
            rows.append({
                'layer': 'vegetation',
                'date': cdate,
                'value': None,
                'unit': 'NDVI',
                'source': 'modis',
                'kind': 'satellite composite',
                'quality': f'Cloud-masked / marginal pixel (MOD13Q1 reliability flags: {sorted(set(flags))})',
                'status': 'cloud-masked',
            })
    return rows


async def vegetation(client, area):
    base = 'https://modis.ornl.gov/rst/api/v1/MOD13Q1'
    coords = {'latitude': area['lat'], 'longitude': area['lon']}
    dates = await get_json(client, base + '/dates', coords)
    available = sorted((d for d in dates.get('dates', []) if d['calendar_date'] <= today().isoformat()), key=lambda d: d['calendar_date'])[-3:]
    if not available:
        return []
    db.record_run('modis', upstream_latest=available[-1]['calendar_date'])
    payload = await get_json(client, base + '/subset', {
        **coords,
        'startDate': available[0]['modis_date'],
        'endDate': available[-1]['modis_date'],
        'kmAboveBelow': 0,
        'kmLeftRight': 0,
    })
    return parse_modis(payload, include_masked=True)


# --- Nationwide satellite & gridded raster collectors ---

async def collect_chirps(client, areas: dict) -> int:
    """Download CHIRPS v3.0 Prelim daily GeoTIFFs, crop to PH, render PNG rasters, and sample 1,642 municipalities."""
    cur_today = today()
    years = sorted({cur_today.year - 1, cur_today.year})
    found_files: list[tuple[str, str]] = []
    for yr in years:
        dir_url = f'https://data.chc.ucsb.edu/products/CHIRPS/v3.0/daily/prelim/sat/{yr}/'
        try:
            listing = (await get_bytes(client, dir_url, ttl_hours=6)).decode('utf-8', errors='replace')
        except RuntimeError:
            continue
        for m in re.finditer(r'href="(chirps-v3\.0\.prelim\.(\d{4})\.(\d{2})\.(\d{2})\.tif)"', listing):
            fname = m.group(1)
            iso_d = f'{m.group(2)}-{m.group(3)}-{m.group(4)}'
            if iso_d <= cur_today.isoformat():
                found_files.append((iso_d, dir_url + fname))

    found_files.sort(key=lambda x: x[0])
    if not found_files:
        raise RuntimeError('No CHIRPS v3.0 Prelim GeoTIFF files found upstream.')

    upstream_latest = found_files[-1][0]
    selected = found_files[-min(config.RASTER_DAYS, 5):]
    total_saved = 0
    for iso_d, file_url in selected:
        tiff_bytes = await get_bytes(client, file_url, ttl_hours=72)
        grid, g_bounds = rasters.read_chirps_ph(tiff_bytes)
        rasters.save_raster('rainfall', 'chirps', iso_d, grid, g_bounds, extra={'source_name': SOURCES['chirps']['name']})
        rows_by_area = rasters.sample_areas_from_grid(
            areas, grid, g_bounds,
            layer='rainfall', obs_date=iso_d, unit='mm',
            source='chirps', kind='satellite + station raster',
            quality_fn=lambda v, cov: f'CHIRPS v3.0 Prelim 0.05° pixel; land neighborhood {int(cov * 100)}%',
        )
        total_saved += db.save_area_rows(areas, rows_by_area)

    db.record_run('chirps', upstream_latest=upstream_latest)
    return total_saved


async def collect_imerg(client, areas: dict) -> int:
    """Fetch NASA GPM IMERG daily precipitation rasters via GIBS WMS + v1.3 colormap, render overlays, and sample municipalities."""
    caps = await gibs_layer_times(client)
    imerg_dates = caps.get('IMERG_Precipitation_Rate', {}).get('dates', [])
    if not imerg_dates:
        raise RuntimeError('GIBS Capabilities reported no available IMERG_Precipitation_Rate dates.')

    cmap = await get_colormap(client, 'imerg')
    selected = imerg_dates[-min(config.RASTER_DAYS, 7):]
    total_saved = 0
    for iso_d in selected:
        png = await fetch_gibs_wms(client, 'IMERG_Precipitation_Rate', iso_d, width=500, height=840)
        # On published IMERG dates within 60°S-60°N, GIBS encodes rates <0.1 mm/hr ("Insignificant") as transparent.
        rate_mm_hr, stats = rasters.decode_wms_png(png, cmap, allow_nearest=True, transparent_fill=0.0)
        daily_mm = np.round(rate_mm_hr * 24.0, 3)
        rasters.save_raster(
            'rainfall', 'imerg', iso_d, daily_mm, rasters.PH_BOUNDS,
            extra={'source_name': SOURCES['imerg']['name'], 'rain_area_pct': round(stats['opaque_fraction'] * 100, 1)},
        )
        rows_by_area = rasters.sample_areas_from_grid(
            areas, daily_mm, rasters.PH_BOUNDS,
            layer='rainfall', obs_date=iso_d, unit='mm',
            source='imerg', kind='satellite precipitation',
            quality_fn=lambda v, _: ('IMERG rate < 0.1 mm/hr (below threshold)' if v == 0
                                     else 'GPM IMERG daily precipitation rate × 24h'),
        )
        total_saved += db.save_area_rows(areas, rows_by_area)

    db.record_run('imerg', upstream_latest=imerg_dates[-1])
    return total_saved


async def collect_himawari(client, areas: dict) -> int:
    """Fetch JMA Himawari-9 AHI Band 13 Clean Infrared imagery up through today(), decode BT (°C) -> cloud index (%), and export PNG rasters."""
    caps = await gibs_layer_times(client)
    hima_info = caps.get('Himawari_AHI_Band13_Clean_Infrared', {})
    dflt_ts = hima_info.get('default') or f'{today().isoformat()}T04:00:00Z'
    cmap = await get_colormap(client, 'himawari')

    cur_today = min(today(), datetime.now(timezone.utc).date())
    days = [(cur_today - timedelta(days=i)).isoformat() for i in range(min(config.RASTER_DAYS, 7) - 1, -1, -1)]
    total_saved = 0
    latest_ok = None

    for iso_d in days:
        if iso_d == cur_today.isoformat() and dflt_ts.startswith(iso_d):
            ts = dflt_ts
        else:
            # 04:00 UTC = 12:00 noon Manila time (avoids 02:40 UTC Himawari housekeeping slot).
            ts = f'{iso_d}T04:00:00Z'
        try:
            png = await fetch_gibs_wms(client, 'Himawari_AHI_Band13_Clean_Infrared', ts, width=700, height=1176)
        except RuntimeError:
            if dflt_ts and dflt_ts.startswith(iso_d):
                ts = dflt_ts
                png = await fetch_gibs_wms(client, 'Himawari_AHI_Band13_Clean_Infrared', ts, width=700, height=1176)
            else:
                continue

        bt_c, stats = rasters.decode_himawari_png(png, cmap)
        if stats['opaque_fraction'] < 0.05:
            continue
        # Tropical Band 13 (10.3 µm): >= 20°C warm surface/clear boundary layer (0%), <= -60°C deep convective anvil (100%).
        cloud_idx = rasters.smooth_grid_3x3(np.clip((20.0 - bt_c) / 80.0 * 100.0, 0.0, 100.0))
        rasters.save_raster(
            'cloud', 'himawari', iso_d, cloud_idx, rasters.PH_BOUNDS,
            extra={'source_name': SOURCES['himawari']['name'], 'imagery_timestamp': ts},
        )
        rows_by_area = rasters.sample_areas_from_grid(
            areas, cloud_idx, rasters.PH_BOUNDS,
            layer='cloud', obs_date=iso_d, unit='%',
            source='himawari', kind='Himawari-9 AHI Band 13 IR',
            quality_fn=lambda v, _, _ts=ts: f'Himawari-9 AHI 10.3 µm IR ({_ts}); BT≈{20.0 - v * 0.8:.1f}°C',
        )
        total_saved += db.save_area_rows(areas, rows_by_area)
        latest_ok = iso_d

    if not total_saved:
        raise RuntimeError('No valid Himawari-9 AHI scenes could be decoded.')
    db.record_run('himawari', upstream_latest=latest_ok or dflt_ts[:10])
    return total_saved


async def collect_smap(client, areas: dict) -> int:
    """Fetch NASA SMAP 9 km L3 radiometer swath + L4 analyzed surface soil moisture rasters and sample municipalities."""
    caps = await gibs_layer_times(client)
    l4_dates = caps.get('SMAP_L4_Analyzed_Surface_Soil_Moisture', {}).get('dates', [])
    l3_dates = set(caps.get('SMAP_L3_Passive_Enhanced_Day_Soil_Moisture', {}).get('dates', []))
    all_dates = sorted(set(l4_dates) | l3_dates)
    if not all_dates:
        raise RuntimeError('GIBS Capabilities reported no available SMAP soil moisture dates.')

    cmap_l3 = await get_colormap(client, 'smap_l3')
    cmap_l4 = await get_colormap(client, 'smap_l4')
    selected = all_dates[-min(config.RASTER_DAYS, 7):]
    total_saved = 0

    for iso_d in selected:
        l3_grid = np.full((672, 400), np.nan, dtype=np.float32)
        l4_grid = np.full((672, 400), np.nan, dtype=np.float32)
        if iso_d in l3_dates:
            try:
                png_l3 = await fetch_gibs_wms(client, 'SMAP_L3_Passive_Enhanced_Day_Soil_Moisture', iso_d, width=400, height=672)
                l3_grid, _ = rasters.decode_wms_png(png_l3, cmap_l3, allow_nearest=True)
            except RuntimeError:
                pass
        if iso_d in l4_dates:
            try:
                png_l4 = await fetch_gibs_wms(client, 'SMAP_L4_Analyzed_Surface_Soil_Moisture', iso_d, width=400, height=672)
                l4_grid, _ = rasters.decode_wms_png(png_l4, cmap_l4, allow_nearest=True)
            except RuntimeError:
                pass

        merged = np.where(np.isfinite(l3_grid), l3_grid, l4_grid)
        if not np.isfinite(merged).any():
            continue

        l3_mask = np.isfinite(l3_grid)
        rasters.save_raster(
            'soil', 'smap', iso_d, merged, rasters.PH_BOUNDS,
            extra={'source_name': SOURCES['smap']['name'], 'l3_swath_pct': round(float(l3_mask.mean() * 100), 2)},
        )
        rows_by_area = rasters.sample_areas_from_grid(
            areas, merged, rasters.PH_BOUNDS,
            layer='soil', obs_date=iso_d, unit='m³/m³',
            source='smap', kind='SMAP 9 km soil moisture',
            quality_fn=lambda v, cov: f'NASA SMAP 9 km (L3 swath + L4 surface); land coverage {int(cov * 100)}%',
            search_radius=2,
        )
        total_saved += db.save_area_rows(areas, rows_by_area)

    db.record_run('smap', upstream_latest=all_dates[-1])
    return total_saved


async def collect_gibs_ndvi(client, areas: dict) -> int:
    """Fetch MODIS Terra 250 m rolling 8-day NDVI composites, mark cloud-masked Philippine municipalities, and save PNG rasters."""
    caps = await gibs_layer_times(client)
    ndvi_dates = caps.get('MODIS_Terra_NDVI_8Day', {}).get('dates', [])
    if not ndvi_dates:
        raise RuntimeError('GIBS Capabilities reported no available MODIS_Terra_NDVI_8Day dates.')

    cmap = await get_colormap(client, 'gibs_ndvi')
    # Collect the latest composite plus the composite ~7 days prior so the 7-day timeline has full composite history.
    picked = sorted({ndvi_dates[-1], ndvi_dates[max(0, len(ndvi_dates) - 7)]})
    total_saved = 0

    for iso_d in picked:
        png = await fetch_gibs_wms(client, 'MODIS_Terra_NDVI_8Day', iso_d, width=700, height=1176)
        ndvi, _ = rasters.decode_wms_png(png, cmap, allow_nearest=False)
        ndvi[ndvi < 0.0] = np.nan
        land_mask = rasters.approximate_land_mask(areas, ndvi.shape, rasters.PH_BOUNDS)
        cloud_mask = land_mask & (~np.isfinite(ndvi))
        rasters.save_raster(
            'vegetation', 'gibs_ndvi', iso_d, ndvi, rasters.PH_BOUNDS,
            cloud_mask=cloud_mask,
            extra={
                'source_name': SOURCES['gibs_ndvi']['name'],
                'land_cloud_masked_pct': round(float(cloud_mask.sum() / max(1, land_mask.sum()) * 100.0), 1),
            },
        )
        rows_by_area = rasters.sample_areas_from_grid(
            areas, ndvi, rasters.PH_BOUNDS,
            layer='vegetation', obs_date=iso_d, unit='NDVI',
            source='gibs_ndvi', kind='MODIS 8-day 250 m composite',
            quality_fn=lambda v, cov: f'MODIS Terra 8-day NDVI; clear neighborhood {int(cov * 100)}%',
            record_cloud_masked=True,
            search_radius=1,
        )
        total_saved += db.save_area_rows(areas, rows_by_area)

    db.record_run('gibs_ndvi', upstream_latest=ndvi_dates[-1])
    return total_saved


async def collect_open_meteo_grid(client, areas: dict) -> int:
    """Fetch a compact 0.75° Philippine archipelago weather grid for rasters + 1,642-municipality baseline coverage through today()."""
    west, south, east, north = rasters.PH_BOUNDS
    area_coords = [(float(a['lat']), float(a['lon'])) for a in areas.values()]
    grid_pts: list[tuple[float, float]] = []
    lat = south + 0.4
    while lat <= north - 0.2:
        lon = west + 0.4
        while lon <= east - 0.2:
            if any((lat - la) ** 2 + (lon - lo) ** 2 <= 0.75 ** 2 for la, lo in area_coords):
                grid_pts.append((round(lat, 2), round(lon, 2)))
            lon += 0.75
        lat += 0.75

    end = min(today(), datetime.now(timezone.utc).date())
    start = end - timedelta(days=min(config.RASTER_DAYS, 7) - 1)
    key = os.getenv('OPEN_METEO_API_KEY', '')
    host = 'customer-archive-api.open-meteo.com' if key else 'archive-api.open-meteo.com'

    responses: list[dict] = []
    for i in range(0, len(grid_pts), 40):
        batch = grid_pts[i:i + 40]
        params = {
            'latitude': ','.join(str(p[0]) for p in batch),
            'longitude': ','.join(str(p[1]) for p in batch),
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'daily': 'temperature_2m_mean,et0_fao_evapotranspiration,rain_sum,soil_moisture_0_to_7cm_mean,cloud_cover_mean',
            'timezone': 'Asia/Manila',
        }
        if key:
            params['apikey'] = key
        payload = await get_json(client, f'https://{host}/v1/archive', params)
        responses.extend(payload if isinstance(payload, list) else [payload])

    if not responses:
        raise RuntimeError('Open-Meteo archipelago grid returned no data.')

    land_mask = rasters.approximate_land_mask(areas, (224, 134), rasters.PH_BOUNDS)
    days = responses[0].get('daily', {}).get('time', [])
    cur_today = end.isoformat()
    era5_cutoff = (end - timedelta(days=5)).isoformat()
    total_saved = 0

    for d_idx, iso_d in enumerate(days):
        qual_tag = ('ECMWF IFS same-day national grid' if iso_d >= cur_today
                    else 'ECMWF IFS / ERA5T national grid' if iso_d > era5_cutoff
                    else 'ERA5 national reanalysis grid')
        for layer, field, unit, lims in [
            ('temperature', 'temperature_2m_mean', '°C', (-80, 65)),
            ('evapotranspiration', 'et0_fao_evapotranspiration', 'mm', (0, 30)),
            ('rainfall', 'rain_sum', 'mm', (0, 2000)),
            ('soil', 'soil_moisture_0_to_7cm_mean', 'm³/m³', (0, 1)),
            ('cloud', 'cloud_cover_mean', '%', (0, 100)),
        ]:
            lats, lons, vals = [], [], []
            for pt_payload in responses:
                arr = pt_payload.get('daily', {}).get(field, [])
                if d_idx < len(arr) and valid(arr[d_idx], *lims):
                    lats.append(float(pt_payload['latitude']))
                    lons.append(float(pt_payload['longitude']))
                    vals.append(float(arr[d_idx]))
            if len(vals) >= 10:
                continuous_grid = rasters.interpolate_points_to_grid(lats, lons, vals, rasters.PH_BOUNDS, 224, 134, land_mask=None)
                masked_grid = np.where(land_mask, continuous_grid, np.nan)
                rasters.save_raster(layer, 'open_meteo_grid', iso_d, masked_grid, rasters.PH_BOUNDS,
                                    extra={'source_name': SOURCES['open_meteo_grid']['name'], 'grid_points': len(vals)})
                rows_by_area = rasters.sample_areas_from_grid(
                    areas, continuous_grid, rasters.PH_BOUNDS,
                    layer=layer, obs_date=iso_d, unit=unit,
                    source='open_meteo_grid', kind='reanalysis grid',
                    quality_fn=lambda v, _, _q=qual_tag: f'{_q} (0.75° cell)',
                )
                total_saved += db.save_area_rows(areas, rows_by_area)

    db.record_run('open_meteo_grid', upstream_latest=days[-1] if days else None)
    return total_saved


ADAPTERS = {
    'open_meteo': weather,
    'nasa_power': power,
    'modis': vegetation,
}

NATIONAL_ADAPTERS = {
    'chirps': collect_chirps,
    'imerg': collect_imerg,
    'himawari': collect_himawari,
    'smap': collect_smap,
    'gibs_ndvi': collect_gibs_ndvi,
    'open_meteo_grid': collect_open_meteo_grid,
    'pagasa': advisories_crops.collect_pagasa,
    'psa': advisories_crops.collect_psa,
}
