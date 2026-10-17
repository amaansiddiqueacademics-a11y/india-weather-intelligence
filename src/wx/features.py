"""Leak-free feature construction shared by training and inference.

Every feature at time t uses only observations at or before t. Targets are
built separately (see ml.py) from values strictly after t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ISSUE_HOURS = (0, 6, 12, 18)
HOURLY_COLS = [
    "temperature_c", "relative_humidity_pct", "dew_point_c", "pressure_msl_hpa", "cloud_cover_pct",
    "wind_speed_kmh", "wind_gust_kmh", "wind_direction_deg", "heat_index_c", "apparent_temperature_c",
    "soil_moisture_m3m3", "shortwave_radiation_wm2", "precipitation_mm",
]
STATIC_COLS = ["latitude", "longitude", "elevation_m"]


def make_features(g: pd.DataFrame) -> pd.DataFrame:
    """g: one location, hourly index (obs_time) sorted and complete. Returns feature frame."""
    t, p = g["temperature_c"], g["pressure_msl_hpa"]
    f = pd.DataFrame(index=g.index)
    f["temp"], f["rh"], f["dew"], f["pres"] = t, g["relative_humidity_pct"], g["dew_point_c"], p
    f["cloud"], f["wind"], f["gust"] = g["cloud_cover_pct"], g["wind_speed_kmh"], g["wind_gust_kmh"]
    f["hi"], f["app"], f["soil"], f["rad"] = g["heat_index_c"], g["apparent_temperature_c"], g["soil_moisture_m3m3"], g["shortwave_radiation_wm2"]
    for k in (1, 3, 6, 12, 24, 48):
        f[f"temp_l{k}"] = t.shift(k)
    f["rh_l24"] = g["relative_humidity_pct"].shift(24)
    f["pres_d3"], f["pres_d24"] = p - p.shift(3), p - p.shift(24)
    pr = g["precipitation_mm"]
    for w in (6, 24, 72, 168):
        f[f"rain_{w}h"] = pr.rolling(w, min_periods=w).sum()
    f["temp_mean24"], f["temp_max24"], f["temp_min24"] = (t.rolling(24, min_periods=24).mean(),
                                                         t.rolling(24, min_periods=24).max(), t.rolling(24, min_periods=24).min())
    f["hi_max24"] = g["heat_index_c"].rolling(24, min_periods=24).max()
    hour, doy = g.index.hour.to_numpy(), g.index.dayofyear.to_numpy()
    f["sin_h"], f["cos_h"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    f["sin_d"], f["cos_d"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    wd = np.deg2rad(g["wind_direction_deg"])  # direction is circular: use sin/cos, never the raw angle
    f["wd_sin"], f["wd_cos"] = np.sin(wd), np.cos(wd)
    return f


def climatology(df: pd.DataFrame, col: str) -> dict:
    """Mean of `col` per (location_id, month, hour). Pass TRAINING rows only."""
    g = df.groupby(["location_id", df.index.month, df.index.hour])[col].mean()
    return {k: float(v) for k, v in g.items()}


def attach_climatology(f: pd.DataFrame, loc_id: str, clim: dict, name: str) -> pd.Series:
    keys = list(zip([loc_id] * len(f), f.index.month, f.index.hour))
    return pd.Series([clim.get(k, np.nan) for k in keys], index=f.index, name=name)
