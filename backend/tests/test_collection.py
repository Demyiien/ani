import json
import numpy as np
import pytest
from fastapi.testclient import TestClient
from ani import advisories_crops, config, db, ingestion, main, providers, rasters
from ani.main import app

@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DB_PATH',tmp_path/'test.sqlite3')
    monkeypatch.setattr(config,'PROCESSED_DIR',tmp_path/'processed')
    monkeypatch.setattr(config,'ADMIN_KEY','')
    db.init()
    return TestClient(app)

def test_weather_full_days_zero_and_missing():
    times=[f'2026-10-01T{h:02}:00' for h in range(24)]
    rows=providers.parse_weather({'daily':{'time':['2026-10-01','2026-10-02'],'rain_sum':[0,None],'temperature_2m_mean':[30,float('nan')]},'hourly':{'time':times,'cloud_cover':[25]*24,'soil_moisture_0_to_7cm':[.3]*23+[None]}})
    assert {r['layer'] for r in rows}=={'rainfall','temperature','cloud'}
    assert next(r for r in rows if r['layer']=='rainfall')['value']==0

def test_power_fill_values():
    rows=providers.parse_power({'properties':{'parameter':{'T2M':{'20261001':-999,'20261002':29},'PRECTOTCORR':{'20261001':0}}}})
    assert len(rows)==2 and any(r['value']==0 for r in rows)

def test_modis_scaling_quality():
    rows=providers.parse_modis({'subset':[{'band':'250m_16_days_NDVI','calendar_date':'2026-08-29','data':[6000,9000,-3000,8000]},{'band':'250m_16_days_pixel_reliability','calendar_date':'2026-08-29','data':[0,3,0,1]}]})
    assert len(rows)==1 and rows[0]['value']==.6
    assert providers.parse_modis({'subset':[{'band':'250m_16_days_NDVI','calendar_date':'2026-08-29','data':[6000]}]})==[]

def test_persistence_idempotency_missing(store):
    area=next(iter(ingestion.AREAS.values()))
    row={'layer':'rainfall','date':'2026-10-01','value':0,'unit':'mm','source':'open_meteo','kind':'reanalysis'}
    db.save_rows(area,[row]);db.save_rows(area,[{**row,'value':2}])
    assert len(db.rows_for(area['id']))==1 and db.rows_for(area['id'])[0]['value']==2
    other=next(p for p in ingestion.AREAS if p!=area['id'])
    assert store.get('/api/point',params={'area_id':other}).json()['observations']==[]
    assert store.get('/api/map',params={'layer':'rainfall','observation_date':'2026-10-02'}).json()['values']=={}

def test_queue_validation_conflict_cancel(store):
    area_id=next(iter(ingestion.AREAS))
    assert store.post('/api/collect',json={'area_ids':['invalid']}).status_code==422
    assert store.post('/api/collect',json={'area_ids':[]}).status_code==422
    body={'area_ids':[area_id,area_id],'sources':['open_meteo','open_meteo']}
    result=store.post('/api/collect',json=body)
    assert result.status_code==202
    job=result.json();assert job['total']==1
    assert store.post('/api/collect',json=body).status_code==409
    assert store.post(f"/api/jobs/{job['id']}/cancel").json()['status']=='cancelled'
    assert store.post('/api/collect',json=body).status_code==202

def test_write_security(store,monkeypatch):
    body={'area_ids':[next(iter(ingestion.AREAS))]}
    assert store.post('/api/collect',json=body,headers={'Origin':'https://unrelated.example'}).status_code==403
    monkeypatch.setattr(config,'ADMIN_KEY','test-secret')
    assert store.post('/api/collect',json=body).status_code==401
    assert store.post('/api/collect',json=body,headers={'X-Admin-Key':'test-secret'}).status_code==202

def test_restart_resume(store):
    job_id,_=ingestion.enqueue([next(iter(ingestion.AREAS))],['open_meteo'])
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='running'");conn.execute("UPDATE jobs SET status='running'")
    db.init()
    assert db.job_status(job_id)['status']=='queued'
    with db.connection() as conn: assert conn.execute('SELECT status FROM tasks').fetchone()[0]=='queued'

