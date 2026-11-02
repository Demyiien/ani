# Climate and Crop Risk Forecasting Platform
### Huawei Innovation Track: Tech Stack and Build Plan

> **Assumption:** From your chat notes, the project is a satellite and climate-driven forecasting system for Philippine agriculture (rainfall, drought, and crop stress), served through a map dashboard and delivered to farmers as alerts through chat bots. If the actual scope differs, the stack below still holds. Only the model targets and map layers change.

---

## 1. System Overview

```
   FREE DATA SOURCES                HUAWEI CLOUD                         USERS
 ┌──────────────────┐        ┌─────────────────────────┐        ┌──────────────────┐
 │ Himawari (AHI)   │        │  Ingestion jobs         │        │  Web map         │
 │ CHIRPS / IMERG   │  ───►  │  (Python, satpy, etc.)  │        │  dashboard       │
 │ ERA5 / NASA POWER│        │          │              │        │                  │
 │ MODIS / Sentinel │        │          ▼              │        │  Telegram bot    │
 │ SMAP             │        │   OBS (raw + processed  │        │                  │
 │ PAGASA / PSA /DA │        │   datasets, checkpoints)│  ───►  │  FB Messenger    │
 └──────────────────┘        │          │              │        │  bot             │
                             │          ▼              │        └──────────────────┘
                             │  ModelArts + MindSpore  │                 ▲
                             │  (train, version)       │                 │
                             │          │              │                 │
                             │          ▼              │                 │
                             │  Backend API            │ ────────────────┘
                             │  (ECS / FunctionGraph)  │
                             └─────────────────────────┘
```

**Data flow in one line:** free satellite and climate data → ingestion → OBS → feature dataset → MindSpore model on ModelArts → backend API → map and bot alerts.

---

## 2. Technology Stack

### 2.1 Huawei Cloud (required for the competition)

| Service | Role in the project | Notes |
|---|---|---|
| **MindSpore** | Core framework for the forecasting model (LSTM, ConvLSTM, or Transformer) | Start with a simple baseline, then move to ConvLSTM for gridded rainfall. See Section 4. |
| **ModelArts** | Training jobs, experiment tracking, model registry and versioning, optional inference deployment | Check free trial quota before relying on GPU or NPU hours. Develop locally on small data first. |
| **OBS (Object Storage Service)** | Stores raw downloads, processed datasets, model checkpoints, and a cache of map-ready layers | Cheap at our data volume. Keep the area of interest (AOI) small to control storage. |
| **ECS** or **FunctionGraph** | Hosts the backend API and scheduled jobs | FunctionGraph (serverless) suits low traffic and scheduled ingestion. ECS is simpler if we need a long-running service. |

### 2.2 Satellite and Climate Data (all free)

| Source | What we use it for | Resolution and latency (approx.) | Access |
|---|---|---|---|
| **Himawari (AHI)** | Cloud, convection, and storm tracking. Near-realtime input for short-term forecasts | ~2 km, 10-minute full disk | Public data archives. Use a Himawari download helper plus **satpy** to read. Files are large, so crop to the AOI immediately. |
| **satpy** | Reads and processes AHI files (reprojection, band composites, cropping) | n/a (tool) | Python library, free |
| **CHIRPS** | Daily rainfall estimate, good for history and baselines | ~5 km, daily, short delay on the preliminary product | Open download, no login |
| **GPM IMERG** (NASA Earthdata) | Near-realtime rainfall | ~10 km, 30-minute. Early run is a few hours' latency, Final run is months | Free NASA Earthdata account |
| **ERA5** | Temperature and evapotranspiration inputs | ~25 km, hourly, a few days' latency | Free Copernicus CDS account and API key |
| **NASA POWER** | Solar radiation and temperature | Coarse grid, daily | Open API, no key needed |
| **MODIS and Sentinel-2** | NDVI and vegetation stress | MODIS: 250 m, 16-day composites. Sentinel-2: 10 m, ~5-day revisit | MODIS via Earthdata. Sentinel-2 via Copernicus Data Space. Cloud cover is a major issue in PH, so expect gaps. |
| **SMAP** | Soil moisture | ~9 to 36 km, 2 to 3 day revisit | Earthdata account |
| **PAGASA and DOST advisories** | Official outlooks used as ground truth and as a comparison layer | n/a | No clean public API. Likely manual entry or careful scraping. Confirm terms of use. |
| **PSA, DA, PhilRice** | Crop calendars and farm statistics (what is planted where and when) | Provincial or municipal tables | Mostly PDFs and tables. Plan on **manual curation** into a CSV or JSON. |

