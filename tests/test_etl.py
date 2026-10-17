"""ETL stage tests on tiny fixtures (no network, no database)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from wx import etl_weather as etl
from wx.derived import heat_category, heat_index_c, imd_season, wet_bulb_c

FIXTURE = """latitude,longitude,elevation,utc_offset_seconds,timezone,timezone_abbreviation
19.08,72.85,9.0,19800,Asia/Kolkata,GMT+5:30

time,temperature_2m (°C),relative_humidity_2m (%),dew_point_2m (°C),apparent_temperature (°C),precipitation (mm),pressure_msl (hPa),surface_pressure (hPa),cloud_cover (%),wind_speed_10m (km/h),wind_direction_10m (°),wind_gusts_10m (km/h),shortwave_radiation (W/m²),et0_fao_evapotranspiration (mm),soil_moisture_0_to_7cm (m³/m³)
2020-06-01T00:00,28.0,80,24.0,32.0,0.0,1005.0,1000.0,50,10.0,200,20.0,0.0,0.0,0.30
2020-06-01T01:00,27.5,82,24.0,31.0,1.2,1005.0,1000.0,60,11.0,210,21.0,0.0,0.0,0.30
2020-06-01T02:00,27.0,85,24.0,30.0,0.0,1004.0,999.0,70,12.0,220,22.0,0.0,0.0,0.30
"""


def frame(tmp_path):
    p = tmp_path / "BOM_2020.csv"
    p.write_text(FIXTURE, encoding="utf-8")
    df, meta, bad = etl.parse_raw_csv(p, "BOM")
    return df, meta, bad


def test_parse_normalises_columns_and_units(tmp_path):
    df, meta, bad = frame(tmp_path)
    assert bad == []
    assert {"temperature_c", "relative_humidity_pct", "precipitation_mm", "soil_moisture_m3m3"} <= set(df.columns)
    assert len(df) == 3 and meta["elevation"] == "9.0"


def test_unit_mismatch_is_reported(tmp_path):
    p = tmp_path / "BOM_2020.csv"
    p.write_text(FIXTURE.replace("temperature_2m (°C)", "temperature_2m (°F)"), encoding="utf-8")
    _, _, bad = etl.parse_raw_csv(p, "BOM")
    assert bad and "temperature_2m" in bad[0]


def test_timestamp_and_duplicates(tmp_path):
    df, _, _ = frame(tmp_path)
    df.loc[1, "time"] = "not-a-date"
    ok, bad = etl.validate_timestamps(df)
    assert len(ok) == 2 and len(bad) == 1
    dup = pd.concat([ok, ok.iloc[[0]]], ignore_index=True)
    kept, rej = etl.drop_duplicates(dup)
    assert len(kept) == 2 and len(rej) == 1


def test_range_check_quarantines_impossible_values(tmp_path):
    df, _, _ = frame(tmp_path)
    df["obs_time"] = pd.to_datetime(df.time)
    df.loc[0, "relative_humidity_pct"] = 140
    df.loc[1, "temperature_c"] = 75
    ok, bad = etl.check_ranges(df)
    assert len(ok) == 1 and set(bad.column_name) == {"relative_humidity_pct", "temperature_c"}


def test_extreme_but_valid_values_are_kept(tmp_path):
    df, _, _ = frame(tmp_path)
    df["obs_time"] = pd.to_datetime(df.time)
    df.loc[0, "temperature_c"] = 49.5          # genuine extreme heat
    df.loc[1, "precipitation_mm"] = 120.0      # genuine extreme rain
    ok, bad = etl.check_ranges(df)
    assert len(ok) == 3 and bad.empty


def test_gap_fill_and_short_interpolation(tmp_path):
    df, _, _ = frame(tmp_path)
    df["obs_time"] = pd.to_datetime(df.time)
    df = df.drop(index=1)                       # one missing hour
    full, added = etl.fill_gaps(df)
    assert added == 1 and len(full) == 3
    out, cells, rows = etl.impute_short_gaps(full)
    assert out.loc[1, "temperature_c"] == 27.5  # linear midpoint of 28.0 and 27.0
    assert pd.isna(out.loc[1, "precipitation_mm"])  # rainfall never interpolated
    assert rows == 1 and cells > 0


def test_long_gaps_stay_missing():
    t = pd.date_range("2020-01-01", periods=10, freq="h")
    d = pd.DataFrame({"location_id": "X", "obs_time": t, **{c: 1.0 for c in etl.VALUE_COLS}, "source_file": "f"})
    d.loc[2:6, "temperature_c"] = np.nan       # 5-hour gap > MAX_GAP_H
    out, _, _ = etl.impute_short_gaps(d)
    assert out.temperature_c.isna().sum() == 5  # a gap longer than MAX_GAP_H is left entirely empty, not part-filled


def test_outliers_are_flagged_not_deleted():
    t = pd.date_range("2019-01-01", periods=24 * 60, freq="h")
    rng = np.random.default_rng(0)
    d = pd.DataFrame({"location_id": "X", "obs_time": t, "temperature_c": 20 + rng.normal(0, 0.3, len(t)),
                      "pressure_msl_hpa": 1010.0, "wind_speed_kmh": 10.0, "dew_point_c": 10.0})
    d.loc[100, "temperature_c"] = 45.0
    out, counts = etl.flag_outliers(d)
    assert len(out) == len(d) and out.loc[100, "is_outlier"] and counts["temperature_c"] >= 1


def test_derived_variables():
    hi = heat_index_c(np.array([35.0, 20.0]), np.array([70.0, 50.0]))
    assert hi[0] > 35 + 5 and abs(hi[1] - 20) < 2  # humid heat feels hotter; cool air ~ unchanged
    assert 20 < wet_bulb_c(np.array([35.0]), np.array([50.0]))[0] < 35
    assert heat_category(45) == "Danger" and heat_category(20) == "Low"
    assert [imd_season(m) for m in (1, 4, 7, 11)] == ["Winter", "Pre-monsoon", "Monsoon", "Post-monsoon"]


def test_agri_cleaning_rejects_bad_rows():
    from wx.etl_agri import clean_agri
    raw = pd.DataFrame({"state_name": ["Gujarat"] * 4, "district_name": ["Ahmedabad"] * 4, "year": ["2010-2011"] * 4, "crop_name": list("abcd"),
                        "crop_type": "Cereals", "season": "Kharif", "area": [100, 0, 100, 100], "production": [200, 10, 200, -5], "yield": [2.0, 1.0, 9.0, 1.0]})
    mapping = pd.DataFrame({"state": ["Gujarat"], "agri_district": ["Ahmedabad"], "location_id": ["AMD"]})
    ok, rej, counts = clean_agri(raw, mapping)
    assert len(ok) == 1 and counts["rejected"] == 3
