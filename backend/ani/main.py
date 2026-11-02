from contextlib import asynccontextmanager
import asyncio
from datetime import date, timedelta
import hmac
import json
from typing import Literal
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from . import advisories_crops, config, db, ingestion, providers, rasters


@asynccontextmanager
async def lifespan(app):
    db.init()
    tasks = [asyncio.create_task(ingestion.worker()), asyncio.create_task(ingestion.scheduler())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title='Ani climate collection API', version='0.2.0', lifespan=lifespan)
Layer = Literal['rainfall', 'temperature', 'evapotranspiration', 'cloud', 'soil', 'vegetation']
Source = Literal[
    'chirps', 'imerg', 'himawari', 'smap', 'gibs_ndvi', 'modis',
    'open_meteo', 'open_meteo_grid', 'nasa_power', 'pagasa', 'psa',
]

ALL_LAYERS: list[Layer] = ['rainfall', 'cloud', 'temperature', 'evapotranspiration', 'vegetation', 'soil']
LAYER_STALE_DAYS = {
    'rainfall': 3,
    'cloud': 2,
    'temperature': 2,
    'evapotranspiration': 2,
    'vegetation': 10,
    'soil': 5,
}
SOURCE_PRIORITY = {
    'rainfall': {'chirps': 0, 'imerg': 1, 'open_meteo': 2, 'open_meteo_grid': 3, 'nasa_power': 4},
    'cloud': {'himawari': 0, 'open_meteo': 1, 'open_meteo_grid': 2},
    'soil': {'smap': 0, 'open_meteo': 1, 'open_meteo_grid': 2},
    'vegetation': {'modis': 0, 'gibs_ndvi': 1},
    'temperature': {'open_meteo': 0, 'open_meteo_grid': 1, 'nasa_power': 2},
    'evapotranspiration': {'open_meteo': 0, 'open_meteo_grid': 1, 'nasa_power': 2},
}


class CollectRequest(BaseModel):
    area_ids: list[str] = Field(default_factory=list, max_length=2000)
    all_areas: bool = False
    sources: list[Source] = Field(default=['open_meteo', 'modis'], min_length=1, max_length=12)


def authorize(request):
    if config.ADMIN_KEY:
        if not hmac.compare_digest(request.headers.get('X-Admin-Key', ''), config.ADMIN_KEY):
            raise HTTPException(401, 'A valid X-Admin-Key is required for collection.')
    else:
        origin = request.headers.get('Origin')
        if origin and urlparse(origin).hostname not in ('localhost', '127.0.0.1', '::1'):
            raise HTTPException(403, 'Collection is restricted to the local workspace. Configure ANI_ADMIN_KEY before deployment.')


def _row_rank(row: dict) -> tuple[int, int]:
    # Prefer valid numeric readings over cloud-masked placeholders on the same date, then prioritize satellite over reanalysis.
    is_masked = 1 if (row.get('status') == 'cloud-masked' or row.get('value') is None) else 0
    layer_prio = SOURCE_PRIORITY.get(row['layer'], {})
    return is_masked, layer_prio.get(row['source'], 99)


def selected_rows(rows: list[dict]) -> list[dict]:
    result: dict[tuple[str, str, str], dict] = {}
    for row in sorted(rows, key=_row_rank):
        key = (row['area_id'], row['layer'], row['date'])
        if key not in result:
            result[key] = row
    return list(result.values())


def evaluate_freshness(total_samples: int, valid_samples: int, cloud_masked_samples: int,
                       latest_date: str | None, stale_after_days: int,
                       latest_cloud_masked_ratio: float = 0.0) -> tuple[str, str]:
    """Compute canonical freshness state: available | stale | missing | cloud-masked."""
    if total_samples == 0 or not latest_date:
        return 'missing', 'No observations stored yet'
    if valid_samples == 0 and cloud_masked_samples > 0:
        return 'cloud-masked', f'All retrieved pixels on {latest_date} are cloud-masked'
    if latest_cloud_masked_ratio >= 0.5:
        return 'cloud-masked', f'{int(round(latest_cloud_masked_ratio * 100))}% cloud-masked on {latest_date}'

    cur = providers.today()
    iso_part = latest_date[:10]
    try:
        d_obj = date.fromisoformat(iso_part)
        lag = (cur - d_obj).days
    except ValueError:
        return 'available', f'Latest record: {latest_date}'

    if lag > stale_after_days:
        return 'stale', f'{lag}d behind {cur.isoformat()} (freshness window ≤{stale_after_days}d)'
    if lag <= 0:
        return 'available', f'Current as of {iso_part}'
    return 'available', f'{lag}d upstream latency (within {stale_after_days}d window)'


