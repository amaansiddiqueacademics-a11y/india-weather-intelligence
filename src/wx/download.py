"""Acquire hourly ERA5-based history from the Open-Meteo Historical Archive API as CSV.

One raw CSV per (city, year), saved untouched. Throttled, retried with exponential
backoff, and idempotent (existing non-empty files are skipped).
"""
from __future__ import annotations

import sys
import time

import pandas as pd
import requests

from . import config

URL = "https://archive-api.open-meteo.com/v1/archive"
OUT = config.DATA_RAW / "openmeteo"
MIN_INTERVAL_S = 1.0  # polite throttle between requests


def fetch_year(session: requests.Session, loc: pd.Series, year: int) -> str:
    params = {
        "latitude": loc.latitude, "longitude": loc.longitude,
        "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
        "hourly": ",".join(config.HOURLY_VARS), "timezone": "Asia/Kolkata", "format": "csv",
    }
    delay = 5.0
    for attempt in range(8):
        r = session.get(URL, params=params, timeout=120)
        if r.status_code == 200 and "time," in r.text:
            return r.text
        wait = delay * (2 ** attempt)
        print(f"  {loc.location_id} {year}: HTTP {r.status_code} {r.text[:120]!r}; retry in {wait:.0f}s", flush=True)
        time.sleep(min(wait, 300))
    raise RuntimeError(f"failed {loc.location_id} {year}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    locs = pd.read_csv(config.LOCATIONS_CSV)
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    for _, loc in locs.iterrows():
        for year in range(config.START_YEAR, config.END_YEAR + 1):
            path = OUT / f"{loc.location_id}_{year}.csv"
            if path.exists() and path.stat().st_size > 1000:
                continue
            t0 = time.time()
            path.write_text(fetch_year(s, loc, year), encoding="utf-8")
            print(f"saved {path.name} ({path.stat().st_size/1e6:.2f} MB)", flush=True)
            time.sleep(max(0.0, MIN_INTERVAL_S - (time.time() - t0)))
    print("download complete")


if __name__ == "__main__":
    sys.exit(main())
