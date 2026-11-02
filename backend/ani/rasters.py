"""Georeferenced raster decoding, Web-Mercator resampling, PNG overlay export, and municipality pixel sampling."""
from __future__ import annotations
import io
import json
import math
import re
from pathlib import Path
import numpy as np
from PIL import Image
from . import config, db

# Matches MapLibre nationalBounds in frontend/src/MapView.tsx: (west, south, east, north) in EPSG:4326.
PH_BOUNDS = (116.8, 4.5, 126.8, 21.3)
PH_COORDINATES = [
    [PH_BOUNDS[0], PH_BOUNDS[3]],
    [PH_BOUNDS[2], PH_BOUNDS[3]],
    [PH_BOUNDS[2], PH_BOUNDS[1]],
    [PH_BOUNDS[0], PH_BOUNDS[1]],
]

LAYER_RAMPS = {
    'rainfall': {'max': 50.0, 'min': 0.0, 'unit': 'mm', 'transparent_below': 0.15,
                 'colors': ['#e5f0ed', '#b6d8c8', '#7bb5a6', '#3e8e84', '#1c666a']},
    'cloud': {'max': 100.0, 'min': 0.0, 'unit': '%', 'transparent_below': 4.0,
              'colors': ['#f4f7fb', '#d8e1f0', '#b3c3de', '#8396bb', '#52698e']},
    'temperature': {'max': 38.0, 'min': 18.0, 'unit': '°C', 'transparent_below': None,
                    'colors': ['#f7eccc', '#ead79e', '#dfb16e', '#cc8350', '#a7523d']},
    'evapotranspiration': {'max': 8.0, 'min': 0.0, 'unit': 'mm', 'transparent_below': None,
                           'colors': ['#f4efe2', '#e5cf9c', '#d4a359', '#b87435', '#8c4a1f']},
    'vegetation': {'max': 1.0, 'min': 0.0, 'unit': 'NDVI', 'transparent_below': None,
                   'colors': ['#e9e6ca', '#c7d5a5', '#95b883', '#619460', '#326b45']},
    'soil': {'max': 0.5, 'min': 0.0, 'unit': 'm³/m³', 'transparent_below': None,
             'colors': ['#e6eeee', '#bdd7de', '#8cb7c9', '#588ea9', '#326581']},
}

LAYER_SOURCE_PRIORITY = {
    'rainfall': ['chirps', 'imerg', 'open_meteo_grid', 'open_meteo'],
    'cloud': ['himawari', 'open_meteo_grid', 'open_meteo'],
    'vegetation': ['gibs_ndvi', 'modis'],
    'soil': ['smap', 'open_meteo_grid', 'open_meteo'],
    'temperature': ['open_meteo_grid', 'open_meteo'],
    'evapotranspiration': ['open_meteo_grid', 'open_meteo'],
}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip('#')
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def parse_interval_midpoint(expr: str) -> float | None:
    if not expr:
        return None
    nums = [float(x) for x in re.findall(r'[-+]?\d+(?:\.\d+)?', expr)]
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    return 0.5 * (nums[0] + nums[1])


def parse_gibs_colormap(xml_text: str) -> dict:
    """Parse a NASA GIBS ColorMap v1.3 XML document into RGB->value lookup arrays."""
    entries = []
    for tag in re.findall(r'<ColorMapEntry\b[^>]*/>', xml_text):
        rgb_m = re.search(r'rgb="(\d+),(\d+),(\d+)"', tag)
        if not rgb_m:
            continue
        rgb = (int(rgb_m.group(1)), int(rgb_m.group(2)), int(rgb_m.group(3)))
        nodata = 'nodata="true"' in tag
        transparent = 'transparent="true"' in tag
        val_m = re.search(r'value="([^"]+)"', tag) or re.search(r'sourceValue="([^"]+)"', tag)
        mid = None if nodata else parse_interval_midpoint(val_m.group(1) if val_m else '')
        entries.append({'rgb': rgb, 'nodata': nodata, 'transparent': transparent, 'value': mid})
    exact = {e['rgb']: e for e in entries}
    valid_entries = [e for e in entries if not e['nodata'] and e['value'] is not None]
    rgb_arr = np.array([e['rgb'] for e in valid_entries], dtype=np.int16) if valid_entries else np.zeros((0, 3), dtype=np.int16)
    val_arr = np.array([e['value'] for e in valid_entries], dtype=np.float32) if valid_entries else np.zeros((0,), dtype=np.float32)
    return {'entries': entries, 'exact': exact, 'rgb_arr': rgb_arr, 'val_arr': val_arr}


