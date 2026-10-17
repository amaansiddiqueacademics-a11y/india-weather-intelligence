"""Weather ETL: extract -> validate -> clean -> transform -> load.

Pure transformation functions are separated from I/O so they can be unit-tested
on small fixtures. `run()` orchestrates, logs every step with before/after
statistics, quarantines rejected rows, and upserts idempotently into PostgreSQL.
"""
from __future__ import annotations

import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

from . import config, db, seed
from .derived import heat_index_c, wet_bulb_c

# raw column (without unit) -> (warehouse column, expected unit)
COLUMN_MAP = {
    "temperature_2m": ("temperature_c", "°C"),
    "relative_humidity_2m": ("relative_humidity_pct", "%"),
    "dew_point_2m": ("dew_point_c", "°C"),
    "apparent_temperature": ("apparent_temperature_c", "°C"),
    "precipitation": ("precipitation_mm", "mm"),
    "pressure_msl": ("pressure_msl_hpa", "hPa"),
    "surface_pressure": ("surface_pressure_hpa", "hPa"),
    "cloud_cover": ("cloud_cover_pct", "%"),
    "wind_speed_10m": ("wind_speed_kmh", "km/h"),
    "wind_direction_10m": ("wind_direction_deg", "°"),
    "wind_gusts_10m": ("wind_gust_kmh", "km/h"),
    "shortwave_radiation": ("shortwave_radiation_wm2", "W/m²"),
    "et0_fao_evapotranspiration": ("et0_mm", "mm"),
    "soil_moisture_0_to_7cm": ("soil_moisture_m3m3", "m³/m³"),
}
VALUE_COLS = [v[0] for v in COLUMN_MAP.values()]
# Hard physical limits: a value outside is impossible, so the row is quarantined.
HARD_RANGES = {
    "temperature_c": (-90, 60), "relative_humidity_pct": (0, 100), "dew_point_c": (-90, 40),
    "apparent_temperature_c": (-90, 80), "precipitation_mm": (0, 300), "pressure_msl_hpa": (850, 1090),
    "surface_pressure_hpa": (400, 1090), "cloud_cover_pct": (0, 100), "wind_speed_kmh": (0, 400),
    "wind_direction_deg": (0, 360), "wind_gust_kmh": (0, 500), "shortwave_radiation_wm2": (0, 1500),
    "et0_mm": (0, 5), "soil_moisture_m3m3": (0, 1),
}
INTERP_COLS = [c for c in VALUE_COLS if c != "precipitation_mm"]  # rain is never interpolated
MAX_GAP_H = 3
OUTLIER_Z = 6.0
OUTLIER_COLS = ["temperature_c", "pressure_msl_hpa", "wind_speed_kmh"]


@dataclass
class StepLogger:
    steps: list[dict] = field(default_factory=list)

    def add(self, stage: str, name: str, purpose: str, columns: list[str], affected: int,
            before: dict | None = None, after: dict | None = None) -> None:
        self.steps.append(dict(step_no=len(self.steps) + 1, stage=stage, step_name=name, purpose=purpose,
                               columns=columns, rows_affected=int(affected), before=before, after=after))


def col_stats(df: pd.DataFrame, cols: list[str]) -> dict:
    out = {}
    for c in cols:
        s = df[c]
        out[c] = dict(missing=int(s.isna().sum()),
                      mean=None if s.notna().sum() == 0 else round(float(s.mean()), 3),
                      min=None if s.notna().sum() == 0 else round(float(s.min()), 3),
                      max=None if s.notna().sum() == 0 else round(float(s.max()), 3))
    return out


