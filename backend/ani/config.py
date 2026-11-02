from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / 'backend' / '.env')
raw_dir = Path(os.getenv('ANI_DATA_DIR', str(ROOT / 'data')))
DATA_DIR = raw_dir if raw_dir.is_absolute() else ROOT / 'backend' / raw_dir
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'ani.sqlite3'
# Map-ready grids (.npz) and rendered PNG overlays; safe to delete and rebuild.
PROCESSED_DIR = DATA_DIR / 'processed'
BOUNDARIES = ROOT / 'frontend' / 'public' / 'philippines-municipalities.json'
ADMIN_KEY = os.getenv('ANI_ADMIN_KEY', '')
REFRESH_HOURS = max(0, float(os.getenv('ANI_REFRESH_HOURS', '0')))
# Days of history collected for daily raster sources. IMERG needs 48 half-hour images per day.
RASTER_DAYS = max(1, min(30, int(os.getenv('ANI_RASTER_DAYS', '7'))))
