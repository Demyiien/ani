"""PAGASA/DOST official advisories parser and PSA OpenSTAT / DA-PhilRice crop calendar & farm statistics adapter."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
import html
import json
import math
import re
from . import db

PAGASA_URLS = {
    'daily': 'https://www.pagasa.dost.gov.ph/weather',
    'agri': 'https://www.pagasa.dost.gov.ph/agri-weather',
    'advisory': 'https://www.pagasa.dost.gov.ph/weather/weather-advisory',
    'pubfiles': 'https://pubfiles.pagasa.dost.gov.ph/tamss/weather/',
}

PSA_TABLES = {
    'production': {
        'url': 'https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB/2E/CS/0012E4EVCP0.px',
        'metric': 'production',
        'unit': 'MT',
    },
    'area_harvested': {
        'url': 'https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB/2E/CS/0022E4EAHC0.px',
        'metric': 'area_harvested',
        'unit': 'ha',
    },
}

PSA_PROVINCE_ALIASES = {
    'cotabato (north cotabato)': 'Cotabato',
    'davao de oro (compostela valley)': 'Davao de Oro',
    'tawi-tawi': 'Tawi-Tawi',
    'philippines': 'PHILIPPINES',
}

ISLAND_GROUP_REGIONS = {
    'luzon': {'CAR', 'Region I', 'Region II', 'Region III', 'Region IV-A', 'MIMAROPA', 'Region V', 'NCR'},
    'visayas': {'Region VI', 'Region VII', 'Region VIII', 'Negros Island'},
    'mindanao': {'Region IX', 'Region X', 'Region XI', 'Region XII', 'Region XIII', 'BARMM'},
}

REGION_KEYWORDS = {
    'ilocos region': 'Region I',
    'cordillera administrative region': 'Cordillera',
    'cagayan valley': 'Region II',
    'central luzon': 'Region III',
    'calabarzon': 'Region IV-A',
    'mimaropa': 'MIMAROPA',
    'bicol region': 'Region V',
    'western visayas': 'Region VI',
    'central visayas': 'Region VII',
    'eastern visayas': 'Region VIII',
    'zamboanga peninsula': 'Region IX',
    'northern mindanao': 'Region X',
    'davao region': 'Region XI',
    'soccsksargen': 'Region XII',
    'caraga': 'Region XIII',
    'barmm': 'Bangsamoro',
    'bangsamoro': 'Bangsamoro',
    'metro manila': 'National Capital Region',
    'national capital region': 'National Capital Region',
}


def _clean_html_text(fragment: str) -> str:
    no_scripts = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', fragment, flags=re.S | re.I)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', no_scripts))
    text = text.replace('\ufffd', '–')
    return re.sub(r'\s+', ' ', text).strip()


def _extract_tables(page_html: str) -> list[list[list[str]]]:
    tables = []
    for tbl in re.findall(r'<table[^>]*>(.*?)</table>', page_html, flags=re.S | re.I):
        rows = []
        for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, flags=re.S | re.I):
            cells = [_clean_html_text(td) for td in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, flags=re.S | re.I)]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def _parse_pagasa_issued(text: str) -> str | None:
    patterns = [
        r'Issued at:\s*([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)\s*,?\s*(?:[A-Za-z]+,\s*)?[A-Za-z]+\s+[0-9]{1,2},\s*[0-9]{4})',
        r'Issued at:\s*([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)\s*,?\s*[0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})',
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            raw = re.sub(r'\s+', ' ', m.group(1)).strip()
            cleaned = re.sub(r',\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),', ',', raw, flags=re.I)
            cleaned = re.sub(r'\b(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*', '', cleaned, flags=re.I)
            for fmt in ('%I:%M %p, %d %B %Y', '%I:%M %p, %B %d, %Y', '%I:%M %p %B %d, %Y', '%I:%M %p %d %B %Y'):
                try:
                    dt = datetime.strptime(cleaned.replace('  ', ' ').strip(), fmt)
                    return dt.strftime('%Y-%m-%dT%H:%M:00+08:00')
                except ValueError:
                    continue
            return raw
    return None


def match_area_phrase_to_provinces(phrase: str, areas: dict, already_claimed: set[str] | None = None) -> list[str]:
    """Map a PAGASA regional/provincial forecast phrase to canonical boundary province names."""
    all_provinces = sorted({a['province'] for a in areas.values()})
    province_to_region = {a['province']: a['region'] for a in areas.values()}
    low = phrase.lower()
    claimed = already_claimed or set()
    matched: set[str] = set()

    for prov in all_provinces:
        clean_prov = re.sub(r'\s*\(.*?\)', '', prov).lower()
        if clean_prov and len(clean_prov) >= 4 and clean_prov in low:
            matched.add(prov)

    for kw, region_sub in REGION_KEYWORDS.items():
        if kw in low:
            pat = re.compile(rf'\b{re.escape(region_sub)}\b', flags=re.I)
            for prov, reg in province_to_region.items():
                if pat.search(reg):
                    matched.add(prov)

    def _in_island(reg: str, group: str) -> bool:
        if group == 'visayas':
            return any(x in reg for x in ('Region VI ', 'Region VII ', 'Region VIII', 'Negros'))
        if group == 'mindanao':
            return any(x in reg for x in ('Region IX', 'Region X ', 'Region XI', 'Region XII', 'Region XIII', 'Bangsamoro'))
        return not (_in_island(reg, 'visayas') or _in_island(reg, 'mindanao'))

    if 'visayas' in low and not any(k in low for k in ('western visayas', 'central visayas', 'eastern visayas')):
        matched.update(p for p, r in province_to_region.items() if _in_island(r, 'visayas'))
    if 'mindanao' in low and not any(k in low for k in ('northern mindanao',)):
        matched.update(p for p, r in province_to_region.items() if _in_island(r, 'mindanao'))
    if 'rest of luzon' in low or ('luzon' in low and 'northern luzon' not in low and 'central luzon' not in low and 'southern luzon' not in low):
        luzon_all = {p for p, r in province_to_region.items() if _in_island(r, 'luzon')}
        matched.update(luzon_all - claimed if 'rest of luzon' in low else luzon_all)
    if 'northern luzon' in low:
        matched.update(p for p, r in province_to_region.items() if any(x in r for x in ('Region I ', 'Region II', 'Cordillera')))
    if 'rest of the country' in low or 'whole country' in low or 'archipelago' in low:
        matched.update(set(all_provinces) - claimed)

    return sorted(matched)


def parse_pagasa_pages(daily_html: str, agri_html: str, adv_html: str, pubfiles_html: str, areas: dict) -> list[dict]:
    """Parse PAGASA public forecast, farm weather forecast, weather advisory status, and official PDF bulletins."""
    records: list[dict] = []
    all_provinces = sorted({a['province'] for a in areas.values()})

    # 1. Daily Weather Forecast (/weather)
    daily_text = _clean_html_text(daily_html)
    daily_issued = _parse_pagasa_issued(daily_text) or datetime.now(timezone.utc).isoformat()
    syn_m = re.search(r'Synopsis\s+(.*?)(?:TC Information|TROPICAL CYCLONE|Forecast Weather Conditions)', daily_text, flags=re.I)
    synopsis = syn_m.group(1).strip() if syn_m else 'Official PAGASA 24-hour public weather synopsis.'
    tc_m = re.search(r'(TROPICAL CYCLONE (?:OUTSIDE|INSIDE) PAR.*?MOVEMENT:\s*[^<]+?KM/H)', daily_text, flags=re.I)
    tc_note = f' {tc_m.group(1).strip()}.' if tc_m else ''

    records.append({
        'id': 'pagasa-synopsis',
        'product': 'Daily Weather Synopsis',
        'title': 'PAGASA 24-Hour Public Weather Synopsis',
        'issued_at': daily_issued,
        'url': PAGASA_URLS['daily'],
        'provinces': all_provinces,
        'excerpt': f'{synopsis}{tc_note}'.strip(),
        'text_status': 'active',
    })

    claimed_daily: set[str] = set()
    for tbl in _extract_tables(daily_html):
        header = [c.lower() for c in tbl[0]]
        if len(header) >= 4 and 'place' in header[0] and 'weather condition' in header[1]:
            for idx, row in enumerate(tbl[1:], start=1):
                if len(row) < 4:
                    continue
                place_txt, cond, cause, impact = row[0], row[1], row[2], row[3]
                provs = match_area_phrase_to_provinces(place_txt, areas, claimed_daily)
                claimed_daily.update(provs)
                records.append({
                    'id': f'pagasa-daily-{idx}',
                    'product': 'Regional Weather Forecast',
                    'title': f'{place_txt} — {cause}',
                    'issued_at': daily_issued,
                    'url': PAGASA_URLS['daily'],
                    'provinces': provs,
                    'excerpt': f'{cond}. Cause: {cause}. Potential impacts: {impact}.',
                    'text_status': 'warning' if any(w in (cond + ' ' + impact).lower() for w in ('heavy', 'flash flood', 'landslide', 'gale', 'storm')) else 'advisory',
                })

    # 2. Farm Weather Forecast (/agri-weather)
    agri_text = _clean_html_text(agri_html)
    agri_issued = _parse_pagasa_issued(agri_text) or daily_issued
    agri_syn_m = re.search(r'SYNOPSIS\s+(.*?)(?:TROPICAL CYCLONE|Forecast Area)', agri_text, flags=re.I)
    if agri_syn_m:
        records.append({
            'id': 'pagasa-agri-synopsis',
            'product': 'Farm Weather Synopsis',
            'title': 'PAGASA Agro-Meteorological Synopsis',
            'issued_at': agri_issued,
            'url': PAGASA_URLS['agri'],
            'provinces': all_provinces,
            'excerpt': agri_syn_m.group(1).strip(),
            'text_status': 'active',
        })

    claimed_agri: set[str] = set()
    for tbl in _extract_tables(agri_html):
        header_join = ' '.join(tbl[0]).lower()
        if 'forecast area' in header_join and 'agri-weather' in header_join:
            data_rows = [r for r in tbl[1:] if len(r) >= 6 and 'lowland' not in r[0].lower()]
            for idx, row in enumerate(data_rows, start=1):
                area_txt, sky, winds = row[0], row[1], row[2]
                t_low, t_up = row[3], row[4]
                rh = row[5]
                leaf = row[6] if len(row) > 6 else '—'
                provs = match_area_phrase_to_provinces(area_txt, areas, claimed_agri)
                claimed_agri.update(provs)
                records.append({
                    'id': f'pagasa-agri-{idx}',
                    'product': 'Farm Weather Forecast',
                    'title': f'Farm Weather: {area_txt}',
                    'issued_at': agri_issued,
                    'url': PAGASA_URLS['agri'],
                    'provinces': provs,
                    'excerpt': (f'{sky}. Winds: {winds} Lowland temp: {t_low} °C · Upland: {t_up} °C · '
                                f'RH: {rh}% · Leaf wetness: {leaf} hrs.'),
                    'text_status': 'warning' if 'scattered rains' in sky.lower() or 'heavy' in sky.lower() else 'advisory',
                })

    # 3. Active Weather Advisory check (/weather/weather-advisory + pubfiles PDF archive)
    adv_text = _clean_html_text(adv_html)
    no_adv = 'no weather advisory issued' in adv_text.lower()
    records.append({
        'id': 'pagasa-wx-advisory-status',
        'product': 'Weather Advisory Bulletin',
        'title': 'No Active Heavy Rainfall Advisory' if no_adv else 'Active PAGASA Weather Advisory',
        'issued_at': daily_issued,
        'url': PAGASA_URLS['advisory'],
        'provinces': [] if no_adv else all_provinces,
        'excerpt': 'PAGASA reports no special heavy-rainfall Weather Advisory in effect at this check.' if no_adv
                   else adv_text[-500:],
        'text_status': 'clear' if no_adv else 'warning',
    })

    pdf_targets = {
        'pf.pdf': 'Official Public Weather Forecast PDF',
        'outlook.pdf': 'PAGASA Weekly Weather Outlook PDF',
        'gale.pdf': 'PAGASA Marine Gale Warning PDF',
        'advisory.pdf': 'Latest Archived Weather Advisory PDF',
    }
    for m in re.finditer(r'href="([^"?]+\.pdf)"[^>]*>[^<]*</a>\s*([0-9]{2}-[A-Za-z]{3}-[0-9]{4}\s+[0-9:]{5})', pubfiles_html, flags=re.I):
        fname, stamp = m.group(1), m.group(2)
        if fname in pdf_targets:
            try:
                dt = datetime.strptime(stamp.strip(), '%d-%b-%Y %H:%M').replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                dt = stamp
            records.append({
                'id': f'pagasa-pdf-{fname.replace(".", "-")}',
                'product': 'DOST-PAGASA Official Archive',
                'title': pdf_targets[fname],
                'issued_at': dt,
                'url': PAGASA_URLS['pubfiles'] + fname,
                'provinces': all_provinces,
                'excerpt': f'Official signed DOST-PAGASA bulletin file ({fname}) published on pubfiles.pagasa.dost.gov.ph.',
                'text_status': 'archive',
            })

    return records


async def collect_pagasa(client, areas: dict) -> int:
    """Fetch PAGASA HTML advisories and pubfiles metadata and store normalized records."""
    res_daily = await client.get(PAGASA_URLS['daily'])
    res_agri = await client.get(PAGASA_URLS['agri'])
    res_adv = await client.get(PAGASA_URLS['advisory'])
    res_pub = await client.get(PAGASA_URLS['pubfiles'])
    for r in (res_daily, res_agri):
        if r.status_code >= 400:
            raise RuntimeError(f'PAGASA returned HTTP {r.status_code}.')

    records = parse_pagasa_pages(
        res_daily.text,
        res_agri.text,
        res_adv.text if res_adv.status_code < 400 else '',
        res_pub.text if res_pub.status_code < 400 else '',
        areas,
    )
    fetched = db.now()
    with db.connection() as conn:
        conn.execute('DELETE FROM advisories')
        conn.executemany(
            'INSERT OR REPLACE INTO advisories VALUES (?,?,?,?,?,?,?,?,?)',
            [(r['id'], r['product'], r['title'], r['issued_at'], r['url'],
              json.dumps(r['provinces']), r['excerpt'], r['text_status'], fetched) for r in records]
        )
    latest_issued = max((r['issued_at'][:10] for r in records if r['issued_at'] and r['issued_at'][:4].isdigit()), default=None)
    db.record_run('pagasa', upstream_latest=latest_issued)
    return len(records)


def normalize_psa_province(raw_label: str, valid_provinces: set[str]) -> str | None:
    """Normalize PSA OpenSTAT geolocation strings (dots + footnote markers) to boundary province names."""
    stripped = re.sub(r'^\.+', '', raw_label).strip()
    stripped = re.sub(r'\s+[a-z]/$', '', stripped).strip()
    low = stripped.lower()
    if low in PSA_PROVINCE_ALIASES:
        return PSA_PROVINCE_ALIASES[low]
    if stripped in valid_provinces:
        return stripped
    for vp in valid_provinces:
        if vp.lower() == low:
            return vp
    return None


def parse_pxweb_json(payload: dict, meta: dict, metric: str, unit: str, valid_provinces: set[str]) -> list[dict]:
    """Decode PXWeb native JSON {'data': [{'key': [...], 'values': [...]}]} using variable code->text maps."""
    var_maps: list[dict[str, str]] = []
    for v in meta.get('variables', []):
        var_maps.append(dict(zip(v.get('values', []), v.get('valueTexts', []))))
    if len(var_maps) != 4:
        return []

    crop_map, geo_map, year_map, period_map = var_maps
    rows = []
    for item in payload.get('data', []):
        keys = item.get('key', [])
        vals = item.get('values', [])
        if len(keys) != 4 or not vals:
            continue
        raw_val = vals[0]
        if raw_val in ('..', '.', '-', '', None):
            continue
        try:
            num = float(raw_val)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(num) or num < 0:
            continue

        crop_name = crop_map.get(keys[0], keys[0]).strip()
        prov = normalize_psa_province(geo_map.get(keys[1], ''), valid_provinces)
        yr_str = year_map.get(keys[2], '')
        period_name = period_map.get(keys[3], keys[3]).strip()
        if not prov or not yr_str.isdigit():
            continue
        rows.append({
            'province': prov,
            'crop': crop_name,
            'metric': metric,
            'year': int(yr_str),
            'period': period_name,
            'value': round(num, 2),
            'unit': unit,
            'source': 'psa_openstat',
        })
    return rows


async def collect_psa(client, areas: dict) -> int:
    """Query PSA OpenSTAT quarterly & annual Palay and Corn production and harvested area by province."""
    valid_provinces = {a['province'] for a in areas.values()}
    all_rows: list[dict] = []
    latest_year = None

    for spec in PSA_TABLES.values():
        meta_resp = await client.get(spec['url'])
        if meta_resp.status_code >= 400:
            raise RuntimeError(f'PSA OpenSTAT metadata returned HTTP {meta_resp.status_code}.')
        meta = meta_resp.json()
        yr_var = next(v for v in meta['variables'] if v['code'] == 'Year')
        recent_yr_codes = yr_var['values'][-3:]
        recent_yr_texts = [int(t) for t in yr_var['valueTexts'][-3:] if t.isdigit()]
        if recent_yr_texts:
            latest_year = max(recent_yr_texts)

        query_body = {
            'query': [
                {'code': 'Ecosystem/Croptype', 'selection': {'filter': 'item', 'values': ['0', '1', '2', '5']}},
                {'code': 'Geolocation', 'selection': {'filter': 'all', 'values': ["*"]}},
                {'code': 'Year', 'selection': {'filter': 'item', 'values': recent_yr_codes}},
                {'code': 'Period', 'selection': {'filter': 'item', 'values': ['0', '1', '3', '4', '6']}},
            ],
            'response': {'format': 'json'},
        }
        data_resp = await client.post(spec['url'], json=query_body)
        if data_resp.status_code >= 400:
            raise RuntimeError(f'PSA OpenSTAT query returned HTTP {data_resp.status_code}.')
        all_rows.extend(parse_pxweb_json(data_resp.json(), meta, spec['metric'], spec['unit'], valid_provinces))

    if not all_rows:
        raise RuntimeError('PSA OpenSTAT returned no valid provincial crop statistics.')

    fetched = db.now()
    with db.connection() as conn:
        conn.execute('DELETE FROM farm_stats')
        conn.executemany(
            '''INSERT INTO farm_stats VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(province,crop,metric,year,period) DO UPDATE SET
               value=excluded.value,fetched_at=excluded.fetched_at''',
            [(r['province'], r['crop'], r['metric'], r['year'], r['period'],
              r['value'], r['unit'], r['source'], fetched) for r in all_rows]
        )
    db.record_run('psa', upstream_latest=f'{latest_year}-Q3' if latest_year else None)
    return len(all_rows)


def build_crop_profiles(areas: dict, current_month: int = 11) -> dict:
    """Build provincial farm statistics + DA/PhilRice seasonal planting & harvest calendars from stored PSA data."""
    all_provinces = sorted({a['province'] for a in areas.values()})
    province_regions = {a['province']: a['region'] for a in areas.values()}

    with db.connection() as conn:
        raw_rows = [dict(r) for r in conn.execute('SELECT * FROM farm_stats ORDER BY year, period')]

    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in raw_rows:
        grouped[r['province']].append(r)

    def _latest_complete_annual(rows: list[dict], crop: str, metric: str) -> tuple[int | None, float | None]:
        ann = [r for r in rows if r['crop'] == crop and r['metric'] == metric and r['period'] == 'Annual']
        if ann:
            best = max(ann, key=lambda x: x['year'])
            return best['year'], best['value']
        return None, None

    def _quarterly_series(rows: list[dict], crop: str, metric: str) -> tuple[int | None, dict[str, float]]:
        q_rows = [r for r in rows if r['crop'] == crop and r['metric'] == metric and r['period'].startswith('Quarter')]
        if not q_rows:
            return None, {}
        by_year: dict[int, dict[str, float]] = defaultdict(dict)
        for r in q_rows:
            by_year[r['year']][r['period']] = r['value']
        # Prefer the most recent year with all 4 quarters for seasonal calendar share; also expose latest 2026 YTD quarters.
        full_years = [y for y, qs in by_year.items() if len(qs) == 4]
        ref_year = max(full_years) if full_years else max(by_year)
        return ref_year, by_year[ref_year]

    def _ytd_quarters(rows: list[dict], crop: str, metric: str, year: int = 2026) -> dict[str, float]:
        return {r['period']: r['value'] for r in rows
                if r['crop'] == crop and r['metric'] == metric and r['year'] == year and r['period'].startswith('Quarter')}

    profiles = {}
    national_rows = grouped.get('PHILIPPINES', [])
    _, nat_palay = _latest_complete_annual(national_rows, 'Palay', 'production')
    _, nat_corn = _latest_complete_annual(national_rows, 'Corn', 'production')

    for prov in all_provinces:
        rows = grouped.get(prov, [])
        # BARMM split provinces fallback note if PSA aggregates under parent or regional entry
        note = None
        if not rows and 'NCR' in prov:
            note = 'Urban metropolitan district (NCR); commercial palay and corn area is negligible in PSA surveys.'
        elif not rows and prov == 'City of Isabela (Not a Province)':
            rows = grouped.get('Basilan', [])
            if rows:
                note = 'PSA reports Basilan Island statistics at the provincial level.'
        elif not rows and prov == 'Special Geographic Area':
            rows = grouped.get('Cotabato', [])
            if rows:
                note = 'Shown with Cotabato basin statistics; BARMM SGA is aggregated in PSA provincial series.'

        palay_yr, palay_prod = _latest_complete_annual(rows, 'Palay', 'production')
        _, palay_area = _latest_complete_annual(rows, 'Palay', 'area_harvested')
        _, irr_prod = _latest_complete_annual(rows, 'Irrigated Palay', 'production')
        _, rain_prod = _latest_complete_annual(rows, 'Rainfed Palay', 'production')
        corn_yr, corn_prod = _latest_complete_annual(rows, 'Corn', 'production')
        _, corn_area = _latest_complete_annual(rows, 'Corn', 'area_harvested')

        cal_year, palay_q = _quarterly_series(rows, 'Palay', 'production')
        _, corn_q = _quarterly_series(rows, 'Corn', 'production')
        ytd_2026 = _ytd_quarters(rows, 'Palay', 'production', 2026)

        q_total = sum(palay_q.values())
        shares = {f'Q{i}': round(palay_q.get(f'Quarter {i}', 0.0) / q_total * 100.0, 1) if q_total > 0 else 0.0 for i in range(1, 5)}
        peak_q = max(shares, key=shares.get) if q_total > 0 else None
        wet_share = round(shares.get('Q3', 0.0) + shares.get('Q4', 0.0), 1) if q_total > 0 else None
        dry_share = round(shares.get('Q1', 0.0) + shares.get('Q2', 0.0), 1) if q_total > 0 else None
        irr_share = round((irr_prod / palay_prod) * 100.0, 1) if (irr_prod is not None and palay_prod and palay_prod > 0) else None
        palay_yield = round(palay_prod / palay_area, 2) if (palay_prod and palay_area and palay_area > 0) else None
        corn_yield = round(corn_prod / corn_area, 2) if (corn_prod and corn_area and corn_area > 0) else None

        # Derive DA / PhilRice cropping calendar tailored to the province's observed PSA quarterly harvest regime.
        if not rows or (not palay_prod and not corn_prod):
            nov_stage = 'Non-major grain area' if 'NCR' in prov else 'No PSA provincial record collected'
            wet_window = {'planting': 'May – Jul', 'harvest': 'Oct – Nov'}
            dry_window = {'planting': 'Nov – Jan', 'harvest': 'Mar – May'}
        elif shares.get('Q4', 0) >= shares.get('Q3', 0) and shares.get('Q4', 0) >= 28:
            wet_tag = 'Peak Q4' if peak_q == 'Q4' else 'Main wet Q4'
            dry_tag = 'Peak Q2' if peak_q == 'Q2' else ('Peak Q1' if peak_q == 'Q1' else 'Q1–Q2')
            wet_window = {'planting': 'Jun – Jul', 'harvest': f'Oct – Dec ({wet_tag})'}
            dry_window = {'planting': 'Dec – Jan', 'harvest': f'Mar – May ({dry_tag})'}
            nov_stage = 'Wet-season palay harvest & dry-season seedbed/land prep'
        elif shares.get('Q3', 0) >= 30:
            wet_window = {'planting': 'May – Jun', 'harvest': 'Aug – Oct (Peak Q3)'}
            dry_window = {'planting': 'Nov – Dec', 'harvest': 'Feb – Apr (Q1–Q2)'}
            nov_stage = 'Dry-season transplanting & early vegetative growth'
        else:
            wet_window = {'planting': 'May – Jul', 'harvest': 'Sep – Nov'}
            dry_window = {'planting': 'Nov – Jan', 'harvest': 'Mar – May'}
            nov_stage = 'Late wet-season harvest & dry-season planting transition'

        dominant_crop = 'Palay' if (palay_prod or 0) >= (corn_prod or 0) else 'Corn'
        if not palay_prod and not corn_prod:
            dominant_crop = 'Urban / Non-grain'

        profiles[prov] = {
            'province': prov,
            'region': province_regions.get(prov, ''),
            'reference_year': palay_yr or corn_yr or cal_year,
            'dominant_crop': dominant_crop,
            'palay_production_mt': palay_prod,
            'palay_area_ha': palay_area,
            'palay_yield_mt_ha': palay_yield,
            'irrigated_palay_mt': irr_prod,
            'rainfed_palay_mt': rain_prod,
            'irrigated_share_pct': irr_share,
            'corn_production_mt': corn_prod,
            'corn_area_ha': corn_area,
            'corn_yield_mt_ha': corn_yield,
            'quarterly_palay_shares_pct': shares,
            'quarterly_palay_mt': {f'Q{i}': palay_q.get(f'Quarter {i}') for i in range(1, 5)},
            'quarterly_corn_mt': {f'Q{i}': corn_q.get(f'Quarter {i}') for i in range(1, 5)},
            'ytd_2026_palay_mt': {k.replace('Quarter ', 'Q'): v for k, v in ytd_2026.items()},
            'peak_harvest_quarter': peak_q,
            'wet_season_share_pct': wet_share,
            'dry_season_share_pct': dry_share,
            'calendar': {
                'wet_season': wet_window,
                'dry_season': dry_window,
                'current_month': current_month,
                'current_stage': nov_stage,
                'quarters': [
                    {'quarter': 'Q1 (Jan–Mar)', 'stage': 'Dry-season vegetative/reproductive & early harvest', 'palay_share_pct': shares['Q1']},
                    {'quarter': 'Q2 (Apr–Jun)', 'stage': 'Main dry-season harvest & wet-season land prep/planting', 'palay_share_pct': shares['Q2']},
                    {'quarter': 'Q3 (Jul–Sep)', 'stage': 'Wet-season crop growth & early harvest', 'palay_share_pct': shares['Q3']},
                    {'quarter': 'Q4 (Oct–Dec)', 'stage': 'Main wet-season harvest & dry-season establishment', 'palay_share_pct': shares['Q4']},
                ],
            },
            'note': note,
        }

    return {
        'collected': bool(raw_rows),
        'national': {
            'palay_production_mt': nat_palay,
            'corn_production_mt': nat_corn,
        },
        'provinces': profiles,
        'source_citation': 'PSA OpenSTAT Quarterly Palay & Corn Survey (0012E4EVCP0 / 0022E4EAHC0) + DA/PhilRice seasonal calendar',
    }
