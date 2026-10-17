"""Build the snowflake schema from the star schema and (re)create materialized views."""
from __future__ import annotations

import json

import psycopg

from . import db

SNOW_LOAD = """
TRUNCATE snow.fact_prediction, snow.fact_model_evaluation;
INSERT INTO snow.dim_region (region_name) SELECT DISTINCT region FROM star.dim_location ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_state (state_name, region_key)
  SELECT DISTINCT l.state, r.region_key FROM star.dim_location l JOIN snow.dim_region r ON r.region_name=l.region
  ON CONFLICT (state_name) DO NOTHING;
INSERT INTO snow.dim_district (district_name, state_key)
  SELECT DISTINCT l.district, s.state_key FROM star.dim_location l JOIN snow.dim_state s ON s.state_name=l.state
  ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_location (location_key, location_id, city, district_key, latitude, longitude, grid_latitude, grid_longitude, elevation_m)
  SELECT l.location_key, l.location_id, l.city, d.district_key, l.latitude, l.longitude, l.grid_latitude, l.grid_longitude, l.elevation_m
  FROM star.dim_location l JOIN snow.dim_state s ON s.state_name=l.state
  JOIN snow.dim_district d ON d.district_name=l.district AND d.state_key=s.state_key
  ON CONFLICT (location_key) DO UPDATE SET grid_latitude=EXCLUDED.grid_latitude, grid_longitude=EXCLUDED.grid_longitude, elevation_m=EXCLUDED.elevation_m;
INSERT INTO snow.dim_season (season_name)
  SELECT DISTINCT imd_season FROM star.dim_date ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_month (month_key, month_name, quarter, season_key)
  SELECT DISTINCT d.month, d.month_name, d.quarter, s.season_key FROM star.dim_date d JOIN snow.dim_season s ON s.season_name=d.imd_season
  ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_year (year_key) SELECT DISTINCT year FROM star.dim_date ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_date (date_key, full_date, year_key, month_key, day, day_of_year, iso_week, day_name, is_weekend)
  SELECT date_key, full_date, year, month, day, day_of_year, iso_week, day_name, is_weekend FROM star.dim_date ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_part_of_day (part_of_day) SELECT DISTINCT part_of_day FROM star.dim_time ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_time (time_key, hour_label, pod_key)
  SELECT t.time_key, t.hour_label, p.pod_key FROM star.dim_time t JOIN snow.dim_part_of_day p USING (part_of_day) ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_source (source_key, source_name, source_type, provider, licence, url)
  SELECT source_key, source_name, source_type, provider, licence, url FROM star.dim_source ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_crop_type (crop_type) SELECT DISTINCT crop_type FROM star.dim_crop ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_crop (crop_key, crop_name, crop_type_key)
  SELECT c.crop_key, c.crop_name, t.crop_type_key FROM star.dim_crop c JOIN snow.dim_crop_type t USING (crop_type) ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_crop_season SELECT crop_season_key, season_name FROM star.dim_crop_season ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_model_family (model_family) SELECT DISTINCT model_family FROM star.dim_model ON CONFLICT DO NOTHING;
INSERT INTO snow.dim_model (model_key, model_name, family_key, risk_module, target, horizon_h, version, is_baseline)
  SELECT m.model_key, m.model_name, f.family_key, m.risk_module, m.target, m.horizon_h, m.version, m.is_baseline
  FROM star.dim_model m JOIN snow.dim_model_family f USING (model_family) ON CONFLICT DO NOTHING;
INSERT INTO snow.fact_weather_hourly SELECT * FROM star.fact_weather_hourly ON CONFLICT DO NOTHING;
INSERT INTO snow.fact_agri_production (district_key, crop_key, crop_season_key, year_start_date_key, source_key, area_ha, production_t, yield_t_ha)
  SELECT sl.district_key, a.crop_key, a.crop_season_key, a.year_start_date_key, a.source_key, a.area_ha, a.production_t, a.yield_t_ha
  FROM star.fact_agri_production a JOIN snow.dim_location sl ON sl.location_key=a.location_key ON CONFLICT DO NOTHING;
INSERT INTO snow.fact_prediction SELECT * FROM star.fact_prediction ON CONFLICT DO NOTHING;
INSERT INTO snow.fact_model_evaluation SELECT * FROM star.fact_model_evaluation ON CONFLICT DO NOTHING;
"""

MATVIEWS = """
DROP MATERIALIZED VIEW IF EXISTS star.mv_weather_daily CASCADE;
CREATE MATERIALIZED VIEW star.mv_weather_daily AS
SELECT f.location_key, f.date_key, d.full_date, d.year, d.month, d.imd_season,
       count(*) AS n_hours,
       max(temperature_c) AS tmax_c, min(temperature_c) AS tmin_c, avg(temperature_c) AS tmean_c,
       sum(precipitation_mm) AS precip_mm, avg(relative_humidity_pct) AS rh_mean,
       avg(wind_speed_kmh) AS wind_mean_kmh, max(wind_gust_kmh) AS gust_max_kmh,
       max(heat_index_c) AS hi_max_c, max(wet_bulb_c) AS wb_max_c,
       avg(soil_moisture_m3m3) AS soil_moisture, sum(et0_mm) AS et0_mm,
       avg(pressure_msl_hpa) AS pressure_mean_hpa
FROM star.fact_weather_hourly f JOIN star.dim_date d USING (date_key)
GROUP BY f.location_key, f.date_key, d.full_date, d.year, d.month, d.imd_season;
CREATE UNIQUE INDEX ON star.mv_weather_daily (location_key, date_key);
CREATE INDEX ON star.mv_weather_daily (year, month);
"""


def build(conn: psycopg.Connection) -> dict:
    conn.execute(SNOW_LOAD)
    conn.execute(MATVIEWS)
    conn.commit()
    out = {}
    for sch in ("star", "snow"):
        out[sch] = conn.execute(
            "SELECT count(*) AS n FROM %s.fact_weather_hourly" % sch).fetchone()["n"]
    return out


if __name__ == "__main__":
    c = db.connect()
    print(json.dumps(build(c)))
