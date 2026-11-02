# Ani: nationwide agricultural climate & satellite explorer

React + TypeScript + Vite + MapLibre GL JS frontend connected to the FastAPI satellite, advisory, and crop statistics service in `../backend`.

## Run

```powershell
cd D:\HuaweiInnov2026\frontend
npm install
npm run dev
```

Start the API in another terminal: `cd D:\HuaweiInnov2026\backend; python run.py`.
See `../backend/README.md` for collector architecture, source priority, raster pipeline, and API details.

`npm run build` validates TypeScript (`tsc -b`) and creates `dist/`. Both `dev` and `preview` proxy `/api` to `http://127.0.0.1:8000` (configurable via `ANI_API_URL`).

## Phase 1 capabilities

1. **Full-screen map + floating HUD**: Edge-to-edge (`100% × 100dvh`) MapLibre workspace modeled on Google Maps and Zoom Earth with floating glass capsules for search, workspace tabs, collapsible `Map layers`, right-edge circular map controls, a closable location inspector card, a bottom-center 7-day timeline dock, and floating overlay sheets for advisories, crop statistics, and data inventory.
2. **Nationwide 1,642-area highlighting at 90% static fill**: Every Philippine city and municipality (`1,642 / 1,642`) renders its live agro-climatic state at a static `90%` fill opacity across both **Raster grid** (Web-Mercator PNG overlays from CHIRPS v3, GPM IMERG, Himawari-9 AHI infrared, NASA SMAP, MODIS 8-day NDVI, and Open-Meteo grids over the 90% municipal land base) and **Municipal fill**.
3. **Six agro-climatic layers**: Rainfall (`mm`), Cloud cover (`%`), Temperature (`°C`), Reference Evapotranspiration `ET₀` (`mm`), Vegetation health (`NDVI`), and Soil moisture (`m³/m³`), plus `Rain − ET₀` moisture balance in location details.
4. **Official DOST-PAGASA advisories**: Floating **PAGASA advisories** sheet and map hazard overlay showing the 24-hour public synopsis, regional weather conditions, `Agri-Weather` farm forecasts (lowland/upland temperatures, relative humidity, leaf wetness), and `pubfiles` PDF bulletins.
5. **Crop calendars & PSA farm statistics**: Floating **Crops & stats** sheet and provincial choropleth (`Palay` / `Corn` tonnage) powered by PSA OpenSTAT quarterly surveys paired with DA/PhilRice wet- and dry-season planting and harvest windows.
6. **Complete data inventory**: Floating **Data sources** sheet auditing both the 6 map layers and all 11 upstream feeds with explicit `coverage_start → coverage_end` ranges, stored raster counts, and canonical freshness badges (`Available`, `Stale`, `Cloud-masked`, `Missing`).

## Collected versus simulated data

**Collected data** is the default across all 1,642 municipalities. Backend failures or unpublished observations display `—`, and cloud-obscured optical NDVI pixels explicitly report `Cloud-masked` without synthetic interpolation or fallback. **Simulated demo** is an opt-in mode clearly labeled in the top floating bar.

## Verify

With both servers running:
- `node verify-geography.mjs` checks all 1,642 PSGC 3Q 2026 municipality polygons, representative-point containment, duplicate-name disambiguation, and ocean rejection.
- `node verify.mjs` runs headless Microsoft Edge via Playwright across laptop/desktop (`1536×740`, `1440×860`) and mobile (`390×844`) viewports, testing full-screen zero-scroll map layout, static 90% fill across all 1,642 areas, Nov 1 data freshness, satellite rasters, evapotranspiration, PAGASA advisories, PSA crop calendars, the 11-feed data inventory, dark/light themes, zero horizontal overflow, and backend failure isolation. Screenshots are saved in `test-results/`.
