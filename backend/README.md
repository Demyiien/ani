# Ani collection backend

FastAPI + SQLite + georeferenced raster service for the nationwide Philippine agricultural climate explorer. All 1,642 cities and municipalities in the bundled PSGC 3Q 2026 boundary dataset can be queried and queued.

## Start

From the project root, in two terminals:

```powershell
cd D:\HuaweiInnov2026\backend
python -m pip install -r requirements.txt
Copy-Item .env.example .env  # first run only; do not overwrite an existing .env
python run.py
```

```powershell
cd D:\HuaweiInnov2026\frontend
npm install
npm run dev
```

Dashboard: http://localhost:5173/ — API docs: http://127.0.0.1:8000/docs

Use **Collected data** (default), select a municipality, and click **Collect [place]** for local point series, or click **Sync satellites & advisories** to refresh nationwide CHIRPS, GPM IMERG, Himawari-9, SMAP, MODIS NDVI, Open-Meteo reanalysis grids, DOST-PAGASA advisories, and PSA OpenSTAT provincial crop statistics. The **Data sources** tab also provides **Collect nationwide weather** for sequential per-municipality reanalysis collection.

## Connected Phase 1 sources

| Collector | Data & layers | Authentication | Interpretation |
|---|---|---|---|
| **CHIRPS v3.0 Prelim** (`chirps`) | Daily satellite + station rainfall GeoTIFFs (`0.05°` / ~5.5 km) | No key | Primary historical/baseline rainfall raster and municipal pixel sampler (`~5–7d` publication lag) |
| **NASA GPM IMERG** (`imerg`) | Near-real-time daily precipitation rate via NASA GIBS WMS + ColorMap v1.3 (`0.1°` / ~10 km) | No key | Fills the recent gap after CHIRPS prelim (`~1d` latency); exports Web-Mercator PNG rasters |
| **JMA Himawari-9 AHI Band 13** (`himawari`) | Geostationary clean-IR brightness temperature (`10.4 µm`, ~2 km) converted to cloud-top cover `%` | No key | True satellite cloud imagery raster and pixel sample through the current Manila date |
| **NASA SMAP L3 / L4** (`smap`) | Surface & root-zone volumetric soil moisture (`m³/m³`, 9–36 km) via NASA GIBS | No key | Primary satellite soil moisture raster and municipal pixel sampler (`~2–3d` latency) |
| **MODIS Terra MOD13Q1 & GIBS 8-Day NDVI** (`modis`, `gibs_ndvi`) | 250 m NDVI composites with explicit pixel-reliability / cloud masking | No key | Accepted clear-land pixels (`0–1` NDVI) plus explicit `cloud-masked` status (`value = null`) where clouds obscure the canopy |
| **Open-Meteo Seamless Historical & Forecast Grid** (`open_meteo`, `open_meteo_grid`) | Daily rainfall, 2 m mean temperature, FAO-56 reference evapotranspiration (`ET₀`, `mm/day`), cloud cover, soil moisture | No key for noncommercial use | Seamless `ERA5 + ERA5T + ECMWF IFS` through `today()` in `Asia/Manila`; exports nationwide `temperature` and `evapotranspiration` rasters |
| **NASA POWER Agroclimatology** (`nasa_power`) | `PRECTOTCORR`, `T2M`, and `EVPTRNS` (`MJ/m²/day ÷ 2.45 = mm/day`) | No key | Secondary reanalysis fallback (`0.5° × 0.625°`) |
| **DOST-PAGASA Advisories** (`pagasa`) | 24-hour public weather synopsis, regional hazard forecasts, `Agri-Weather` farm forecast, and `pubfiles` bulletin archive | No key | Structured HTML + bulletin metadata mapped to canonical Philippine provinces |
| **PSA OpenSTAT + DA/PhilRice** (`psa`) | Quarterly & annual Palay and Corn production (`MT`) and area harvested (`ha`) across Philippine provinces (`0012E4EVCP0.px`, `0022E4EAHC0.px`) | No key | Powers provincial yield, irrigated share, seasonal planting/harvest windows, and crop choropleths |