def decode_wms_png(png_bytes: bytes, colormap: dict, allow_nearest: bool = False,
                   transparent_fill: float | None = None, max_dist: int = 18) -> tuple[np.ndarray, dict]:
    """Decode an EPSG:4326 GIBS WMS RGBA PNG into a float32 physical-value grid (NaN = missing/masked)."""
    img = Image.open(io.BytesIO(png_bytes)).convert('RGBA')
    rgba = np.array(img, dtype=np.uint8)
    h, w, _ = rgba.shape
    alpha = rgba[..., 3]
    out = np.full((h, w), np.nan, dtype=np.float32)
    if transparent_fill is not None:
        out[alpha == 0] = transparent_fill

    opaque_mask = alpha > 0
    if not opaque_mask.any():
        return out, {'width': w, 'height': h, 'opaque_fraction': 0.0}

    rgb_flat = rgba[..., :3][opaque_mask]
    # Deduplicate unique RGB triples in the image so lookup takes <2 ms even for 1M pixels.
    uniq_rgb, inverse = np.unique(rgb_flat, axis=0, return_inverse=True)
    uniq_vals = np.full(len(uniq_rgb), np.nan, dtype=np.float32)
    exact = colormap['exact']
    rgb_arr = colormap['rgb_arr']
    val_arr = colormap['val_arr']

    unresolved = []
    for idx, triple in enumerate(uniq_rgb):
        key = (int(triple[0]), int(triple[1]), int(triple[2]))
        hit = exact.get(key)
        if hit is not None:
            if not hit['nodata'] and hit['value'] is not None:
                uniq_vals[idx] = float(hit['value'])
        elif allow_nearest and len(rgb_arr):
            unresolved.append(idx)

    if unresolved and len(rgb_arr):
        q = uniq_rgb[unresolved].astype(np.int16)
        dists = np.abs(q[:, None, :] - rgb_arr[None, :, :]).sum(axis=-1)
        best = dists.argmin(axis=1)
        best_dist = dists[np.arange(len(unresolved)), best]
        for local_i, global_i in enumerate(unresolved):
            if best_dist[local_i] <= max_dist:
                uniq_vals[global_i] = val_arr[best[local_i]]

    out[opaque_mask] = uniq_vals[inverse]
    return out, {'width': w, 'height': h, 'opaque_fraction': round(float(opaque_mask.mean()), 4)}


def _dilate_bool(mask: np.ndarray, radius: int = 3) -> np.ndarray:
    """Fast pure-NumPy 2D binary dilation with square structuring element (2*radius+1)."""
    h, w = mask.shape
    padded = np.pad(mask, radius, mode='edge')
    out = np.zeros((h, w), dtype=bool)
    span = 2 * radius + 1
    for dr in range(span):
        for dc in range(span):
            out |= padded[dr:dr + h, dc:dc + w]
    return out


def smooth_grid_3x3(grid: np.ndarray) -> np.ndarray:
    """Apply a NaN-safe 3x3 binomial (Gaussian-like) filter to remove WMS palette quantization steps."""
    h, w = grid.shape
    padded = np.pad(grid, 1, mode='edge')
    weights = ((1.0, 2.0, 1.0), (2.0, 4.0, 2.0), (1.0, 2.0, 1.0))
    acc = np.zeros((h, w), dtype=np.float32)
    w_sum = np.zeros((h, w), dtype=np.float32)
    for dr in range(3):
        for dc in range(3):
            patch = padded[dr:dr + h, dc:dc + w]
            valid = np.isfinite(patch)
            wt = weights[dr][dc]
            acc += np.where(valid, patch * wt, 0.0)
            w_sum += np.where(valid, wt, 0.0)
    return np.where(w_sum > 0.0, acc / np.maximum(w_sum, 1e-6), np.nan).astype(np.float32)


