import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from . import config

def now(): return datetime.now(timezone.utc).isoformat()

@contextmanager
def connection():
    db = sqlite3.connect(config.DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    finally: db.close()

def init():
    with connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS observations (
          area_id TEXT, layer TEXT, date TEXT, value REAL, unit TEXT, source TEXT,
          kind TEXT, quality TEXT, fetched_at TEXT, sample_lat REAL, sample_lon REAL,
          PRIMARY KEY(area_id,layer,date,source));
        CREATE INDEX IF NOT EXISTS observations_lookup ON observations(layer,date,area_id);
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, job_id TEXT, area_id TEXT, source TEXT,
          status TEXT, error TEXT, row_count INTEGER DEFAULT 0, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT, fetched_at TEXT);
        CREATE TABLE IF NOT EXISTS source_runs (source TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
          last_error TEXT, upstream_latest TEXT, upstream_checked TEXT);
        CREATE TABLE IF NOT EXISTS advisories (id TEXT PRIMARY KEY, product TEXT, title TEXT, issued_at TEXT,
          url TEXT, provinces TEXT, excerpt TEXT, text_status TEXT, fetched_at TEXT);
        CREATE TABLE IF NOT EXISTS farm_stats (province TEXT, crop TEXT, metric TEXT, year INTEGER, period TEXT,
          value REAL, unit TEXT, source TEXT, fetched_at TEXT, PRIMARY KEY(province,crop,metric,year,period));
        ''')
        columns={r['name'] for r in db.execute('PRAGMA table_info(observations)')}
        # status: valid | cloud-masked | no-coverage. Masked rows keep value NULL; nothing is filled in.
        if 'status' not in columns: db.execute("ALTER TABLE observations ADD COLUMN status TEXT DEFAULT 'valid'")
        if 'coverage' not in columns: db.execute('ALTER TABLE observations ADD COLUMN coverage REAL')
        # A single worker safely resumes interrupted tasks after an API restart.
        db.execute("UPDATE tasks SET status='queued' WHERE status='running'")
        db.execute("UPDATE jobs SET status='queued' WHERE status='running'")

def _row(area, r, fetched):
    return (area['id'],r['layer'],r['date'],r['value'],r['unit'],r['source'],r['kind'],r.get('quality','valid'),fetched,
            r.get('lat',area['lat']),r.get('lon',area['lon']),r.get('status','valid'),r.get('coverage'))

UPSERT='''INSERT INTO observations (area_id,layer,date,value,unit,source,kind,quality,fetched_at,sample_lat,sample_lon,status,coverage)
  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
  ON CONFLICT(area_id,layer,date,source) DO UPDATE SET value=excluded.value,
  quality=excluded.quality,fetched_at=excluded.fetched_at,status=excluded.status,coverage=excluded.coverage,
  sample_lat=excluded.sample_lat,sample_lon=excluded.sample_lon'''

def save_rows(area, rows):
    fetched = now()
    with connection() as db:
        db.executemany(UPSERT,[_row(area,r,fetched) for r in rows])
    return len(rows)

def save_area_rows(areas, rows_by_area):
    """Bulk save for nationwide raster sources: {area_id: [rows]}."""
    fetched=now()
    params=[_row(areas[area_id],r,fetched) for area_id,rows in rows_by_area.items() for r in rows]
    with connection() as db: db.executemany(UPSERT,params)
    return len(params)

def rows_for(area_id):
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM observations WHERE area_id=? ORDER BY date', (area_id,))]

def record_run(source, error=None, upstream_latest=None):
    stamp=now()
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO source_runs (source) VALUES (?)',(source,))
        db.execute('UPDATE source_runs SET last_attempt=?,last_error=? WHERE source=?',(stamp,error,source))
        if not error: db.execute('UPDATE source_runs SET last_success=? WHERE source=?',(stamp,source))
        if upstream_latest: db.execute('UPDATE source_runs SET upstream_latest=?,upstream_checked=? WHERE source=?',(upstream_latest,stamp,source))

def job_status(job_id):
    with connection() as db:
        job=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not job: return None
        tasks=[dict(r) for r in db.execute('SELECT area_id,source,status,error,row_count FROM tasks WHERE job_id=?',(job_id,))]
    return {**dict(job),'total':len(tasks),'completed':sum(t['status'] in ('complete','failed') for t in tasks),
            'failed':sum(t['status']=='failed' for t in tasks),'rows':sum(t['row_count'] for t in tasks),
            'errors':[t for t in tasks if t['status']=='failed'][:20]}