def test_point_map_source_consistency(store):
    area=next(iter(ingestion.AREAS.values()))
    base={'layer':'rainfall','date':'2026-10-01','unit':'mm','kind':'reanalysis'}
    db.save_rows(area,[{**base,'value':1,'source':'nasa_power'},{**base,'value':2,'source':'open_meteo'}])
    point=store.get('/api/point',params={'area_id':area['id']}).json()['observations'][0]
    mapped=store.get('/api/map',params={'layer':'rainfall','observation_date':'2026-10-01'}).json()['values'][area['id']]
    assert point['value']==mapped['value']==2

def test_concurrent_enqueue_has_one_owner(store):
    from concurrent.futures import ThreadPoolExecutor
    area=next(iter(ingestion.AREAS))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _: ingestion.enqueue([area],['open_meteo']),range(4)))
    assert sum(created for _,created in results)==1
    assert len({job_id for job_id,_ in results})==1

def test_evapotranspiration_and_cloud_masked_modis():
    times=[f'2026-11-01T{h:02}:00' for h in range(24)]
    w_rows=providers.parse_weather({'daily':{'time':['2026-11-01'],'rain_sum':[4.2],'temperature_2m_mean':[28.4],'et0_fao_evapotranspiration':[4.65]},'hourly':{'time':times,'cloud_cover':[40]*24,'soil_moisture_0_to_7cm':[.28]*24}})
    assert {r['layer'] for r in w_rows}=={'rainfall','temperature','evapotranspiration','cloud','soil'}
    assert next(r for r in w_rows if r['layer']=='evapotranspiration')['value']==4.65
    p_rows=providers.parse_power({'properties':{'parameter':{'EVPTRNS':{'20261101':4.9,'20261102':-999}}}})
    assert len(p_rows)==1 and p_rows[0]['layer']=='evapotranspiration' and p_rows[0]['value']==2.0
    masked=providers.parse_modis({'subset':[{'band':'250m_16_days_NDVI','calendar_date':'2026-10-31','data':[7200]},{'band':'250m_16_days_pixel_reliability','calendar_date':'2026-10-31','data':[3]}]},include_masked=True)
    assert len(masked)==1 and masked[0]['value'] is None and masked[0]['status']=='cloud-masked'

def test_satellite_source_priority_and_cloud_masked_fallback(store):
    area=next(iter(ingestion.AREAS.values()))
    db.save_rows(area,[
        {'layer':'rainfall','date':'2026-10-25','value':5,'unit':'mm','source':'open_meteo','kind':'reanalysis'},
        {'layer':'rainfall','date':'2026-10-25','value':12,'unit':'mm','source':'imerg','kind':'satellite'},
        {'layer':'rainfall','date':'2026-10-25','value':14,'unit':'mm','source':'chirps','kind':'satellite'},
        {'layer':'cloud','date':'2026-11-01','value':55,'unit':'%','source':'open_meteo','kind':'reanalysis'},
        {'layer':'cloud','date':'2026-11-01','value':72,'unit':'%','source':'himawari','kind':'satellite'},
        {'layer':'soil','date':'2026-10-28','value':.25,'unit':'m³/m³','source':'open_meteo','kind':'reanalysis'},
        {'layer':'soil','date':'2026-10-28','value':.34,'unit':'m³/m³','source':'smap','kind':'satellite'},
        {'layer':'vegetation','date':'2026-10-31','value':None,'unit':'NDVI','source':'modis','kind':'satellite','status':'cloud-masked'},
    ])
    obs={r['layer']:r for r in store.get('/api/point',params={'area_id':area['id']}).json()['observations']}
    assert obs['rainfall']['source']=='chirps' and obs['rainfall']['value']==14
    assert obs['cloud']['source']=='himawari' and obs['cloud']['value']==72
    assert obs['soil']['source']=='smap' and obs['soil']['value']==.34
    assert obs['vegetation']['status']=='cloud-masked' and obs['vegetation']['value'] is None