**Accounts to create now (free):** NASA Earthdata, Copernicus CDS, Copernicus Data Space, Huawei Cloud, Telegram (BotFather), Meta for Developers.

### 2.3 Alerts

| Channel | Cost | Notes |
|---|---|---|
| **Telegram bot** | Free | Primary channel. Fast to build, no review process. |
| **Facebook Messenger bot** | Free API | Needs a Facebook Page and a Meta app. Full public use needs **app review**. In development mode only admins and testers can message the bot, which is enough for a competition demo. Messenger also limits when a bot may message users outside a recent conversation, so design alerts as replies or opt-in subscriptions. |
| **SMS** | Not used (cost) | Revisit later through a partner or telco sponsorship. Many rural farmers may reach us more easily through Messenger than Telegram, so Messenger matters for real adoption. |

### 2.4 Supporting Tools (suggested)

| Tool | Purpose |
|---|---|
| Python 3.10+, `xarray`, `rasterio`, `numpy`, `pandas`, `geopandas` | Data processing |
| `earthaccess`, `cdsapi` | Earthdata and ERA5 downloads |
| FastAPI | Backend API |
| MapLibre GL JS | Web map (free, open source, no API key) |
| OpenStreetMap or Carto basemap tiles | Free basemap |
| GitHub | Repo and task tracking |

---

## 3. Build Phases

Each phase ends with a **deliverable** you can demo. Phase 1 is the initial build you requested.

### Phase 0: Initialization and Scoping

**Goal:** Lock the problem so we don't download the planet.

- [ ] Define the **area of interest** (one province or region to start).
- [ ] Pick **one or two crops** (e.g. rice, corn) and one core question (e.g. *"Will rainfall be enough in the next 7 to 14 days for planting?"* or *"Is drought stress building?"*).
- [ ] Create all accounts listed in 2.2 and test one download from each.
- [ ] Set up the repo, branch rules, and folder structure (see 3.1).
- [ ] Set up Huawei Cloud: IAM users, an OBS bucket, and confirm ModelArts access and quota.

**Deliverable:** A one-page scope and a repo where everyone can run a "hello world" data download.

---

### Phase 1: Initial Build: The Data Map

**Goal:** A working web map that shows the different data views and the data we have gathered, before any model exists. This proves the data pipeline and gives the team something visual to build on.

#### 1A. What the map shows (views and layers)

| View / Layer | Source | Display |
|---|---|---|
| **Basemap and boundaries** | OSM, PH admin boundaries (province, municipality) | Always on |
| **Rainfall** | CHIRPS (history), IMERG (recent) | Color-scaled raster overlay with a date slider |
| **Cloud / Himawari** | Himawari AHI via satpy | Latest cloud imagery overlay (IR or true-color composite) |
| **Temperature and evapotranspiration** | ERA5, NASA POWER | Raster overlay and point values |
| **Vegetation (NDVI)** | MODIS, Sentinel-2 | Raster overlay with a note when cloud-masked |
| **Soil moisture** | SMAP | Raster overlay |
| **Official advisories** | PAGASA and DOST | Polygons or markers with advisory text |
| **Crop calendar and farm stats** | PSA, DA, PhilRice | Choropleth by province, with planting and harvest windows in a popup |

#### 1B. "Data gathered" panel

A side panel that makes the data inventory visible to judges and to us:

- Per layer: **source name, last updated timestamp, resolution, coverage dates, status** (ok, stale, missing).
- Click on the map to see a **point summary**: rainfall, temp, NDVI, and soil moisture at that location.
- A simple **time slider** to scrub through available dates.

