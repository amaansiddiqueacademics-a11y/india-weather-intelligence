-- SNOWFLAKE SCHEMA: same facts, dimensions normalised into hierarchies.
CREATE TABLE IF NOT EXISTS snow.dim_region (region_key SMALLSERIAL PRIMARY KEY, region_name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS snow.dim_state (
    state_key SMALLSERIAL PRIMARY KEY, state_name TEXT NOT NULL UNIQUE,
    region_key SMALLINT NOT NULL REFERENCES snow.dim_region);
CREATE TABLE IF NOT EXISTS snow.dim_district (
    district_key SMALLSERIAL PRIMARY KEY, district_name TEXT NOT NULL,
    state_key SMALLINT NOT NULL REFERENCES snow.dim_state, UNIQUE (state_key, district_name));
CREATE TABLE IF NOT EXISTS snow.dim_location (
    location_key SMALLINT PRIMARY KEY, location_id TEXT NOT NULL UNIQUE, city TEXT NOT NULL,
    district_key SMALLINT NOT NULL REFERENCES snow.dim_district,
    latitude DOUBLE PRECISION NOT NULL, longitude DOUBLE PRECISION NOT NULL,
    grid_latitude DOUBLE PRECISION, grid_longitude DOUBLE PRECISION, elevation_m REAL);

CREATE TABLE IF NOT EXISTS snow.dim_season (season_key SMALLSERIAL PRIMARY KEY, season_name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS snow.dim_month (
    month_key SMALLINT PRIMARY KEY CHECK (month_key BETWEEN 1 AND 12), month_name TEXT NOT NULL,
    quarter SMALLINT NOT NULL, season_key SMALLINT NOT NULL REFERENCES snow.dim_season);
CREATE TABLE IF NOT EXISTS snow.dim_year (year_key SMALLINT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS snow.dim_date (
    date_key INTEGER PRIMARY KEY, full_date DATE NOT NULL UNIQUE,
    year_key SMALLINT NOT NULL REFERENCES snow.dim_year,
    month_key SMALLINT NOT NULL REFERENCES snow.dim_month,
    day SMALLINT NOT NULL, day_of_year SMALLINT NOT NULL, iso_week SMALLINT NOT NULL,
    day_name TEXT NOT NULL, is_weekend BOOLEAN NOT NULL);
CREATE TABLE IF NOT EXISTS snow.dim_part_of_day (pod_key SMALLSERIAL PRIMARY KEY, part_of_day TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS snow.dim_time (
    time_key SMALLINT PRIMARY KEY CHECK (time_key BETWEEN 0 AND 23), hour_label TEXT NOT NULL,
    pod_key SMALLINT NOT NULL REFERENCES snow.dim_part_of_day);
CREATE TABLE IF NOT EXISTS snow.dim_source (
    source_key SMALLINT PRIMARY KEY, source_name TEXT NOT NULL UNIQUE, source_type TEXT NOT NULL,
    provider TEXT NOT NULL, licence TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS snow.dim_crop_type (crop_type_key SMALLSERIAL PRIMARY KEY, crop_type TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS snow.dim_crop (
    crop_key INTEGER PRIMARY KEY, crop_name TEXT NOT NULL UNIQUE,
    crop_type_key SMALLINT NOT NULL REFERENCES snow.dim_crop_type);
CREATE TABLE IF NOT EXISTS snow.dim_crop_season (crop_season_key SMALLINT PRIMARY KEY, season_name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS snow.dim_model_family (family_key SMALLSERIAL PRIMARY KEY, model_family TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS snow.dim_model (
    model_key INTEGER PRIMARY KEY, model_name TEXT NOT NULL,
    family_key SMALLINT NOT NULL REFERENCES snow.dim_model_family,
    risk_module TEXT NOT NULL, target TEXT NOT NULL, horizon_h INTEGER NOT NULL,
    version TEXT NOT NULL, is_baseline BOOLEAN NOT NULL DEFAULT FALSE);

CREATE TABLE IF NOT EXISTS snow.fact_weather_hourly (
    location_key SMALLINT NOT NULL REFERENCES snow.dim_location,
    date_key INTEGER NOT NULL REFERENCES snow.dim_date,
    time_key SMALLINT NOT NULL REFERENCES snow.dim_time,
    source_key SMALLINT NOT NULL REFERENCES snow.dim_source,
    temperature_c REAL, apparent_temperature_c REAL, dew_point_c REAL,
    relative_humidity_pct REAL, precipitation_mm REAL, pressure_msl_hpa REAL,
    surface_pressure_hpa REAL, cloud_cover_pct REAL, wind_speed_kmh REAL,
    wind_direction_deg REAL, wind_gust_kmh REAL, shortwave_radiation_wm2 REAL,
    et0_mm REAL, soil_moisture_m3m3 REAL, heat_index_c REAL, wet_bulb_c REAL,
    is_imputed BOOLEAN NOT NULL DEFAULT FALSE, is_outlier BOOLEAN NOT NULL DEFAULT FALSE,
    qc_note TEXT,
    PRIMARY KEY (location_key, date_key, time_key));
CREATE INDEX IF NOT EXISTS ix_snow_fw_date ON snow.fact_weather_hourly(date_key);

CREATE TABLE IF NOT EXISTS snow.fact_agri_production (
    district_key SMALLINT NOT NULL REFERENCES snow.dim_district,
    crop_key INTEGER NOT NULL REFERENCES snow.dim_crop,
    crop_season_key SMALLINT NOT NULL REFERENCES snow.dim_crop_season,
    year_start_date_key INTEGER NOT NULL REFERENCES snow.dim_date,
    source_key SMALLINT NOT NULL REFERENCES snow.dim_source,
    area_ha DOUBLE PRECISION, production_t DOUBLE PRECISION, yield_t_ha DOUBLE PRECISION,
    PRIMARY KEY (district_key, crop_key, crop_season_key, year_start_date_key));

CREATE TABLE IF NOT EXISTS snow.fact_prediction (
    prediction_id BIGINT PRIMARY KEY,
    model_key INTEGER NOT NULL REFERENCES snow.dim_model,
    location_key SMALLINT NOT NULL REFERENCES snow.dim_location,
    issue_date_key INTEGER NOT NULL REFERENCES snow.dim_date,
    issue_time_key SMALLINT NOT NULL REFERENCES snow.dim_time,
    target_date_key INTEGER NOT NULL REFERENCES snow.dim_date,
    target_time_key SMALLINT NOT NULL REFERENCES snow.dim_time,
    horizon_h INTEGER NOT NULL, predicted DOUBLE PRECISION NOT NULL,
    pred_lower DOUBLE PRECISION, pred_upper DOUBLE PRECISION, probability DOUBLE PRECISION,
    actual DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS snow.fact_model_evaluation (
    evaluation_id BIGINT PRIMARY KEY,
    model_key INTEGER NOT NULL REFERENCES snow.dim_model,
    location_key SMALLINT REFERENCES snow.dim_location,
    scope TEXT NOT NULL, scope_value TEXT NOT NULL, metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION, n_samples BIGINT);
