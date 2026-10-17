"""Central configuration. Secrets/DSNs come from the environment (.env), never code."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"
SQL_DIR = ROOT / "sql"
LOCATIONS_CSV = ROOT / "config" / "locations.csv"

# Local PostgreSQL (pg_hba trusts 127.0.0.1). Override with WX_DSN.
DSN = os.environ.get("WX_DSN", "host=localhost port=5432 dbname=wx user=postgres")
ADMIN_DSN = os.environ.get("WX_ADMIN_DSN", "host=localhost port=5432 dbname=postgres user=postgres")

START_YEAR = int(os.environ.get("WX_START_YEAR", "2002"))
END_YEAR = int(os.environ.get("WX_END_YEAR", "2022"))
USER_AGENT = "india-weather-intelligence/0.1 (academic DWM project; non-commercial)"

HOURLY_VARS = [
    "temperature_2m", "relative_humidity_2m", "dew_point_2m", "apparent_temperature",
    "precipitation", "pressure_msl", "surface_pressure", "cloud_cover",
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
    "shortwave_radiation", "et0_fao_evapotranspiration", "soil_moisture_0_to_7cm",
]