def decode_himawari_png(png_bytes: bytes, colormap: dict) -> tuple[np.ndarray, dict]:
    """Decode Himawari-9 Band 13 Clean IR WMS PNG without Dvorak grayscale-ring collisions on warm skies."""
    # GIBS Clean_Longwave_Infrared_Window_Band reuses grayscale RGBs for both warm scenes (-19..+56°C)
    # and the Dvorak deep-convective ring (-79.6..-70.6°C & -91.6°C). Filter out the cold grayscale
    # duplicates first so warm clear-sky/ocean pixels never alias into 100% storm contour rings.
    primary_entries = []
    cold_gray_entries = []
    for e in colormap['entries']:
        r, g, b = e['rgb']
        is_gray = max(r, g, b) - min(r, g, b) <= 3
        if not e['nodata'] and e['value'] is not None and is_gray and e['value'] < -65.0:
            cold_gray_entries.append(e)
        else:
            primary_entries.append(e)

    valid_primary = [e for e in primary_entries if not e['nodata'] and e['value'] is not None]
    primary_cmap = {
        'entries': primary_entries,
        'exact': {e['rgb']: e for e in primary_entries},
        'rgb_arr': np.array([e['rgb'] for e in valid_primary], dtype=np.int16),
        'val_arr': np.array([e['value'] for e in valid_primary], dtype=np.float32),
    }
    bt_c, stats = decode_wms_png(png_bytes, primary_cmap, allow_nearest=True, max_dist=30)

    # Restore genuine Dvorak cold grayscale tops only when enclosed by chromatic deep-convective anvils (< -55°C).
    if cold_gray_entries and np.isfinite(bt_c).any():
        img_rgba = np.array(Image.open(io.BytesIO(png_bytes)).convert('RGBA'), dtype=np.uint8)
        rgb = img_rgba[..., :3].astype(np.int16)
        chroma = rgb.max(axis=-1) - rgb.min(axis=-1)
        cold_chromatic = np.isfinite(bt_c) & (bt_c < -55.0) & (chroma > 8)
        if cold_chromatic.any():
            near_cold_core = _dilate_bool(cold_chromatic, radius=4)
            dvorak_core_mask = (img_rgba[..., 3] > 0) & (chroma <= 5) & near_cold_core & (bt_c > -25.0)
            bt_c = np.where(dvorak_core_mask, -74.0, bt_c)

    return bt_c, stats