def _layer_inventory_for_rows(rows: list[dict]) -> dict[str, dict]:
    inv: dict[str, dict] = {}
    for lyr in ALL_LAYERS:
        l_rows = [r for r in rows if r['layer'] == lyr]
        valid_rows = [r for r in l_rows if r.get('value') is not None and r.get('status', 'valid') == 'valid']
        masked_rows = [r for r in l_rows if r.get('status') == 'cloud-masked']
        dates_all = sorted({r['date'] for r in l_rows})
        latest_d = dates_all[-1] if dates_all else None
        latest_rows = [r for r in l_rows if r['date'] == latest_d] if latest_d else []
        latest_cm_ratio = (
            sum(1 for r in latest_rows if r.get('status') == 'cloud-masked') / len(latest_rows)
            if latest_rows else 0.0
        )
        state, reason = evaluate_freshness(
            total_samples=len(l_rows),
            valid_samples=len(valid_rows),
            cloud_masked_samples=len(masked_rows),
            latest_date=latest_d,
            stale_after_days=LAYER_STALE_DAYS[lyr],
            latest_cloud_masked_ratio=latest_cm_ratio,
        )
        latest_obs = next((r for r in reversed(sorted(l_rows, key=lambda x: x['date'])) if r.get('value') is not None), None)
        inv[lyr] = {
            'layer': lyr,
            'coverage_start': dates_all[0] if dates_all else None,
            'coverage_end': latest_d,
            'latest_valid_date': latest_obs['date'] if latest_obs else None,
            'primary_source': latest_obs['source'] if latest_obs else (latest_rows[0]['source'] if latest_rows else None),
            'samples': len(l_rows),
            'valid_samples': len(valid_rows),
            'cloud_masked_samples': len(masked_rows),
            'freshness': state,
            'freshness_reason': reason,
        }
    return inv


@app.get('/api/health')
def health():
    return {'status': 'ok', 'areas': len(ingestion.AREAS), 'scheduled_refresh_hours': config.REFRESH_HOURS, 'today': providers.today().isoformat()}


