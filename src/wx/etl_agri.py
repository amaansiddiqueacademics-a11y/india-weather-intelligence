"""Agriculture ETL: district-level crop area/production/yield (India Data Portal APY).

Only districts that contain one of the supported cities are loaded (the weather
data is a point per city, so national coverage would join to nothing).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from . import config, db, seed
from .etl_weather import StepLogger

RAW = config.DATA_RAW / "agri" / "crop-wise-area-production-yield.csv"
STATE_FIX = {"Delhi": "Delhi"}


def clean_agri(df: pd.DataFrame, mapping: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Filter to mapped districts and validate; returns (accepted, rejected, counts)."""
    counts: dict[str, int] = {"input_rows": len(df)}
    key = mapping[["state", "agri_district", "location_id"]].rename(columns={"state": "state_name", "agri_district": "district_name"})
    df = df.merge(key, on=["state_name", "district_name"], how="inner")
    counts["in_supported_districts"] = len(df)
    df["year_start"] = df.year.str[:4].astype(int)
    rej = []
    # Each rule is evaluated on the rows still in play, so a row is rejected once (first failing rule)
    # and the boolean mask always aligns with the frame it filters.
    for reason, rule in {
        "area missing or <= 0": lambda d: d.area.isna() | (d.area <= 0),
        "production missing or negative": lambda d: d.production.isna() | (d.production < 0),
    }.items():
        mask = rule(df)
        rej.append(df[mask].assign(reason=reason))
        df = df[~mask]
    # yield consistency: reported yield must equal production/area within 5 %
    implied = df.production / df.area
    inconsistent = ((df["yield"] - implied).abs() > 0.05 * implied.clip(lower=0.01)) & (df["yield"].notna())
    rej.append(df[inconsistent].assign(reason="yield != production/area (>5%)"))
    df = df[~inconsistent]
    dup = df.duplicated(["location_id", "crop_name", "season", "year_start"], keep="first")
    rej.append(df[dup].assign(reason="duplicate district-crop-season-year"))
    df = df[~dup]
    rejected = pd.concat(rej) if rej else df.iloc[0:0]
    counts["rejected"], counts["accepted"] = len(rejected), len(df)
    return df.assign(yield_calc=df.production / df.area), rejected, counts


def run() -> dict:
    t0 = time.time()
    db.ensure_database()
    conn = db.connect()
    db.apply_ddl(conn)
    seed.seed_all(conn)
    run_id = conn.execute("INSERT INTO etl.run_log (pipeline) VALUES ('agriculture') RETURNING run_id").fetchone()["run_id"]
    conn.commit()
    steps = StepLogger()
    raw = pd.read_csv(RAW, low_memory=False)
    mapping = pd.read_csv(config.LOCATIONS_CSV).dropna(subset=["agri_district"])
    steps.add("extract", "Read national APY CSV", "Load the district-level crop file as published.",
              list(raw.columns), len(raw), after={"rows": len(raw), "states": int(raw.state_name.nunique()),
                                                 "districts": int(raw.district_name.nunique())})
    df, rejected, counts = clean_agri(raw, mapping)
    steps.add("validate", "Filter to supported districts", "Keep only districts containing a supported city (joinable to weather).",
              ["state_name", "district_name"], counts["input_rows"] - counts["in_supported_districts"],
              {"rows": counts["input_rows"]}, {"rows": counts["in_supported_districts"]})
    steps.add("validate", "Value and consistency checks", "Reject non-positive area, negative production, yield != production/area, duplicates.",
              ["area", "production", "yield"], counts["rejected"], None, {"accepted": counts["accepted"]})

    keys = {r["location_id"]: r["location_key"] for r in conn.execute("SELECT location_id, location_key FROM star.dim_location")}
    src = conn.execute("SELECT source_key FROM star.dim_source WHERE source_name='india_apy'").fetchone()["source_key"]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO star.dim_crop (crop_name, crop_type) VALUES (%s,%s) ON CONFLICT (crop_name) DO NOTHING",
                        df[["crop_name", "crop_type"]].drop_duplicates("crop_name").itertuples(index=False, name=None))
        cur.executemany("INSERT INTO star.dim_crop_season (season_name) VALUES (%s) ON CONFLICT DO NOTHING",
                        [(s,) for s in df.season.unique()])
    crop = {r["crop_name"]: r["crop_key"] for r in conn.execute("SELECT * FROM star.dim_crop")}
    seas = {r["season_name"]: r["crop_season_key"] for r in conn.execute("SELECT * FROM star.dim_crop_season")}
    rows = [(keys[r.location_id], crop[r.crop_name], seas[r.season], r.year_start * 10000 + 401, src,
             r.area, r.production, r.yield_calc) for r in df.itertuples()]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO star.fact_agri_production (location_key, crop_key, crop_season_key, year_start_date_key, source_key, area_ha, production_t, yield_t_ha) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (location_key, crop_key, crop_season_key, year_start_date_key) "
            "DO UPDATE SET area_ha=EXCLUDED.area_ha, production_t=EXCLUDED.production_t, yield_t_ha=EXCLUDED.yield_t_ha", rows)
    n_table = conn.execute("SELECT count(*) AS n FROM star.fact_agri_production").fetchone()["n"]
    steps.add("load", "Upsert into star.fact_agri_production", "Idempotent upsert on (district, crop, season, year).",
              ["area_ha", "production_t", "yield_t_ha"], len(rows), None, {"rows_in_fact_after": n_table})
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO etl.quarantine (run_id, location_id, source_file, obs_time, column_name, bad_value, reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [(run_id, r.location_id, RAW.name, r.year, f"{r.crop_name}/{r.season}", str(r.production), r.reason) for r in rejected.itertuples()])
        cur.executemany(
            "INSERT INTO etl.step_log (run_id, step_no, stage, step_name, purpose, columns, rows_affected, before_stats, after_stats) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(run_id, s["step_no"], s["stage"], s["step_name"], s["purpose"], s["columns"], s["rows_affected"],
              json.dumps(s["before"]) if s["before"] else None, json.dumps(s["after"]) if s["after"] else None) for s in steps.steps])
    conn.execute(
        "UPDATE etl.run_log SET finished_at=now(), status='success', rows_read=%s, rows_rejected=%s, rows_transformed=%s, rows_loaded=%s, rows_in_table_after=%s, runtime_s=%s WHERE run_id=%s",
        (len(raw), counts["rejected"], counts["accepted"], len(rows), n_table, round(time.time() - t0, 2), run_id))
    conn.commit()
    conn.close()
    return dict(run_id=run_id, **counts, loaded=len(rows), rows_in_table=n_table)


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