#### 1C. Technical approach (keep it simple for the MVP)

1. **Ingestion scripts** download data for the AOI and a recent date range.
2. **Processing** converts each layer to a small, map-ready format:
   - Rasters become **PNG image overlays plus a bounds JSON**, or Cloud Optimized GeoTIFFs. PNG overlays are the fastest path.
   - Vector data (boundaries, advisories, crop calendar) becomes **GeoJSON**.
   - A **`catalog.json`** lists every layer, date, source, and file path. The "Data gathered" panel reads this file.
3. **Upload** processed files to **OBS**. Configure public read or signed URLs for the map assets.
4. **Frontend:** a static page using **MapLibre GL JS** that reads `catalog.json` and loads layers from OBS.
5. **Hosting:** static files on OBS static website hosting, or served from the backend.

No model and no live API are needed for this phase. Pre-processed files are enough.

#### 1D. Suggested folder structure

```
project/
├── data/
│   ├── raw/               # downloaded files (git-ignored)
│   └── processed/         # map-ready PNG/GeoJSON + catalog.json
├── ingestion/
│   ├── himawari.py
│   ├── chirps.py
│   ├── imerg.py
│   ├── era5.py
│   ├── nasa_power.py
│   ├── ndvi.py
│   └── smap.py
├── processing/            # crop, reproject, colorize, export
├── frontend/              # MapLibre web map
├── backend/               # (Phase 5)
├── models/                # (Phase 4)
├── bots/                  # (Phase 6)
└── docs/
```

#### 1E. Acceptance criteria

- [ ] Map loads with the AOI and boundaries.
- [ ] At least **4 data layers** toggle on and off (rainfall, cloud, NDVI, and one of temp or soil moisture).
- [ ] The "Data gathered" panel lists every layer with source and last-updated date.
- [ ] Clicking a point shows values for the active layers.
- [ ] All map assets are served from OBS.

**Deliverable:** A demo link to the data map.

---

### Phase 2: Automated Data Ingestion

**Goal:** Replace manual downloads with scheduled pipelines.

- Wrap each ingestion script as a scheduled job (FunctionGraph timer triggers or cron on ECS).
- Add **retries, logging, and a "last successful run" record** per source.
- Write raw data to `obs://.../raw/`, processed data to `obs://.../processed/`.
- Update `catalog.json` automatically so the map stays current.
- Handle known failure cases: missing days, cloud-masked NDVI, source downtime.

**Deliverable:** The map updates itself daily without anyone touching it.

---

### Phase 3: Feature Engineering and Dataset Building

**Goal:** Turn raw layers into a clean training dataset.

- Align all sources to a **common grid and time step** (e.g. daily, 0.1° grid over the AOI).
- Build features: rolling rainfall totals (3, 7, 14, 30 days), temperature anomalies, NDVI change, soil moisture trend, cloud indicators from Himawari.
- Build **labels**. Examples: next-day or next-7-day rainfall (CHIRPS or IMERG Final as truth), or a drought and stress index.
- **Split by time, not randomly.** For example, train on earlier years and test on the most recent season, to avoid leakage.
- Save as `.npy`, Zarr, or MindRecord files in OBS.

**Deliverable:** A versioned dataset in OBS and a short data card describing it.

---

### Phase 4: Model Development (MindSpore on ModelArts)

**Goal:** A forecasting model that beats a naive baseline.

1. **Baselines first:** persistence ("tomorrow = today") and climatology. If we cannot beat these, the model is not adding value.
2. **LSTM** on point or regional time series. Quick to build and a good first MindSpore model.
3. **ConvLSTM** on gridded sequences (rainfall and cloud fields). Captures spatial movement.
4. **Transformer** is optional and only if time allows. More data hungry.
5. Train on ModelArts, track metrics (MAE, RMSE for amounts, precision/recall or CSI for rain/no-rain), and save checkpoints to OBS.
6. Export the best model for inference (MindSpore checkpoint, or MindIR).

**Deliverable:** A trained model with an evaluation table comparing it against the baselines.

---

### Phase 5: Backend API

**Goal:** One service that the map and bots both call.

Suggested endpoints:

| Endpoint | Returns |
|---|---|
| `GET /layers` | The catalog of layers and dates |
| `GET /point?lat=&lon=` | Current values and forecast for a location |
| `GET /forecast?area=&days=` | Forecast summary for a province or municipality |
| `GET /risk?area=` | Risk level (e.g. low, moderate, high) with a short explanation |
| `POST /subscribe` | Register a user and location for alerts |

- Build with FastAPI, deploy to ECS or FunctionGraph.
- Load the model for inference, or read precomputed forecasts from OBS (cheaper and simpler for a demo).

**Deliverable:** A deployed, documented API.

---

### Phase 6: Alerts (Telegram and Messenger)

**Goal:** Get forecasts to farmers.

- **Telegram bot first:** `/start`, set location, `/forecast`, `/subscribe`. Free and quick.
- **Messenger bot second:** create the Page and Meta app, run in development mode with testers for the demo, and start the app review process early because it takes time.
- Alert logic: a scheduled job checks forecasts against thresholds per subscribed area and sends a short message.
- Keep messages **short and actionable**. Offer Filipino and Cebuano text if possible. Example: *"Heavy rain expected in 3 days. Consider delaying fertilizer application."*

**Deliverable:** A live demo where a phone receives a real alert.

---

### Phase 7: Integration and Farmer-Facing Features

**Goal:** Tie everything together.

- Add **forecast and risk layers** to the map (model output next to observed data).
- Add **crop calendar logic**: link forecast risk to the crop stage in that province (planting, flowering, harvest).
- Show **PAGASA and DOST advisories beside our forecast** as a comparison view.
- Add a simple **history and accuracy view** so users can see how past forecasts performed.

**Deliverable:** An end-to-end flow from satellite data to a farmer-relevant risk message.

---

### Phase 8: Testing, Validation, and Competition Delivery

- Validate forecasts against held-out data and, if possible, against local observations or advisories.
- Test with a few real users (farmers, agri technicians, LGU staff) and record feedback.
- Document: architecture diagram, data sources and licenses, model card, limitations.
- Prepare the pitch: problem, Huawei Cloud usage (MindSpore, ModelArts, OBS, ECS or FunctionGraph), demo, impact, and sustainability plan.
- Record a backup demo video in case of live demo failure.

**Deliverable:** Final demo, documentation, and pitch deck.

---

## 4. Risks and Notes

| Risk | Mitigation |
|---|---|
| **Cloud cover** blocks optical NDVI (MODIS, Sentinel-2) | Use 16-day composites, gap-fill, and show a "cloud-masked" status in the data panel |
| **Himawari file size** | Crop to the AOI during processing and do not store full-disk files |
| **Huawei Cloud free quota limits** | Develop and debug locally, use ModelArts only for real training runs, and keep the AOI small |
| **No PAGASA or DOST API** | Manual or semi-manual ingestion. Confirm usage terms. Treat as a comparison layer, not a core dependency. |
| **PSA, DA, PhilRice data is in PDFs** | Hand-curate one province's crop calendar first |
| **Messenger app review delay** | Start early and use dev mode for the demo. Telegram is the fallback. |
| **Overpromising model accuracy** | Always report results against baselines and state limitations clearly |
| **Data licensing** | Record each source's license and citation requirements in `docs/` |

---

## 5. Suggested Timeline (adjust to the competition deadline)

| Phase | Suggested share of time |
|---|---|
| 0: Scoping | 5% |
| 1: Data map (initial build) | 20% |
| 2: Automated ingestion | 10% |
| 3: Dataset building | 10% |
| 4: Model development | 20% |
| 5: Backend API | 10% |
| 6: Alerts | 10% |
| 7 to 8: Integration, testing, pitch | 15% |

---

## 6. Immediate Next Steps

1. Decide the **AOI and crop** (Phase 0).
2. Everyone creates the free accounts in 2.2.
3. Assign owners: data ingestion, map frontend, Huawei Cloud setup.
4. Download one week of CHIRPS rainfall for the AOI and render it on a MapLibre page. This is the smallest end-to-end slice of Phase 1.