### Source priority & missing/cloud-masked data integrity

- **Rainfall**: `chirps > imerg > open_meteo > open_meteo_grid > nasa_power`
- **Cloud cover**: `himawari > open_meteo > open_meteo_grid`
- **Soil moisture**: `smap > open_meteo > open_meteo_grid`
- **Vegetation (NDVI)**: `modis > gibs_ndvi`
- **Temperature & Evapotranspiration**: `open_meteo > open_meteo_grid > nasa_power`

Every Philippine municipality (`1,642 / 1,642`) is sampled from either its satellite swath or the nationwide `open_meteo_grid` reanalysis/forecast grid so no island or province is left blank during nationwide exploration. No synthetic interpolation or fake fill is ever applied in Collected mode: backend outages or unpublished records display `—`, and cloud-obscured optical NDVI pixels are stored with `status='cloud-masked'` and `value=None`. **Simulated demo** remains strictly isolated.

## Keys and credentials

**No API key is required for any implemented Phase 1 collector.**

Optional:
- `OPEN_METEO_API_KEY` in `backend/.env`: switches Open-Meteo requests to the commercial customer endpoint if you hold a paid subscription.
- `ANI_ADMIN_KEY` in `backend/.env`: requires `X-Admin-Key` on `POST /api/collect` and `POST /api/jobs/{id}/cancel` for non-local deployments.

Later Phase 2+ integrations (direct Copernicus Sentinel-2 10 m scenes and automated Huawei Cloud OBS `obsutil` bucket upload from `data/processed/`) will use their respective credentials when enabled; never store secrets in `VITE_*` variables or source control.

## Storage & georeferenced rasters

- `data/ani.sqlite3`: normalized municipal observations (`status` = `valid` | `cloud-masked` | `no-coverage`), `source_runs`, `advisories`, `farm_stats`, persistent jobs/tasks, and HTTP cache metadata.
- `data/raw/`: compressed upstream response cache.
- `data/processed/rasters/<layer>/<source>_<date>.{png,npz,json}` & `data/processed/catalog.json`: Web-Mercator (`EPSG:3857`) resampled RGBA PNG overlays aligned to `[116.8, 4.5, 126.8, 21.3]` for MapLibre `image` sources, ready for static object-storage sync.

## API

- `GET /api/health`: readiness, 1,642 area count, and current Manila date (`today`).
- `GET /api/sources`: complete data inventory with explicit `coverage_start`, `coverage_end`, raster counts, and canonical `freshness` states (`available` | `stale` | `missing` | `cloud-masked`) per layer and upstream source.
- `GET /api/point?area_id=304903000&layer=rainfall`: stored observations, 7-day timeline ending at latest observation, per-layer freshness inventory, and active local PAGASA advisories.
- `GET /api/map?layer=rainfall&observation_date=2026-10-31`: municipal values plus georeferenced `raster` overlay metadata (`url`, `coordinates`, `bounds`, `observed_min`, `observed_max`).
- `GET /api/rasters/{layer}/{filename}`: serves processed Web-Mercator PNG overlays.
- `GET /api/advisories?province=Nueva%20Ecija`: official DOST-PAGASA public & farm weather advisories plus province severity map.
- `GET /api/crops?province=Nueva%20Ecija`: PSA OpenSTAT provincial palay/corn tonnage, area harvested, yields, quarterly harvest shares, and DA/PhilRice seasonal calendar.
- `POST /api/collect`, `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`.

## Tests

```powershell
cd D:\HuaweiInnov2026\backend
python -m pytest tests -q --basetemp=.testtmp-run
```

Covers valid zeros, partial-day filtering, evapotranspiration (`ET₀` and `EVPTRNS` conversion), MODIS quality & `cloud-masked` tracking, satellite-over-reanalysis source priority, `available`/`stale`/`missing`/`cloud-masked` freshness evaluation, Web-Mercator raster export & serving, PAGASA HTML parsing, PSA PXWeb JSON decoding, queue concurrency, and write security.