@app.get('/api/sources')
def sources():
    processed_rasters = rasters.rebuild_processed_catalog()
    rasters_by_source: dict[str, list[dict]] = {}
    for item in processed_rasters:
        rasters_by_source.setdefault(item['source'], []).append(item)

    with db.connection() as conn:
        obs_stats = {
            r['source']: dict(r)
            for r in conn.execute(
                '''SELECT source,
                          COUNT(*) AS samples,
                          SUM(CASE WHEN value IS NOT NULL AND COALESCE(status,'valid')='valid' THEN 1 ELSE 0 END) AS valid_samples,
                          SUM(CASE WHEN status='cloud-masked' THEN 1 ELSE 0 END) AS cloud_masked_samples,
                          COUNT(DISTINCT area_id) AS areas,
                          MIN(date) AS coverage_start,
                          MAX(date) AS latest_date,
                          MAX(fetched_at) AS last_collected
                   FROM observations GROUP BY source'''
            )
        }
        latest_masked = {
            r['source']: r['masked_ratio']
            for r in conn.execute(
                '''SELECT o.source,
                          AVG(CASE WHEN o.status='cloud-masked' THEN 1.0 ELSE 0.0 END) AS masked_ratio
                   FROM observations o
                   JOIN (SELECT source, MAX(date) AS max_d FROM observations GROUP BY source) m
                     ON o.source=m.source AND o.date=m.max_d
                   GROUP BY o.source'''
            )
        }
        runs = {r['source']: dict(r) for r in conn.execute('SELECT * FROM source_runs')}
        adv_row = conn.execute('SELECT COUNT(*) AS cnt, MIN(issued_at) AS min_d, MAX(issued_at) AS max_d, MAX(fetched_at) AS last_c FROM advisories').fetchone()
        psa_row = conn.execute('SELECT COUNT(*) AS cnt, MIN(year) AS min_y, MAX(year) AS max_y, COUNT(DISTINCT province) AS provs, MAX(fetched_at) AS last_c FROM farm_stats').fetchone()
        layer_rows = [
            dict(r) for r in conn.execute(
                '''SELECT layer,
                          COUNT(*) AS samples,
                          SUM(CASE WHEN value IS NOT NULL AND COALESCE(status,'valid')='valid' THEN 1 ELSE 0 END) AS valid_samples,
                          SUM(CASE WHEN status='cloud-masked' THEN 1 ELSE 0 END) AS cloud_masked_samples,
                          COUNT(DISTINCT area_id) AS areas,
                          MIN(date) AS coverage_start,
                          MAX(date) AS coverage_end,
                          MAX(fetched_at) AS last_collected
                   FROM observations GROUP BY layer'''
            )
        ]
        layer_latest_masked = {
            r['layer']: r['masked_ratio']
            for r in conn.execute(
                '''SELECT o.layer,
                          AVG(CASE WHEN o.status='cloud-masked' THEN 1.0 ELSE 0.0 END) AS masked_ratio
                   FROM observations o
                   JOIN (SELECT layer, MAX(date) AS max_d FROM observations GROUP BY layer) m
                     ON o.layer=m.layer AND o.date=m.max_d
                   GROUP BY o.layer'''
            )
        }

    source_list = []
    for key, info in providers.SOURCES.items():
        run = runs.get(key, {})
        r_items = rasters_by_source.get(key, [])
        if key == 'pagasa':
            cnt = int(adv_row['cnt'] or 0)
            c_start = adv_row['min_d'][:10] if adv_row['min_d'] else None
            c_end = adv_row['max_d'][:10] if adv_row['max_d'] else None
            state, reason = evaluate_freshness(cnt, cnt, 0, c_end, info['stale_after_days'])
            stat_entry = {
                'samples': cnt, 'valid_samples': cnt, 'cloud_masked_samples': 0,
                'areas': len(ingestion.AREAS) if cnt else 0,
                'coverage_start': c_start, 'coverage_end': c_end, 'latest_date': c_end,
                'last_collected': adv_row['last_c'],
            }
        elif key == 'psa':
            cnt = int(psa_row['cnt'] or 0)
            c_start = f"{psa_row['min_y']}-Q1" if psa_row['min_y'] else None
            c_end = f"{psa_row['max_y']}-Q3" if psa_row['max_y'] else None
            state = 'available' if cnt > 0 else 'missing'
            reason = f"{psa_row['provs']} provinces · {psa_row['min_y']}–{psa_row['max_y']}" if cnt else 'No PSA crop statistics stored yet'
            stat_entry = {
                'samples': cnt, 'valid_samples': cnt, 'cloud_masked_samples': 0,
                'areas': int(psa_row['provs'] or 0),
                'coverage_start': c_start, 'coverage_end': c_end, 'latest_date': c_end,
                'last_collected': psa_row['last_c'],
            }
        elif key == 'open_meteo_grid' and not obs_stats.get(key):
            r_dates = sorted({m['date'] for m in r_items})
            c_start = r_dates[0] if r_dates else None
            c_end = r_dates[-1] if r_dates else None
            state, reason = evaluate_freshness(len(r_items), len(r_items), 0, c_end, info['stale_after_days'])
            stat_entry = {
                'samples': len(r_items), 'valid_samples': len(r_items), 'cloud_masked_samples': 0,
                'areas': len(ingestion.AREAS) if r_items else 0,
                'coverage_start': c_start, 'coverage_end': c_end, 'latest_date': c_end,
                'last_collected': max((m['created_at'] for m in r_items), default=run.get('last_success')),
            }
        else:
            st = obs_stats.get(key, {})
            total = int(st.get('samples') or 0)
            val_cnt = int(st.get('valid_samples') or 0)
            cm_cnt = int(st.get('cloud_masked_samples') or 0)
            latest_d = st.get('latest_date')
            state, reason = evaluate_freshness(
                total, val_cnt, cm_cnt, latest_d, info['stale_after_days'],
                latest_cloud_masked_ratio=float(latest_masked.get(key) or 0.0),
            )
            stat_entry = {
                'samples': total,
                'valid_samples': val_cnt,
                'cloud_masked_samples': cm_cnt,
                'areas': int(st.get('areas') or 0),
                'coverage_start': st.get('coverage_start'),
                'coverage_end': latest_d,
                'latest_date': latest_d,
                'last_collected': st.get('last_collected'),
            }

        source_list.append({
            'id': key,
            **info,
            **stat_entry,
            'rasters_count': len(r_items),
            'upstream_latest': run.get('upstream_latest'),
            'last_error': run.get('last_error'),
            'freshness': state,
            'freshness_reason': reason,
        })

    by_layer_map = {r['layer']: r for r in layer_rows}
    layer_summary = {}
    for lyr in ALL_LAYERS:
        st = by_layer_map.get(lyr, {})
        total = int(st.get('samples') or 0)
        val_cnt = int(st.get('valid_samples') or 0)
        cm_cnt = int(st.get('cloud_masked_samples') or 0)
        c_end = st.get('coverage_end')
        state, reason = evaluate_freshness(
            total, val_cnt, cm_cnt, c_end, LAYER_STALE_DAYS[lyr],
            latest_cloud_masked_ratio=float(layer_latest_masked.get(lyr) or 0.0),
        )
        l_rasters = [m for m in processed_rasters if m['layer'] == lyr]
        layer_summary[lyr] = {
            'layer': lyr,
            'samples': total,
            'valid_samples': val_cnt,
            'cloud_masked_samples': cm_cnt,
            'areas': int(st.get('areas') or 0),
            'coverage_start': st.get('coverage_start'),
            'coverage_end': c_end,
            'last_collected': st.get('last_collected'),
            'rasters_count': len(l_rasters),
            'raster_dates': sorted({m['date'] for m in l_rasters}),
            'freshness': state,
            'freshness_reason': reason,
        }

    return {
        'today': providers.today().isoformat(),
        'sources': source_list,
        'layers': layer_summary,
        'rasters_total': len(processed_rasters),
        'pending_integrations': [
            'Huawei Cloud OBS static sync (data/processed ready for obsutil upload)',
            'Sentinel-2 10 m optical scenes (Phase 2 Copernicus Data Space)',
        ],
        'write_auth_required': bool(config.ADMIN_KEY),
    }