def read_chirps_ph(tiff_bytes: bytes, bounds: tuple[float, float, float, float] = PH_BOUNDS) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Read a global CHIRPS float32 GeoTIFF via Pillow and crop to the Philippine bounding box."""
    with Image.open(io.BytesIO(tiff_bytes)) as im:
        tie = im.tag_v2.get(33922, (0.0, 0.0, 0.0, -180.0, 60.0, 0.0))
        scale = im.tag_v2.get(33550, (0.05, 0.05, 0.0))
        origin_lon, origin_lat = float(tie[3]), float(tie[4])
        dx, dy = float(scale[0]), float(scale[1])
        west, south, east, north = bounds
        c0 = max(0, int(math.floor((west - origin_lon) / dx)))
        c1 = min(im.width, int(math.ceil((east - origin_lon) / dx)))
        r0 = max(0, int(math.floor((origin_lat - north) / dy)))
        r1 = min(im.height, int(math.ceil((origin_lat - south) / dy)))
        cropped = im.crop((c0, r0, c1, r1))
        arr = np.array(cropped, dtype=np.float32)
    arr[(~np.isfinite(arr)) | (arr < 0.0)] = np.nan
    actual_bounds = (
        round(origin_lon + c0 * dx, 6),
        round(origin_lat - r1 * dy, 6),
        round(origin_lon + c1 * dx, 6),
        round(origin_lat - r0 * dy, 6),
    )
    return arr, actual_bounds


def interpolate_points_to_grid(lats: list[float], lons: list[float], values: list[float],
                               bounds: tuple[float, float, float, float] = PH_BOUNDS,
                               height: int = 224, width: int = 134,
                               land_mask: np.ndarray | None = None) -> np.ndarray:
    """Inverse-distance interpolate coarse reanalysis grid samples (e.g., 0.5°/0.75° Open-Meteo grid) onto PH_BOUNDS."""
    pts = [(la, lo, v) for la, lo, v in zip(lats, lons, values) if v is not None and math.isfinite(v)]
    if not pts:
        return np.full((height, width), np.nan, dtype=np.float32)
    west, south, east, north = bounds
    gy = np.linspace(north - (north - south) / (2 * height), south + (north - south) / (2 * height), height, dtype=np.float32)[:, None]
    gx = np.linspace(west + (east - west) / (2 * width), east - (east - west) / (2 * width), width, dtype=np.float32)[None, :]
    p_lat = np.array([p[0] for p in pts], dtype=np.float32)[None, None, :]
    p_lon = np.array([p[1] for p in pts], dtype=np.float32)[None, None, :]
    p_val = np.array([p[2] for p in pts], dtype=np.float32)[None, None, :]
    dist2 = (gy[..., None] - p_lat) ** 2 + (gx[..., None] - p_lon) ** 2
    # Keep nearest 6 grid vertices for smooth bilinear-like local interpolation without global bleeding.
    k = min(6, len(pts))
    idx = np.argpartition(dist2, k - 1, axis=-1)[..., :k]
    d2 = np.take_along_axis(dist2, idx, axis=-1)
    vals = np.take_along_axis(np.broadcast_to(p_val, dist2.shape), idx, axis=-1)
    weights = 1.0 / np.maximum(d2, 1e-4)
    grid = (weights * vals).sum(axis=-1) / weights.sum(axis=-1)
    if land_mask is not None and land_mask.shape == grid.shape:
        grid = np.where(land_mask, grid, np.nan)
    return grid.astype(np.float32)


def _mercator_y(lat_deg: np.ndarray | float) -> np.ndarray | float:
    rad = np.radians(lat_deg)
    return np.log(np.tan(np.pi / 4.0 + rad / 2.0))


def resample_to_mercator(grid: np.ndarray, grid_bounds: tuple[float, float, float, float],
                         out_bounds: tuple[float, float, float, float] = PH_BOUNDS,
                         out_height: int = 864, out_width: int = 514) -> np.ndarray:
    """Bilinearly resample an EPSG:4326 grid (top=north, bottom=south) into Web-Mercator rows for smooth MapLibre overlays."""
    h, w = grid.shape
    g_west, g_south, g_east, g_north = grid_bounds
    o_west, o_south, o_east, o_north = out_bounds
    y_top, y_bot = float(_mercator_y(o_north)), float(_mercator_y(o_south))
    merc_y = np.linspace(y_top, y_bot, out_height)
    lats = np.degrees(2.0 * np.arctan(np.exp(merc_y)) - np.pi / 2.0)
    lons = np.linspace(o_west, o_east, out_width)

    rf = np.clip((g_north - lats) / max(1e-9, g_north - g_south) * h - 0.5, 0.0, max(0.0, float(h - 1)))
    cf = np.clip((lons - g_west) / max(1e-9, g_east - g_west) * w - 0.5, 0.0, max(0.0, float(w - 1)))
    r0 = np.floor(rf).astype(np.int32)
    r1 = np.minimum(r0 + 1, h - 1)
    wy = (rf - r0).astype(np.float32)[:, None]
    c0 = np.floor(cf).astype(np.int32)
    c1 = np.minimum(c0 + 1, w - 1)
    wx = (cf - c0).astype(np.float32)[None, :]

    v00 = grid[r0[:, None], c0[None, :]]
    v01 = grid[r0[:, None], c1[None, :]]
    v10 = grid[r1[:, None], c0[None, :]]
    v11 = grid[r1[:, None], c1[None, :]]

    m00, m01, m10, m11 = np.isfinite(v00), np.isfinite(v01), np.isfinite(v10), np.isfinite(v11)
    w00 = (1.0 - wy) * (1.0 - wx) * m00
    w01 = (1.0 - wy) * wx * m01
    w10 = wy * (1.0 - wx) * m10
    w11 = wy * wx * m11
    w_sum = w00 + w01 + w10 + w11
    acc = (
        np.where(m00, v00 * w00, 0.0)
        + np.where(m01, v01 * w01, 0.0)
        + np.where(m10, v10 * w10, 0.0)
        + np.where(m11, v11 * w11, 0.0)
    )
    return np.where(w_sum >= 0.25, acc / np.maximum(w_sum, 1e-6), np.nan).astype(np.float32)


def colorize_grid(grid: np.ndarray, layer: str, cloud_mask: np.ndarray | None = None) -> Image.Image:
    """Render a physical-value grid into an RGBA PNG aligned with catalog color ramps."""
    spec = LAYER_RAMPS[layer]
    vmin, vmax = spec['min'], spec['max']
    stops = np.array([_hex_to_rgb(c) for c in spec['colors']], dtype=np.float32)
    n_seg = len(stops) - 1

    h, w = grid.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    valid = np.isfinite(grid)
    if spec['transparent_below'] is not None:
        valid = valid & (grid >= spec['transparent_below'])

    if valid.any():
        norm = np.clip((grid[valid] - vmin) / max(1e-6, vmax - vmin), 0.0, 1.0)
        scaled = norm * n_seg
        seg = np.minimum(np.floor(scaled).astype(np.int32), n_seg - 1)
        frac = (scaled - seg)[:, None]
        rgb = np.round(stops[seg] * (1.0 - frac) + stops[seg + 1] * frac).astype(np.uint8)
        if layer == 'cloud':
            # Smooth feathered opacity from 0 at threshold (4%) up to 242 at deep convective cloud tops (100%).
            t_thresh = (spec['transparent_below'] or 0.0) / max(1e-6, vmax - vmin)
            fade = np.clip((norm - t_thresh) / max(1e-6, 1.0 - t_thresh), 0.0, 1.0)
            alpha = np.round(242.0 * (fade ** 0.72)).astype(np.uint8)
        elif layer == 'rainfall':
            alpha = np.round(60.0 + 185.0 * (norm ** 0.52)).astype(np.uint8)
        else:
            alpha = np.full(len(norm), 225, dtype=np.uint8)
        rgba[valid, :3] = rgb
        rgba[valid, 3] = alpha

    if cloud_mask is not None and cloud_mask.shape == grid.shape:
        cm = cloud_mask & (~valid)
        if cm.any():
            # Subtle cool slate veil marks cloud-masked optical land pixels without fabricating NDVI.
            rgba[cm] = np.array([154, 168, 182, 130], dtype=np.uint8)

    return Image.fromarray(rgba)


def raster_dir(layer: str) -> Path:
    d = config.PROCESSED_DIR / 'rasters' / layer
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_raster(layer: str, source: str, obs_date: str, grid: np.ndarray,
                grid_bounds: tuple[float, float, float, float] = PH_BOUNDS,
                cloud_mask: np.ndarray | None = None, extra: dict | None = None) -> dict:
    """Persist Web-Mercator PNG overlay + compressed float32 grid + JSON metadata, and refresh processed catalog."""
    d = raster_dir(layer)
    stem = f'{source}_{obs_date}'
    merc_grid = resample_to_mercator(grid, grid_bounds, PH_BOUNDS)
    merc_cm = resample_to_mercator(cloud_mask.astype(np.float32), grid_bounds, PH_BOUNDS) > 0.5 if cloud_mask is not None else None
    img = colorize_grid(merc_grid, layer, cloud_mask=merc_cm)
    png_path = d / f'{stem}.png'
    img.save(png_path, format='PNG', optimize=True)

    np.savez_compressed(d / f'{stem}.npz', grid=grid.astype(np.float32), bounds=np.array(grid_bounds, dtype=np.float64))
    finite = grid[np.isfinite(grid)]
    spec = LAYER_RAMPS[layer]
    meta = {
        'layer': layer,
        'source': source,
        'date': obs_date,
        'filename': png_path.name,
        'url': f'/api/rasters/{layer}/{png_path.name}',
        'bounds': list(PH_BOUNDS),
        'coordinates': PH_COORDINATES,
        'unit': spec['unit'],
        'scale_min': spec['min'],
        'scale_max': spec['max'],
        'observed_min': round(float(finite.min()), 4) if finite.size else None,
        'observed_max': round(float(finite.max()), 4) if finite.size else None,
        'valid_pct': round(float(np.isfinite(grid).mean() * 100.0), 2),
        'cloud_masked_pct': round(float(cloud_mask.mean() * 100.0), 2) if cloud_mask is not None else 0.0,
        'created_at': db.now(),
        **(extra or {}),
    }
    (d / f'{stem}.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    rebuild_processed_catalog()
    return meta


def rebuild_processed_catalog() -> list[dict]:
    """Maintain data/processed/catalog.json listing all processed map-ready rasters."""
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    rasters_root = config.PROCESSED_DIR / 'rasters'
    if rasters_root.exists():
        for meta_file in sorted(rasters_root.glob('*/*.json')):
            try:
                items.append(json.loads(meta_file.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
    (config.PROCESSED_DIR / 'catalog.json').write_text(json.dumps(items, indent=2), encoding='utf-8')
    return items


def find_raster(layer: str, obs_date: str) -> dict | None:
    """Return the highest-priority available georeferenced raster metadata for (layer, obs_date)."""
    d = config.PROCESSED_DIR / 'rasters' / layer
    if not d.exists():
        return None
    priorities = LAYER_SOURCE_PRIORITY.get(layer, [])
    for source in priorities:
        meta_path = d / f'{source}_{obs_date}.json'
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding='utf-8'))
    # For rolling 8-day NDVI composites, match the latest composite whose 8-day window covers obs_date.
    if layer == 'vegetation':
        candidates = []
        for meta_path in d.glob('*.json'):
            try:
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if meta.get('date') and meta['date'] <= obs_date:
                candidates.append(meta)
        if candidates:
            best = max(candidates, key=lambda m: m['date'])
            from datetime import date as _date
            lag = (_date.fromisoformat(obs_date) - _date.fromisoformat(best['date'])).days
            if 0 <= lag <= 8:
                return {**best, 'composite_date': best['date'], 'requested_date': obs_date}
    return None


def sample_areas_from_grid(areas: dict, grid: np.ndarray, grid_bounds: tuple[float, float, float, float],
                           layer: str, obs_date: str, unit: str, source: str, kind: str,
                           quality_fn=None, record_cloud_masked: bool = False,
                           search_radius: int = 1) -> dict[str, list[dict]]:
    """Sample a georeferenced grid at every Philippine municipality's representative coordinates."""
    h, w = grid.shape
    west, south, east, north = grid_bounds
    dx = (east - west) / w
    dy = (north - south) / h
    rows_by_area: dict[str, list[dict]] = {}

    for area_id, area in areas.items():
        lat, lon = float(area['lat']), float(area['lon'])
        if not (west - 3.0 <= lon <= east + 1.0 and south - 1.0 <= lat <= north + 1.0):
            continue
        clamped_lat = min(north - 1e-5, max(south + 1e-5, lat))
        clamped_lon = min(east - 1e-5, max(west + 1e-5, lon))
        r = min(h - 1, max(0, int((north - clamped_lat) / dy)))
        c = min(w - 1, max(0, int((clamped_lon - west) / dx)))
        val = float(grid[r, c])
        r0, r1 = max(0, r - search_radius), min(h, r + search_radius + 1)
        c0, c1 = max(0, c - search_radius), min(w, c + search_radius + 1)
        patch = grid[r0:r1, c0:c1]
        finite_patch = patch[np.isfinite(patch)]
        cov = round(float(finite_patch.size / max(1, patch.size)), 3)

        # Coastal municipalities can sit half a pixel offshore on coarse 5-10 km grids; check immediate 3x3 cell if center is NaN.
        if not math.isfinite(val) and finite_patch.size > 0 and not record_cloud_masked:
            val = float(finite_patch.mean())

        sample_lat = round(north - (r + 0.5) * dy, 5)
        sample_lon = round(west + (c + 0.5) * dx, 5)
        if math.isfinite(val):
            q = quality_fn(val, cov) if quality_fn else f'Georeferenced {source} pixel sample'
            rows_by_area[area_id] = [{
                'layer': layer, 'date': obs_date, 'value': round(val, 4), 'unit': unit,
                'source': source, 'kind': kind, 'quality': q,
                'lat': sample_lat, 'lon': sample_lon, 'status': 'valid', 'coverage': cov,
            }]
        elif record_cloud_masked:
            rows_by_area[area_id] = [{
                'layer': layer, 'date': obs_date, 'value': None, 'unit': unit,
                'source': source, 'kind': kind,
                'quality': 'Cloud-masked optical composite pixel; no synthetic fill applied',
                'lat': sample_lat, 'lon': sample_lon, 'status': 'cloud-masked', 'coverage': cov,
            }]
    return rows_by_area


def approximate_land_mask(areas: dict, shape: tuple[int, int],
                          bounds: tuple[float, float, float, float] = PH_BOUNDS) -> np.ndarray:
    """Build a fast Philippine archipelago mask from the 1,642 municipality centroids for raster masking."""
    h, w = shape
    west, south, east, north = bounds
    dx = (east - west) / w
    dy = (north - south) / h
    mask = np.zeros((h, w), dtype=bool)
    rad_r = max(1, int(round(0.16 / dy)))
    rad_c = max(1, int(round(0.16 / dx)))
    for area in areas.values():
        r = int((north - float(area['lat'])) / dy)
        c = int((float(area['lon']) - west) / dx)
        if 0 <= r < h and 0 <= c < w:
            mask[max(0, r - rad_r):min(h, r + rad_r + 1), max(0, c - rad_c):min(w, c + rad_c + 1)] = True
    return mask
