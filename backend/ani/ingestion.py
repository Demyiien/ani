import asyncio
import json
import uuid
import httpx
from . import config, db, providers

AREAS = {f['properties']['id']: f['properties'] for f in json.loads(config.BOUNDARIES.read_text(encoding='utf-8-sig'))['features']}
NATIONAL_AREA_ID = '__national__'
DEFAULT_NATIONAL_SOURCES = ['chirps', 'imerg', 'himawari', 'smap', 'gibs_ndvi', 'open_meteo_grid', 'pagasa', 'psa']


def enqueue(ids, sources):
    unique_sources = list(dict.fromkeys(sources))
    unique_ids = list(dict.fromkeys(ids))
    now_ts = db.now()
    task_rows = []
    for source in unique_sources:
        if source in providers.NATIONAL_ADAPTERS:
            task_rows.append((NATIONAL_AREA_ID, source))
        else:
            for area_id in unique_ids:
                task_rows.append((area_id, source))

    with db.connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        active = conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()
        if active:
            return active['id'], False
        job_id = uuid.uuid4().hex
        conn.execute('INSERT INTO jobs VALUES (?,?,?,NULL)', (job_id, 'queued', now_ts))
        conn.executemany(
            'INSERT INTO tasks (job_id,area_id,source,status,updated_at) VALUES (?,?,?,?,?)',
            [(job_id, area_id, source, 'queued', now_ts) for area_id, source in task_rows],
        )
    return job_id, True


def cancel(job_id):
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='cancelled',updated_at=? WHERE job_id=? AND status='queued'", (db.now(), job_id))
        conn.execute("UPDATE jobs SET status='cancelled',finished_at=? WHERE id=? AND status IN ('running','queued')", (db.now(), job_id))


async def run_task_once(client, task):
    source = task['source']
    if source in providers.NATIONAL_ADAPTERS:
        count = await providers.NATIONAL_ADAPTERS[source](client, AREAS)
        if not count:
            raise RuntimeError('No valid records returned from upstream national feed.')
        return count

    rows = await providers.ADAPTERS[source](client, AREAS[task['area_id']])
    if not rows:
        raise RuntimeError('No valid samples returned. Data may be delayed, missing, or quality-masked.')
    return db.save_rows(AREAS[task['area_id']], rows)


async def worker():
    headers = {'User-Agent': 'AniResearchPrototype/0.2', 'Accept': 'application/json, text/html, */*'}
    async with httpx.AsyncClient(timeout=90, verify=providers.tls_context(), headers=headers, follow_redirects=True) as client:
        while True:
            with db.connection() as conn:
                task = conn.execute(
                    "SELECT t.* FROM tasks t JOIN jobs j ON j.id=t.job_id "
                    "WHERE t.status='queued' AND j.status IN ('queued','running') ORDER BY t.id LIMIT 1"
                ).fetchone()
                if task:
                    conn.execute("UPDATE tasks SET status='running',updated_at=? WHERE id=?", (db.now(), task['id']))
                    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (task['job_id'],))
            if not task:
                await asyncio.sleep(0.8)
                continue
            error = None
            count = 0
            try:
                count = await run_task_once(client, task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not leak provider request URLs (which may contain credentials).
                error = str(exc) if isinstance(exc, RuntimeError) else f'{type(exc).__name__}: collection failed; check source availability.'
                db.record_run(task['source'], error=error)
            with db.connection() as conn:
                conn.execute(
                    'UPDATE tasks SET status=?,error=?,row_count=?,updated_at=? WHERE id=?',
                    ('failed' if error else 'complete', error, count, db.now(), task['id']),
                )
                pending = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE job_id=? AND status IN ('queued','running')",
                    (task['job_id'],),
                ).fetchone()[0]
                if not pending:
                    failures = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE job_id=? AND status='failed'",
                        (task['job_id'],),
                    ).fetchone()[0]
                    conn.execute(
                        "UPDATE jobs SET status=?,finished_at=? WHERE id=? AND status!='cancelled'",
                        ('partial' if failures else 'complete', db.now(), task['job_id']),
                    )
            await asyncio.sleep(0.05)


async def scheduler():
    if not config.REFRESH_HOURS:
        return
    while True:
        await asyncio.sleep(config.REFRESH_HOURS * 3600)
        with db.connection() as conn:
            ids = [r[0] for r in conn.execute("SELECT DISTINCT area_id FROM observations WHERE source='open_meteo'")]
        enqueue(ids[:50], DEFAULT_NATIONAL_SOURCES + (['open_meteo', 'modis'] if ids else []))