def test_freshness_states_and_data_inventory(store):
    today_iso=providers.today().isoformat()
    area=next(iter(ingestion.AREAS.values()))
    assert main.evaluate_freshness(0,0,0,None,3)[0]=='missing'
    assert main.evaluate_freshness(1,1,0,today_iso,3)[0]=='available'
    assert main.evaluate_freshness(1,1,0,'2026-10-01',3)[0]=='stale'
    assert main.evaluate_freshness(2,0,2,today_iso,10)[0]=='cloud-masked'
    db.save_rows(area,[
        {'layer':'rainfall','date':today_iso,'value':8.5,'unit':'mm','source':'imerg','kind':'satellite'},
        {'layer':'soil','date':'2026-10-15','value':.31,'unit':'m³/m³','source':'smap','kind':'satellite'},
        {'layer':'vegetation','date':today_iso,'value':None,'unit':'NDVI','source':'gibs_ndvi','kind':'satellite','status':'cloud-masked'},
    ])
    pt=store.get('/api/point',params={'area_id':area['id']}).json()['layer_inventory']
    assert pt['rainfall']['freshness']=='available' and pt['rainfall']['coverage_end']==today_iso
    assert pt['soil']['freshness']=='stale'
    assert pt['vegetation']['freshness']=='cloud-masked'
    assert pt['temperature']['freshness']=='missing'
    src_resp=store.get('/api/sources').json()
    by_id={s['id']:s for s in src_resp['sources']}
    assert by_id['imerg']['freshness']=='available'
    assert by_id['smap']['freshness']=='stale'
    assert by_id['gibs_ndvi']['freshness']=='cloud-masked'
    assert by_id['chirps']['freshness']=='missing'

def test_georeferenced_raster_export_and_serving(store):
    grid=np.full((32,20),24.5,dtype=np.float32)
    meta=rasters.save_raster('rainfall','imerg','2026-10-31',grid)
    assert meta['url']=='/api/rasters/rainfall/imerg_2026-10-31.png'
    mapped=store.get('/api/map',params={'layer':'rainfall','observation_date':'2026-10-31'}).json()
    assert mapped['raster']['source']=='imerg' and len(mapped['raster']['coordinates'])==4
    png=store.get(meta['url'])
    assert png.status_code==200 and png.headers['content-type']=='image/png' and png.content[:8]==b'\x89PNG\r\n\x1a\n'
    assert store.get('/api/rasters/rainfall/..%5Csecret.png').status_code in (400,404)
    assert store.get('/api/rasters/rainfall/not_a_png.txt').status_code==400