@app.get('/api/point')
def point(area_id: str, layer: Layer = 'rainfall'):
    if area_id not in ingestion.AREAS:
        raise HTTPException(404, 'Unknown Philippine municipality ID.')
    area = ingestion.AREAS[area_id]
    rows = selected_rows(db.rows_for(area_id))
    all_dates = [r['date'] for r in rows]
    relevant = [r['date'] for r in rows if r['layer'] == layer]

    if all_dates:
        max_all = date.fromisoformat(max(all_dates))
        window_start = (max_all - timedelta(days=6)).isoformat()
        if relevant and not any(d >= window_start for d in relevant):
            end = date.fromisoformat(max(relevant))
        else:
            end = max_all
    else:
        end = providers.today()

    dates = [(end - timedelta(days=6 - i)).isoformat() for i in range(7)]
    with db.connection() as conn:
        adv_rows = [dict(r) for r in conn.execute('SELECT * FROM advisories ORDER BY issued_at DESC, id ASC')]
    local_advs = []
    for a in adv_rows:
        provs = json.loads(a['provinces'] or '[]')
        if a['text_status'] != 'archive' and (area['province'] in provs or not provs):
            local_advs.append({**a, 'provinces': provs})

    return {
        'area': area,
        'dates': dates,
        'observations': rows,
        'layer_inventory': _layer_inventory_for_rows(rows),
        'advisories': local_advs[:4],
        'source_info': providers.SOURCES,
        'spatial_note': 'Satellite raster pixel or reanalysis grid sampled at municipality coordinates; cloud-masked pixels stay blank.',
    }


@app.get('/api/map')
def map_values(layer: Layer, observation_date: date):
    iso_d = observation_date.isoformat()
    with db.connection() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM observations WHERE layer=? AND date=?', (layer, iso_d))]
        # For rolling 8-day optical NDVI composites, match the active composite within 8 days when exact day has no composite stamp.
        composite_used = None
        if not rows and layer == 'vegetation':
            comp = conn.execute(
                'SELECT MAX(date) FROM observations WHERE layer=? AND date<=? AND date>=?',
                (layer, iso_d, (observation_date - timedelta(days=8)).isoformat()),
            ).fetchone()[0]
            if comp:
                rows = [dict(r) for r in conn.execute('SELECT * FROM observations WHERE layer=? AND date=?', (layer, comp))]
                composite_used = comp

    chosen = selected_rows(rows)
    raster_meta = rasters.find_raster(layer, iso_d)
    return {
        'date': iso_d,
        'composite_date': composite_used or (raster_meta.get('date') if raster_meta else None),
        'layer': layer,
        'values': {r['area_id']: r for r in chosen},
        'raster': raster_meta,
    }


