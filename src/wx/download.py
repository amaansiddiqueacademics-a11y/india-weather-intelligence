"""Acquire raw data: hourly ERA5 history (Open-Meteo Historical Archive API) and district crop statistics (India Data Portal).

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
# India Data Portal (CKAN) "Crop Wise Area Production Yield", ODC-BY. Saved where wx.etl_agri reads it.
AGRI_URL = ("https://ckan.indiadataportal.com/dataset/5b8f4660-52d2-4edf-824b-1af41d290498/resource/"
            "a000ce51-a257-42e3-8ab7-3cd212966f88/download/crop-wise-area-production-yield.csv")
AGRI_OUT = config.DATA_RAW / "agri" / "crop-wise-area-production-yield.csv"
MIN_INTERVAL_S = 1.0  # polite throttle between requests


def fetch_year(session: requests.Session, loc: pd.Series, year: int) -> str:
    params = {
        "latitude": loc.latitude, "longitude": loc.longitude,
        "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
        "hourly": ",".join(config.HOURLY_VARS), "timezone": "Asia/Kolkata", "format": "csv",
    }
    delay = 5.0
    for attempt in range(8):
        try:
            r = session.get(URL, params=params, timeout=120)
        except requests.RequestException as exc:  # dropped connection / timeout: transient, retry with the same backoff
            wait = delay * (2 ** attempt)
            print(f"  {loc.location_id} {year}: {exc.__class__.__name__}; retry in {wait:.0f}s", flush=True)
            time.sleep(min(wait, 300))
            continue
        if r.status_code == 200 and "time," in r.text:
            return r.text
        if r.status_code == 429 and "Daily API request limit" in r.text:
            # backing off cannot help until the quota resets at 00:00 UTC; saved files are kept, so just re-run later
            raise SystemExit(f"Open-Meteo daily quota reached at {loc.location_id} {year}. Re-run `python -m wx.download` "
                             "after 00:00 UTC (05:30 IST); files already saved are skipped.")
        wait = delay * (2 ** attempt)
        print(f"  {loc.location_id} {year}: HTTP {r.status_code} {r.text[:120]!r}; retry in {wait:.0f}s", flush=True)
        time.sleep(min(wait, 300))
    raise RuntimeError(f"failed {loc.location_id} {year}")


def fetch_agri(session: requests.Session) -> None:
    """Download the national crop CSV once (about 56 MB); skipped if already present."""
    if AGRI_OUT.exists() and AGRI_OUT.stat().st_size > 1_000_000:
        return
    AGRI_OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = AGRI_OUT.with_suffix(".part")
    with session.get(AGRI_URL, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    with open(tmp, "rb") as f:
        head = f.read(200)
    if not head.startswith(b"id,year,state_name"):
        tmp.unlink()
        raise RuntimeError("crop download did not return the expected CSV header")
    tmp.replace(AGRI_OUT)
    print(f"saved {AGRI_OUT.name} ({AGRI_OUT.stat().st_size/1e6:.1f} MB)", flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    locs = pd.read_csv(config.LOCATIONS_CSV)
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    fetch_agri(s)
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