def test_pagasa_advisories_and_psa_crop_profiles(store):
    daily_html='''<html><body>Issued at: 4:00 PM, 01 November 2026
    Synopsis Shear Line affecting Northern Luzon.
    Forecast Weather Conditions
    <table><tr><th>Place</th><th>Weather Condition</th><th>Caused By</th><th>Impacts</th></tr>
    <tr><td>Central Luzon</td><td>Cloudy skies with scattered rains</td><td>Easterlies</td><td>Possible flash floods</td></tr></table></body></html>'''
    agri_html='''<html><body>Issued at: 8:00 AM, 30 October 2026 SYNOPSIS Easterlies affecting Luzon. Forecast Area
    <table><tr><th>Forecast Area</th><th>Agri-Weather</th><th>Wind</th><th>Low</th><th>Up</th><th>RH</th><th>Leaf</th></tr>
    <tr><td>Nueva Ecija</td><td>Partly cloudy to cloudy skies</td><td>Light</td><td>24-32</td><td>19-26</td><td>75-92</td><td>4-8</td></tr></table></body></html>'''
    adv_html='<html><body>No Weather Advisory Issued.</body></html>'
    pub_html='<a href="pf.pdf">pf.pdf</a> 01-Nov-2026 08:00'
    adv_records=advisories_crops.parse_pagasa_pages(daily_html,agri_html,adv_html,pub_html,ingestion.AREAS)
    with db.connection() as conn:
        for r in adv_records:
            conn.execute('INSERT INTO advisories VALUES (?,?,?,?,?,?,?,?,?)',(r['id'],r['product'],r['title'],r['issued_at'],r['url'],json.dumps(r['provinces']),r['excerpt'],r['text_status'],db.now()))
    adv_resp=store.get('/api/advisories',params={'province':'Nueva Ecija'}).json()
    assert adv_resp['collected'] is True
    assert any(a['product']=='Farm Weather Forecast' for a in adv_resp['advisories'])
    issued_list=[a['issued_at'] for a in adv_resp['advisories']]
    assert issued_list==sorted(issued_list,reverse=True)
    assert adv_resp['province_map']['Nueva Ecija']['severity']=='warning'
    assert 'Nueva Ecija' not in advisories_crops.match_area_phrase_to_provinces('Ilocos Region, Cordillera Administrative Region, and Batanes',ingestion.AREAS)

    meta={'variables':[
        {'code':'Ecosystem/Croptype','values':['0','5'],'valueTexts':['Palay','Corn']},
        {'code':'Geolocation','values':['1'],'valueTexts':['....Nueva Ecija']},
        {'code':'Year','values':['0'],'valueTexts':['2025']},
        {'code':'Period','values':['0','1','2','3','4'],'valueTexts':['Quarter 1','Quarter 2','Quarter 3','Quarter 4','Annual']},
    ]}
    px_payload={'data':[
        {'key':['0','1','0','0'],'values':['400000']},
        {'key':['0','1','0','1'],'values':['500000']},
        {'key':['0','1','0','2'],'values':['250000']},
        {'key':['0','1','0','3'],'values':['750000']},
        {'key':['0','1','0','4'],'values':['1900000']},
    ]}
    valid_provs={a['province'] for a in ingestion.AREAS.values()}
    psa_rows=advisories_crops.parse_pxweb_json(px_payload,meta,'production','MT',valid_provs)
    assert len(psa_rows)==5
    with db.connection() as conn:
        for r in psa_rows:
            conn.execute('INSERT INTO farm_stats VALUES (?,?,?,?,?,?,?,?,?)',(r['province'],r['crop'],r['metric'],r['year'],r['period'],r['value'],r['unit'],r['source'],db.now()))
    crop_resp=store.get('/api/crops',params={'province':'Nueva Ecija'}).json()
    assert crop_resp['collected'] is True
    assert crop_resp['profile']['palay_production_mt']==1900000.0
    assert crop_resp['profile']['peak_harvest_quarter']=='Q4'


def test_jobs_post_route_bypasses_collect_adblock_filters(store,monkeypatch):
    async def fake_pagasa(_c,_areas):
        return 7
    monkeypatch.setitem(providers.NATIONAL_ADAPTERS,'pagasa',fake_pagasa)
    r=store.post('/api/jobs',json={'area_ids':[],'all_areas':False,'sources':['pagasa']},headers={'Origin':'http://127.0.0.1:5174'})
    assert r.status_code==202
    job_id=r.json()['id']
    assert store.get(f'/api/jobs/{job_id}').status_code==200


def test_himawari_clean_ir_decoding_and_bilinear_resample():
    import io
    import numpy as np
    from PIL import Image
    xml='''<ColorMap>
      <ColorMapEntry rgb="102,102,102" transparent="false" value="[-75.1,-74.1)"/>
      <ColorMapEntry rgb="200,0,0" transparent="false" value="[-62.0,-60.0)"/>
      <ColorMapEntry rgb="100,100,100" transparent="false" value="[18.15,19.15)"/>
    </ColorMap>'''
    cmap=rasters.parse_gibs_colormap(xml)
    # Warm clear-sky pixel with rgb=(102,102,102) surrounded by warm (100,100,100) must decode as warm (~+18.65°C), NOT -74.6°C!
    arr=np.full((12,12,4),255,dtype=np.uint8)
    arr[...,:3]=100
    arr[5,5,:3]=102
    buf=io.BytesIO()
    Image.fromarray(arr).save(buf,format='PNG')
    bt,_=rasters.decode_himawari_png(buf.getvalue(),cmap)
    assert float(bt[5,5])>15.0
    smoothed=rasters.smooth_grid_3x3(bt)
    merc=rasters.resample_to_mercator(smoothed,rasters.PH_BOUNDS,out_height=64,out_width=40)
    assert merc.shape==(64,40) and np.isfinite(merc).all()