@app.get('/api/rasters/{layer}/{filename}')
def serve_raster(layer: Layer, filename: str):
    if '/' in filename or '\\' in filename or '..' in filename or not filename.endswith('.png'):
        raise HTTPException(400, 'Invalid raster filename.')
    target = config.PROCESSED_DIR / 'rasters' / layer / filename
    if not target.exists():
        raise HTTPException(404, 'Processed raster overlay not found.')
    return FileResponse(target, media_type='image/png', headers={'Cache-Control': 'public, max-age=3600'})


@app.get('/api/advisories')
def get_advisories(province: str | None = None):
    with db.connection() as conn:
        rows = [dict(r) for r in conn.execute('SELECT * FROM advisories ORDER BY issued_at DESC, id ASC')]
    parsed = [{**r, 'provinces': json.loads(r['provinces'] or '[]')} for r in rows]
    if province:
        parsed = [r for r in parsed if province in r['provinces'] or not r['provinces']]
    province_status: dict[str, dict] = {}
    for r in parsed:
        if r['product'] in ('Farm Weather Forecast', 'Regional Weather Forecast'):
            for p in r['provinces']:
                prev = province_status.get(p)
                if (
                    not prev
                    or (r['text_status'] == 'warning' and prev['severity'] != 'warning')
                    or (r['product'] == 'Farm Weather Forecast' and prev['severity'] != 'warning')
                ):
                    province_status[p] = {
                        'province': p,
                        'severity': r['text_status'],
                        'title': r['title'],
                        'product': r['product'],
                        'excerpt': r['excerpt'],
                        'issued_at': r['issued_at'],
                    }
    return {
        'collected': bool(parsed),
        'count': len(parsed),
        'advisories': parsed,
        'province_map': province_status,
        'source_url': 'https://www.pagasa.dost.gov.ph/agri-weather',
    }


@app.get('/api/crops')
def get_crops(province: str | None = None):
    data = advisories_crops.build_crop_profiles(ingestion.AREAS, current_month=providers.today().month)
    if province:
        if province not in data['provinces']:
            raise HTTPException(404, 'Unknown Philippine province.')
        return {'collected': data['collected'], 'national': data['national'], 'profile': data['provinces'][province], 'source_citation': data['source_citation']}
    return data


@app.post('/api/jobs', status_code=202)
@app.post('/api/sync', status_code=202)
@app.post('/api/collect', status_code=202)
def collect(body: CollectRequest, request: Request):
    authorize(request)
    has_area_sources = any(s in providers.ADAPTERS for s in body.sources)
    ids = list(ingestion.AREAS) if body.all_areas else body.area_ids
    if has_area_sources and not ids:
        raise HTTPException(422, 'Choose at least one area or all_areas.')
    if any(area_id not in ingestion.AREAS for area_id in ids):
        raise HTTPException(422, 'Unknown municipality ID in collection request.')
    job_id, created = ingestion.enqueue(ids, body.sources)
    if not created:
        raise HTTPException(409, {'message': 'A collection is already running. Follow or cancel it before starting another.', 'job_id': job_id})
    return db.job_status(job_id)


@app.get('/api/jobs')
def jobs():
    with db.connection() as conn:
        ids = [r[0] for r in conn.execute('SELECT id FROM jobs ORDER BY created_at DESC LIMIT 10')]
    return [db.job_status(job_id) for job_id in ids]


@app.get('/api/jobs/{job_id}')
def job(job_id: str):
    result = db.job_status(job_id)
    if result is None:
        raise HTTPException(404, 'Collection job not found.')
    return result


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str, request: Request):
    authorize(request)
    if db.job_status(job_id) is None:
        raise HTTPException(404, 'Collection job not found.')
    ingestion.cancel(job_id)
    return db.job_status(job_id)