# ---------------------------------------------------------------- extract
def parse_raw_csv(path: Path, location_id: str) -> tuple[pd.DataFrame, dict, list[str]]:
    """Read one Open-Meteo CSV: returns data, header metadata, and unit mismatches."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    meta = dict(zip(lines[0].split(","), lines[1].split(",")))
    df = pd.read_csv(io.StringIO("\n".join(lines[3:])))
    renamed, bad_units = {}, []
    for col in df.columns:
        if col == "time":
            continue
        m = re.match(r"^(.*?) \((.*)\)$", col)
        raw, unit = (m.group(1), m.group(2)) if m else (col, "")
        if raw in COLUMN_MAP:
            renamed[col] = COLUMN_MAP[raw][0]
            if unit != COLUMN_MAP[raw][1]:
                bad_units.append(f"{col} expected {COLUMN_MAP[raw][1]}")
    df = df.rename(columns=renamed)
    df.insert(0, "location_id", location_id)
    df["source_file"] = path.name
    return df, meta, bad_units


def extract(raw_dir: Path, location_ids: list[str] | None = None) -> tuple[pd.DataFrame, dict, list[str], int]:
    frames, metas, bad, n_files = [], {}, [], 0
    for path in sorted(raw_dir.glob("*.csv")):
        loc = path.stem.split("_")[0]
        if location_ids and loc not in location_ids:
            continue
        df, meta, b = parse_raw_csv(path, loc)
        frames.append(df)
        metas[loc] = meta
        bad += b
        n_files += 1
    return pd.concat(frames, ignore_index=True), metas, bad, n_files


# --------------------------------------------------------------- validate
def validate_timestamps(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse timestamps; rows that fail to parse are rejected."""
    out = df.copy()
    out["obs_time"] = pd.to_datetime(out["time"], errors="coerce")
    bad = out[out.obs_time.isna()].assign(reason="unparseable timestamp", column_name="time", bad_value=lambda d: d["time"])
    return out[out.obs_time.notna()].drop(columns="time"), bad


def drop_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Duplicate (location, obs_time) rows: keep the first, quarantine the rest."""
    dup = df.duplicated(["location_id", "obs_time"], keep="first")
    bad = df[dup].assign(reason="duplicate (location, hour)", column_name="obs_time",
                         bad_value=lambda d: d["obs_time"].astype(str))
    return df[~dup], bad


def check_ranges(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reject rows with physically impossible values (NaN is allowed here: handled by cleaning)."""
    reject = pd.Series(False, index=df.index)
    reasons = []
    for col, (lo, hi) in HARD_RANGES.items():
        mask = df[col].notna() & ((df[col] < lo) | (df[col] > hi))
        if mask.any():
            r = df[mask].assign(reason=f"{col} outside physical range [{lo},{hi}]", column_name=col,
                                bad_value=lambda d, c=col: d[c].astype(str))
            reasons.append(r)
        reject |= mask
    bad = pd.concat(reasons) if reasons else df.iloc[0:0].assign(reason="", column_name="", bad_value="")
    return df[~reject], bad


