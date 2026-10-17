CREATE SCHEMA IF NOT EXISTS etl;
CREATE SCHEMA IF NOT EXISTS star;
CREATE SCHEMA IF NOT EXISTS snow;
CREATE SCHEMA IF NOT EXISTS ml;

-- ETL bookkeeping ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS etl.run_log (
    run_id        BIGSERIAL PRIMARY KEY,
    pipeline      TEXT NOT NULL,                   -- weather | agriculture
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'running', -- running | success | failed
    rows_read     BIGINT, rows_rejected BIGINT, rows_added_gap_fill BIGINT,
    rows_transformed BIGINT, rows_loaded BIGINT, rows_in_table_after BIGINT,
    runtime_s     DOUBLE PRECISION,
    error         TEXT
);
CREATE TABLE IF NOT EXISTS etl.step_log (
    step_id     BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES etl.run_log(run_id) ON DELETE CASCADE,
    step_no     SMALLINT NOT NULL,
    stage       TEXT NOT NULL,                     -- extract|validate|clean|transform|load
    step_name   TEXT NOT NULL,
    purpose     TEXT NOT NULL,
    columns     TEXT[] NOT NULL DEFAULT '{}',
    rows_affected BIGINT NOT NULL,
    before_stats JSONB, after_stats JSONB,
    UNIQUE (run_id, step_no)
);
CREATE TABLE IF NOT EXISTS etl.quality_metrics (
    metric_id   BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES etl.run_log(run_id) ON DELETE CASCADE,
    phase       TEXT NOT NULL,                     -- raw | clean
    location_id TEXT NOT NULL,
    variable    TEXT NOT NULL,
    n_rows BIGINT, n_missing BIGINT, pct_missing DOUBLE PRECISION,
    n_outliers BIGINT, v_min DOUBLE PRECISION, v_mean DOUBLE PRECISION,
    v_max DOUBLE PRECISION, v_std DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS etl.quarantine (
    quarantine_id BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES etl.run_log(run_id) ON DELETE CASCADE,
    location_id TEXT, source_file TEXT, obs_time TEXT,
    column_name TEXT, bad_value TEXT, reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_quality_run ON etl.quality_metrics(run_id);
CREATE INDEX IF NOT EXISTS ix_quarantine_run ON etl.quarantine(run_id);

-- ML metadata: model registry is one-to-one with the model dimension ------
CREATE TABLE IF NOT EXISTS ml.model_registry (
    model_key    INTEGER PRIMARY KEY,
    artifact_path TEXT NOT NULL,
    trained_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    train_period TEXT, val_period TEXT, test_period TEXT,
    n_train BIGINT, features JSONB, params JSONB, metrics JSONB, code_version TEXT
);
CREATE TABLE IF NOT EXISTS ml.analysis_result (
    name        TEXT PRIMARY KEY,
    params      JSONB,
    result      JSONB NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
