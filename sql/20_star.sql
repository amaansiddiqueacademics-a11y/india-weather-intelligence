-- STAR SCHEMA: facts reference fully denormalised dimensions directly.
CREATE TABLE IF NOT EXISTS star.dim_date (
    date_key     INTEGER PRIMARY KEY,             -- yyyymmdd
    full_date    DATE NOT NULL UNIQUE,
    year SMALLINT NOT NULL, quarter SMALLINT NOT NULL, month SMALLINT NOT NULL,
    month_name TEXT NOT NULL, day SMALLINT NOT NULL, day_of_year SMALLINT NOT NULL,
    iso_week SMALLINT NOT NULL, day_name TEXT NOT NULL, is_weekend BOOLEAN NOT NULL,
    imd_season TEXT NOT NULL                      -- Winter | Pre-monsoon | Monsoon | Post-monsoon
);
CREATE TABLE IF NOT EXISTS star.dim_time (
    time_key SMALLINT PRIMARY KEY CHECK (time_key BETWEEN 0 AND 23),
    hour_label TEXT NOT NULL, part_of_day TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS star.dim_location (
    location_key SMALLSERIAL PRIMARY KEY,
    location_id TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL, district TEXT NOT NULL, state TEXT NOT NULL, region TEXT NOT NULL,
    latitude DOUBLE PRECISION NOT NULL, longitude DOUBLE PRECISION NOT NULL,
    grid_latitude DOUBLE PRECISION, grid_longitude DOUBLE PRECISION, elevation_m REAL,
    agri_district TEXT
);
CREATE TABLE IF NOT EXISTS star.dim_source (
    source_key SMALLSERIAL PRIMARY KEY, source_name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL, provider TEXT NOT NULL, licence TEXT, url TEXT
);
CREATE TABLE IF NOT EXISTS star.dim_crop (
    crop_key SERIAL PRIMARY KEY, crop_name TEXT NOT NULL UNIQUE, crop_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS star.dim_crop_season (
    crop_season_key SMALLSERIAL PRIMARY KEY, season_name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS star.dim_model (
    model_key SERIAL PRIMARY KEY, model_name TEXT NOT NULL, model_family TEXT NOT NULL,
    risk_module TEXT NOT NULL, target TEXT NOT NULL, horizon_h INTEGER NOT NULL,
    version TEXT NOT NULL, is_baseline BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (model_name, target, horizon_h, version)
);

CREATE TABLE IF NOT EXISTS star.fact_weather_hourly (
    location_key SMALLINT NOT NULL REFERENCES star.dim_location,
    date_key INTEGER NOT NULL REFERENCES star.dim_date,
    time_key SMALLINT NOT NULL REFERENCES star.dim_time,
    source_key SMALLINT NOT NULL REFERENCES star.dim_source,
    temperature_c REAL, apparent_temperature_c REAL, dew_point_c REAL,
    relative_humidity_pct REAL, precipitation_mm REAL, pressure_msl_hpa REAL,
    surface_pressure_hpa REAL, cloud_cover_pct REAL, wind_speed_kmh REAL,
    wind_direction_deg REAL, wind_gust_kmh REAL, shortwave_radiation_wm2 REAL,
    et0_mm REAL, soil_moisture_m3m3 REAL, heat_index_c REAL, wet_bulb_c REAL,
    is_imputed BOOLEAN NOT NULL DEFAULT FALSE, is_outlier BOOLEAN NOT NULL DEFAULT FALSE,
    qc_note TEXT,
    PRIMARY KEY (location_key, date_key, time_key)
);
CREATE INDEX IF NOT EXISTS ix_star_fw_date ON star.fact_weather_hourly(date_key);

CREATE TABLE IF NOT EXISTS star.fact_agri_production (
    location_key SMALLINT NOT NULL REFERENCES star.dim_location,
    crop_key INTEGER NOT NULL REFERENCES star.dim_crop,
    crop_season_key SMALLINT NOT NULL REFERENCES star.dim_crop_season,
    year_start_date_key INTEGER NOT NULL REFERENCES star.dim_date,   -- 1 April of the agricultural year
    source_key SMALLINT NOT NULL REFERENCES star.dim_source,
    area_ha DOUBLE PRECISION, production_t DOUBLE PRECISION, yield_t_ha DOUBLE PRECISION,
    PRIMARY KEY (location_key, crop_key, crop_season_key, year_start_date_key)
);

CREATE TABLE IF NOT EXISTS star.fact_prediction (
    prediction_id BIGSERIAL PRIMARY KEY,
    model_key INTEGER NOT NULL REFERENCES star.dim_model,
    location_key SMALLINT NOT NULL REFERENCES star.dim_location,
    issue_date_key INTEGER NOT NULL REFERENCES star.dim_date,
    issue_time_key SMALLINT NOT NULL REFERENCES star.dim_time,
    target_date_key INTEGER NOT NULL REFERENCES star.dim_date,
    target_time_key SMALLINT NOT NULL REFERENCES star.dim_time,
    horizon_h INTEGER NOT NULL,
    predicted DOUBLE PRECISION NOT NULL, pred_lower DOUBLE PRECISION, pred_upper DOUBLE PRECISION,
    probability DOUBLE PRECISION, actual DOUBLE PRECISION,
    UNIQUE (model_key, location_key, issue_date_key, issue_time_key)
);
CREATE INDEX IF NOT EXISTS ix_star_fp_loc ON star.fact_prediction(location_key, issue_date_key);

CREATE TABLE IF NOT EXISTS star.fact_model_evaluation (
    evaluation_id BIGSERIAL PRIMARY KEY,
    model_key INTEGER NOT NULL REFERENCES star.dim_model,
    location_key SMALLINT REFERENCES star.dim_location,        -- NULL = all locations
    scope TEXT NOT NULL,                                       -- overall | location | season
    scope_value TEXT NOT NULL DEFAULT 'all',
    metric_name TEXT NOT NULL, metric_value DOUBLE PRECISION, n_samples BIGINT,
    UNIQUE (model_key, scope, scope_value, metric_name)
);