# ------------------------------------------------------------------ clean
def fill_gaps(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Reindex each location to a complete hourly grid. Returns frame and #rows added."""
    parts, added = [], 0
    for loc, g in df.groupby("location_id", sort=False):
        full = pd.date_range(g.obs_time.min(), g.obs_time.max(), freq="h")
        g = g.set_index("obs_time").reindex(full)
        added += int(g.location_id.isna().sum())
        g["location_id"] = loc
        g["source_file"] = g["source_file"].ffill().bfill()
        g.index.name = "obs_time"
        parts.append(g.reset_index())
    return pd.concat(parts, ignore_index=True), added


def impute_short_gaps(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Linear interpolation for gaps up to MAX_GAP_H hours (never for precipitation)."""
    df = df.copy()
    df["is_imputed"] = False
    filled_cells = 0
    for loc, idx in df.groupby("location_id", sort=False).groups.items():
        block = df.loc[idx, INTERP_COLS]
        before = block.isna()
        new = block.interpolate(method="linear", limit_area="inside")
        # pandas `limit` would fill the first 3 hours of a longer gap, so measure each gap's length and fill only short ones
        run_len = before.apply(lambda m: m.groupby((m != m.shift()).cumsum()).transform("sum"))
        changed = before & new.notna() & (run_len <= MAX_GAP_H)
        new = block.where(~changed, new)
        df.loc[idx, INTERP_COLS] = new
        df.loc[idx, "is_imputed"] = changed.any(axis=1).to_numpy()
        filled_cells += int(changed.to_numpy().sum())
    return df, filled_cells, int(df.is_imputed.sum())


def flag_outliers(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Flag (never delete) values > OUTLIER_Z robust SDs from their location-month-hour median."""
    df = df.copy()
    month, hour = df.obs_time.dt.month, df.obs_time.dt.hour
    df["is_outlier"] = False
    df["qc_note"] = ""
    counts = {}
    for col in OUTLIER_COLS:
        g = df.groupby([df.location_id, month, hour])[col]
        med = g.transform("median")
        mad = (df[col] - med).abs().groupby([df.location_id, month, hour]).transform("median")
        z = (df[col] - med) / (1.4826 * mad.clip(lower=0.1))
        mask = z.abs() > OUTLIER_Z
        df.loc[mask, "is_outlier"] = True
        df.loc[mask, "qc_note"] += f"{col}_z>{OUTLIER_Z:g};"
        counts[col] = int(mask.sum())
    bad_dew = df.dew_point_c > df.temperature_c + 0.5
    df.loc[bad_dew, "qc_note"] += "dewpoint_above_temperature;"
    counts["dew_point_above_temperature"] = int(bad_dew.sum())
    return df, counts


# -------------------------------------------------------------- transform
def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["heat_index_c"] = heat_index_c(df.temperature_c.to_numpy(), df.relative_humidity_pct.to_numpy())
    df["wet_bulb_c"] = wet_bulb_c(df.temperature_c.to_numpy(), df.relative_humidity_pct.to_numpy())
    df["date_key"] = df.obs_time.dt.strftime("%Y%m%d").astype(int)
    df["time_key"] = df.obs_time.dt.hour
    return df


def quality_rows(df: pd.DataFrame, phase: str) -> list[tuple]:
    rows = []
    for loc, g in df.groupby("location_id"):
        for col in VALUE_COLS:
            s = g[col]
            n_out = int(g["is_outlier"].sum()) if (phase == "clean" and col in OUTLIER_COLS) else None
            rows.append((phase, loc, col, len(s), int(s.isna().sum()), float(s.isna().mean() * 100), n_out,
                         None if s.notna().sum() == 0 else float(s.min()),
                         None if s.notna().sum() == 0 else float(s.mean()),
                         None if s.notna().sum() == 0 else float(s.max()),
                         None if s.notna().sum() < 2 else float(s.std())))
    return rows


# ------------------------------------------------------------------- load
FACT_COLS = ["location_key", "date_key", "time_key", "source_key"] + VALUE_COLS + [
    "heat_index_c", "wet_bulb_c", "is_imputed", "is_outlier", "qc_note"]


def load_facts(conn: psycopg.Connection, df: pd.DataFrame) -> int:
    """COPY into a temp staging table, then upsert on the natural/primary key."""
    conn.execute("DROP TABLE IF EXISTS pg_temp.stg_weather")
    conn.execute("CREATE TEMP TABLE stg_weather (LIKE star.fact_weather_hourly INCLUDING DEFAULTS)")
    stage_cols = ",".join(FACT_COLS)
    for _, chunk in df.groupby("location_key", sort=False):
        buf = io.StringIO()
        chunk[FACT_COLS].to_csv(buf, index=False, header=False, na_rep="\\N")
        buf.seek(0)
        with conn.cursor().copy(f"COPY stg_weather ({stage_cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')") as cp:
            while data := buf.read(1 << 20):
                cp.write(data)
    updates = ",".join(f"{c}=EXCLUDED.{c}" for c in FACT_COLS[4:])
    cur = conn.execute(
        f"INSERT INTO star.fact_weather_hourly ({stage_cols}) SELECT {stage_cols} FROM stg_weather "
        f"ON CONFLICT (location_key, date_key, time_key) DO UPDATE SET {updates}")
    return cur.rowcount


def run(raw_dir: Path | None = None, location_ids: list[str] | None = None) -> dict:
    raw_dir = raw_dir or config.DATA_RAW / "openmeteo"
    t0 = time.time()
    db.ensure_database()
    conn = db.connect()
    db.apply_ddl(conn)
    seed.seed_all(conn)
    run_id = conn.execute("INSERT INTO etl.run_log (pipeline) VALUES ('weather') RETURNING run_id").fetchone()["run_id"]
    conn.commit()
    steps, quarantine = StepLogger(), []
    try:
        raw, metas, bad_units, n_files = extract(raw_dir, location_ids)
        n_read = len(raw)
        steps.add("extract", "Read raw CSV files", "Load every city-year file untouched and parse header metadata/units.",
                  ["time"] + VALUE_COLS, n_read, after={"files": n_files, "rows": n_read, "unit_mismatches": len(bad_units)})
        raw_quality = quality_rows(raw.assign(is_outlier=False), "raw")

        df, bad = validate_timestamps(raw)
        quarantine.append(bad)
        steps.add("validate", "Timestamp parsing", "Parse ISO timestamps; local time is Asia/Kolkata (UTC+5:30, no DST).",
                  ["time"], len(bad))
        df, bad = drop_duplicates(df)
        quarantine.append(bad)
        steps.add("validate", "Duplicate detection", "Each (location, hour) must be unique; extras are quarantined.",
                  ["location_id", "obs_time"], len(bad))
        before = col_stats(df, VALUE_COLS)
        df, bad = check_ranges(df)
        quarantine.append(bad)
        steps.add("validate", "Physical range check", "Reject rows with physically impossible values (e.g. RH>100%).",
                  list(HARD_RANGES), bad.index.nunique() if len(bad) else 0, before, col_stats(df, VALUE_COLS))

        df, added = fill_gaps(df)
        steps.add("clean", "Gap detection / hourly reindex", "Reindex each city to a complete hourly grid so gaps become explicit.",
                  ["obs_time"], added)
        before = col_stats(df, INTERP_COLS)
        df, cells, rows_imputed = impute_short_gaps(df)
        steps.add("clean", f"Short-gap interpolation (<= {MAX_GAP_H} h)", "Linear interpolation for brief gaps; longer gaps and rainfall stay missing.",
                  INTERP_COLS, cells, before, col_stats(df, INTERP_COLS))
        df, out_counts = flag_outliers(df)
        steps.add("clean", "Outlier flagging", f"Flag (not delete) values beyond {OUTLIER_Z:g} robust SD of the location-month-hour median.",
                  OUTLIER_COLS + ["dew_point_c"], int(df.is_outlier.sum()), None, out_counts)
        df = add_derived(df)
        steps.add("transform", "Derived variables", "Heat index (Rothfusz), wet-bulb (Stull), date/time surrogate keys.",
                  ["heat_index_c", "wet_bulb_c", "date_key", "time_key"], len(df), None,
                  col_stats(df, ["heat_index_c", "wet_bulb_c"]))
        clean_quality = quality_rows(df, "clean")

        # surrogate-key lookups
        loc_keys = {r["location_id"]: r["location_key"] for r in conn.execute("SELECT location_id, location_key FROM star.dim_location")}
        src = conn.execute("SELECT source_key FROM star.dim_source WHERE source_name='openmeteo_era5'").fetchone()["source_key"]
        df["location_key"], df["source_key"] = df.location_id.map(loc_keys), src
        for loc, m in metas.items():  # record the actual ERA5 grid cell and elevation
            conn.execute("UPDATE star.dim_location SET grid_latitude=%s, grid_longitude=%s, elevation_m=%s WHERE location_id=%s",
                         (float(m["latitude"]), float(m["longitude"]), float(m["elevation"]), loc))
        df["qc_note"] = df.qc_note.replace("", np.nan)
        df = df[df.obs_time.notna()]
        loaded = load_facts(conn, df)
        n_table = conn.execute("SELECT count(*) AS n FROM star.fact_weather_hourly").fetchone()["n"]
        steps.add("load", "Upsert into star.fact_weather_hourly", "COPY to staging then INSERT ... ON CONFLICT DO UPDATE on the primary key (idempotent).",
                  FACT_COLS, loaded, None, {"rows_in_fact_after": n_table})

        # persist logs
        rejected = pd.concat([q for q in quarantine if len(q)])if any(len(q) for q in quarantine) else pd.DataFrame()
        n_rejected = int(sum(len(q) for q in quarantine))
        if n_rejected:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO etl.quarantine (run_id, location_id, source_file, obs_time, column_name, bad_value, reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [(run_id, r.location_id, r.source_file, str(r.obs_time) if "obs_time" in rejected else None,
                      r.column_name, r.bad_value, r.reason) for r in rejected.itertuples()])
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO etl.quality_metrics (run_id, phase, location_id, variable, n_rows, n_missing, pct_missing, n_outliers, v_min, v_mean, v_max, v_std) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", [(run_id, *r) for r in raw_quality + clean_quality])
            cur.executemany(
                "INSERT INTO etl.step_log (run_id, step_no, stage, step_name, purpose, columns, rows_affected, before_stats, after_stats) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(run_id, s["step_no"], s["stage"], s["step_name"], s["purpose"], s["columns"], s["rows_affected"],
                  json.dumps(s["before"]) if s["before"] else None, json.dumps(s["after"]) if s["after"] else None) for s in steps.steps])
        conn.execute(
            "UPDATE etl.run_log SET finished_at=now(), status='success', rows_read=%s, rows_rejected=%s, rows_added_gap_fill=%s, "
            "rows_transformed=%s, rows_loaded=%s, rows_in_table_after=%s, runtime_s=%s WHERE run_id=%s",
            (n_read, n_rejected, added, len(df), loaded, n_table, round(time.time() - t0, 2), run_id))
        conn.commit()
        return dict(run_id=run_id, rows_read=n_read, rejected=n_rejected, gap_rows_added=added, loaded=loaded,
                    rows_in_table=n_table, runtime_s=round(time.time() - t0, 1))
    except Exception as exc:
        conn.rollback()
        conn.execute("UPDATE etl.run_log SET finished_at=now(), status='failed', error=%s WHERE run_id=%s", (str(exc)[:2000], run_id))
        conn.commit()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
