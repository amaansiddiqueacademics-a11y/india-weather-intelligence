"""Seed conformed dimensions (date, time, location, source) in the star schema."""
from __future__ import annotations

import pandas as pd
import psycopg

from . import config
from .derived import imd_season

SOURCES = [
    ("openmeteo_era5", "reanalysis", "Open-Meteo Historical Weather API (ECMWF ERA5 / ERA5-Land)",
     "CC BY 4.0 (Open-Meteo); non-commercial use of free API", "https://open-meteo.com/en/docs/historical-weather-api"),
    ("india_apy", "administrative statistics", "India Data Portal / DES, Ministry of Agriculture (APY)",
     "Open Data Commons Attribution License", "https://indiadataportal.com/"),
    ("wx_models", "model output", "This project (scikit-learn models)", "project", "n/a"),
]


def seed_dim_date(conn: psycopg.Connection, start: str = "1997-01-01", end: str = "2023-12-31") -> int:
    d = pd.DataFrame({"full_date": pd.date_range(start, end, freq="D")})
    d["date_key"] = d.full_date.dt.strftime("%Y%m%d").astype(int)
    d["year"], d["quarter"], d["month"] = d.full_date.dt.year, d.full_date.dt.quarter, d.full_date.dt.month
    d["month_name"], d["day"] = d.full_date.dt.month_name(), d.full_date.dt.day
    d["day_of_year"], d["iso_week"] = d.full_date.dt.dayofyear, d.full_date.dt.isocalendar().week.astype(int)
    d["day_name"], d["is_weekend"] = d.full_date.dt.day_name(), d.full_date.dt.dayofweek >= 5
    d["imd_season"] = d.month.map(imd_season)
    cols = ["date_key", "full_date", "year", "quarter", "month", "month_name", "day", "day_of_year",
            "iso_week", "day_name", "is_weekend", "imd_season"]
    rows = [tuple(x) for x in d[cols].assign(full_date=d.full_date.dt.date).itertuples(index=False)]
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO star.dim_date ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
            "ON CONFLICT (date_key) DO NOTHING", rows)
    return len(rows)


def part_of_day(h: int) -> str:
    return "Night" if h < 5 or h >= 21 else "Morning" if h < 12 else "Afternoon" if h < 17 else "Evening"


def seed_dim_time(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO star.dim_time VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            [(h, f"{h:02d}:00", part_of_day(h)) for h in range(24)])


def seed_dim_location(conn: psycopg.Connection) -> None:
    locs = pd.read_csv(config.LOCATIONS_CSV).fillna("")
    with conn.cursor() as cur:
        for r in locs.itertuples():
            cur.execute(
                "INSERT INTO star.dim_location (location_id, city, district, state, region, latitude, longitude, agri_district) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (location_id) DO UPDATE SET "
                "city=EXCLUDED.city, district=EXCLUDED.district, state=EXCLUDED.state, region=EXCLUDED.region, "
                "latitude=EXCLUDED.latitude, longitude=EXCLUDED.longitude, agri_district=EXCLUDED.agri_district",
                (r.location_id, r.city, r.district, r.state, r.region, r.latitude, r.longitude, r.agri_district or None))


def seed_dim_source(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO star.dim_source (source_name, source_type, provider, licence, url) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (source_name) DO NOTHING", SOURCES)


def seed_all(conn: psycopg.Connection) -> None:
    seed_dim_date(conn)
    seed_dim_time(conn)
    seed_dim_location(conn)
    seed_dim_source(conn)
    conn.commit()
