"""Evidence endpoints for the Data pipeline page: real source-file profiles, a live stress test of the
actual ETL functions, reconciliation against the warehouse, flagged-reading analysis and source code."""
from __future__ import annotations

import inspect
import io
from typing import Any

import numpy as np
import pandas as pd

from . import config, db, derived, etl_weather as ew

RAW = config.DATA_RAW / "openmeteo"


def _loc_city() -> dict[str, dict]:
    with db.connect() as c:
        return {r["location_id"]: r for r in c.execute("SELECT location_key, location_id, city, state FROM star.dim_location ORDER BY location_id").fetchall()}


def inventory() -> dict:
    """Every raw file: rows (data lines minus 3 header lines) and size, as a city x year grid."""
    cities = _loc_city()
    files = []
    for p in sorted(RAW.glob("*.csv")):
        loc, yr = p.stem.split("_")
        with p.open("rb") as f:
            n = sum(1 for _ in f) - 4  # 2 metadata lines + blank + header line
        files.append(dict(location_id=loc, city=cities.get(loc, {}).get("city", loc), year=int(yr), rows=n, kb=round(p.stat().st_size / 1024)))
    return dict(files=files, years=sorted({f["year"] for f in files}), cities=[dict(location_id=k, city=v["city"]) for k, v in cities.items()])


def _hist(s: pd.Series, bins: int = 18) -> dict:
    s = s.dropna()
    if s.empty:
        return dict(x=[], y=[])
    cnt, edges = np.histogram(s, bins=bins)
    return dict(x=[round(float((edges[i] + edges[i + 1]) / 2), 3) for i in range(bins)], y=[int(v) for v in cnt])


def source_profile(city: str, year: int) -> dict:
    """Open one real CSV and describe it like an analyst would: shape, raw rows, per-column stats, monthly shape."""
    path = RAW / f"{city}_{year}.csv"
    if not path.exists():
        raise ValueError(f"no file for {city} {year}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    df, meta, bad_units = ew.parse_raw_csv(path, city)
    raw_cols = lines[3].split(",")
    wh_cols = ["time"] + [c for c in df.columns if c in ew.VALUE_COLS]
    df["obs_time"] = pd.to_datetime(df["time"])
    stats = []
    for raw, wh in zip(raw_cols[1:], wh_cols[1:]):
        s = df[wh]
        stats.append(dict(raw=raw, column=wh, non_null=int(s.notna().sum()), missing=int(s.isna().sum()), min=round(float(s.min()), 2), mean=round(float(s.mean()), 2),
                          p50=round(float(s.median()), 2), max=round(float(s.max()), 2), std=round(float(s.std()), 2), hist=_hist(s),
                          limit=list(ew.HARD_RANGES[wh]), inside=bool(s.min() >= ew.HARD_RANGES[wh][0] and s.max() <= ew.HARD_RANGES[wh][1])))
    monthly = df.groupby(df.obs_time.dt.month).agg(temp=("temperature_c", "mean"), rain=("precipitation_mm", "sum"), hum=("relative_humidity_pct", "mean")).round(2)
    hot = df.loc[df.temperature_c.idxmax()]
    wet = df.groupby(df.obs_time.dt.date).precipitation_mm.sum().sort_values(ascending=False).head(1)
    with db.connect() as c:
        w = c.execute("""SELECT count(*) AS rows, count(*) FILTER (WHERE is_outlier) AS flagged FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
            WHERE l.location_id=%s AND f.date_key BETWEEN %s AND %s""", (city, year * 10000 + 101, year * 10000 + 1231)).fetchone()
    expected = 8784 if year % 4 == 0 else 8760
    facts = [f"{len(df):,} hourly rows = {expected / 24:.0f} days x 24 hours" + (" (complete: no hour missing)" if len(df) == expected else f" (expected {expected:,}, so {expected - len(df)} hours are missing)"),
             f"Hottest hour: {hot.temperature_c:.1f} °C on {hot.obs_time:%d %b} at {hot.obs_time:%H:%M}",
             f"Wettest day: {wet.iloc[0]:.1f} mm on {pd.Timestamp(wet.index[0]):%d %b}",
             f"{int(df.precipitation_mm.gt(0).sum()):,} of {len(df):,} hours had measurable rain ({100 * df.precipitation_mm.gt(0).mean():.1f}%)"]
    head = df.head(14)
    raw_rows = [lines[i].split(",") for i in range(4, 18)]
    return dict(file=path.name, size_kb=round(path.stat().st_size / 1024), meta=meta, raw_header=raw_cols, raw_rows=raw_rows, wh_header=wh_cols, mapped_rows=head[wh_cols[1:]].round(2).values.tolist(),
                n_rows=len(df), n_cols=len(raw_cols), expected_rows=expected, unit_mismatches=bad_units, stats=stats, facts=facts,
                monthly=dict(month=list(range(1, 13)), temp=monthly.temp.tolist(), rain=monthly.rain.tolist(), hum=monthly.hum.tolist()),
                warehouse=dict(rows=w["rows"], flagged=w["flagged"], match=w["rows"] == len(df)))


def stress_test() -> dict:
    """Take 3,000 real rows, plant known defects, and run the REAL pipeline functions. Nothing is simulated except the defects."""
    df, _, _ = ew.parse_raw_csv(RAW / "DEL_2010.csv", "DEL")
    df = df.iloc[4000:7000].reset_index(drop=True)
    plan: list[dict] = []
    t_mid = df.index[100]
    df.loc[t_mid, "temperature_c"] = 999.0
    plan.append(dict(case="Temperature of 999 °C", planted=f"row {t_mid}: temperature_c = 999", stage="validate", rule="Possible values (-90 to 60 °C)"))
    df.loc[200, "relative_humidity_pct"] = 140.0
    plan.append(dict(case="Humidity of 140 %", planted="row 200: relative_humidity_pct = 140", stage="validate", rule="Possible values (0 to 100 %)"))
    df.loc[300, "time"] = "not-a-date"
    plan.append(dict(case="Unreadable timestamp", planted="row 300: time = 'not-a-date'", stage="validate", rule="Readable time"))
    df = pd.concat([df, df.iloc[[400]]], ignore_index=True)
    plan.append(dict(case="Same city-hour twice", planted="row 400 copied to the end", stage="validate", rule="Unique city-hour"))
    gap_rows = [500, 501]
    t_before, t_after = pd.Timestamp(df.loc[499, "time"]), pd.Timestamp(df.loc[502, "time"])
    long_rows = list(range(700, 706))
    n0 = len(df)
    df = df.drop(index=gap_rows + long_rows).reset_index(drop=True)
    plan.append(dict(case="2-hour hole in the record", planted="2 hourly rows deleted", stage="clean", rule="Gap fill (≤ 3 h)"))
    plan.append(dict(case="6-hour hole in the record", planted="6 hourly rows deleted", stage="clean", rule="Gap detected, left empty (> 3 h)"))
    sp = df.index[1500]
    spike_before = float(df.loc[sp, "wind_speed_kmh"])
    df.loc[sp, "wind_speed_kmh"] = 140.0
    plan.append(dict(case="Wind spike of 140 km/h", planted=f"row {sp}: wind_speed_kmh {spike_before:.1f} -> 140", stage="clean", rule="Unusual reading flagged, kept"))

    total = len(df)
    ok, bad_ts = ew.validate_timestamps(df)
    ok, bad_dup = ew.drop_duplicates(ok)
    ok, bad_rng = ew.check_ranges(ok)
    grid, added = ew.fill_gaps(ok)
    filled, cells, imputed_rows = ew.impute_short_gaps(grid)
    flagged, counts = ew.flag_outliers(filled)
    still_empty = int(filled.temperature_c.isna().sum())
    res = {
        "Temperature of 999 °C": (bad_rng[bad_rng.column_name == "temperature_c"].shape[0] >= 1, f"{bad_rng[bad_rng.column_name == 'temperature_c'].shape[0]} row quarantined: “{bad_rng[bad_rng.column_name == 'temperature_c'].reason.iloc[0] if len(bad_rng[bad_rng.column_name == 'temperature_c']) else '-'}”"),
        "Humidity of 140 %": (bad_rng[bad_rng.column_name == "relative_humidity_pct"].shape[0] >= 1, f"{bad_rng[bad_rng.column_name == 'relative_humidity_pct'].shape[0]} row quarantined"),
        "Unreadable timestamp": (len(bad_ts) == 1, f"{len(bad_ts)} row quarantined: “unparseable timestamp”"),
        "Same city-hour twice": (len(bad_dup) == 1, f"{len(bad_dup)} duplicate quarantined, first copy kept"),
        "2-hour hole in the record": (added >= 8 and bool(filled.loc[filled.obs_time.between(t_before, t_after), "is_imputed"].sum() == 2), f"hours re-created on the calendar, then {imputed_rows - 0} rows filled by a straight line"),
        "6-hour hole in the record": (still_empty == 6, f"all {still_empty} hours stay empty: too long to guess honestly"),
        "Wind spike of 140 km/h": (bool(flagged.loc[flagged.wind_speed_kmh == 140.0, "is_outlier"].any()), "row flagged (is_outlier = true) and kept in the data"),
    }
    cases = [dict(**p, caught=res[p["case"]][0], result=res[p["case"]][1]) for p in plan]
    # a real interpolated example: show hour-by-hour values around the 2-hour gap
    ex = filled[(filled.obs_time >= t_before) & (filled.obs_time <= t_after)][["obs_time", "temperature_c", "is_imputed"]]
    example = [dict(time=f"{r.obs_time:%d %b %H:%M}", temperature_c=round(float(r.temperature_c), 2), filled=bool(r.is_imputed)) for r in ex.itertuples()]
    return dict(sample_rows=total, planted=len(plan), caught=sum(c["caught"] for c in cases), cases=cases, interpolation_example=example,
                counts=dict(rejected_timestamp=len(bad_ts), rejected_duplicate=len(bad_dup), rejected_range=len(bad_rng), hours_added=added, cells_filled=cells, flagged_total=int(flagged.is_outlier.sum())),
                ranges={k: list(v) for k, v in ew.HARD_RANGES.items()}, source="data/raw/openmeteo/DEL_2010.csv rows 4000-7000 + planted defects")


def reconciliation() -> dict:
    """File rows vs warehouse rows per city, duplicate-key check and expected-size check, all queried live."""
    inv = inventory()
    per_file: dict[str, int] = {}
    for f in inv["files"]:
        per_file[f["location_id"]] = per_file.get(f["location_id"], 0) + f["rows"]
    with db.connect() as c:
        rows = c.execute("""SELECT l.location_id, l.city, count(*) AS wh_rows, count(DISTINCT (f.date_key, f.time_key)) AS distinct_hours, min(f.date_key) AS first_day, max(f.date_key) AS last_day,
                count(*) FILTER (WHERE f.is_outlier) AS flagged, count(*) FILTER (WHERE f.is_imputed) AS imputed
            FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) GROUP BY 1, 2 ORDER BY 1""").fetchall()
        runs = c.execute("SELECT run_id, pipeline, rows_read, rows_loaded, rows_in_table_after, started_at FROM etl.run_log WHERE pipeline='weather' AND status='success' ORDER BY run_id").fetchall()
        quar = c.execute("SELECT count(*) AS n FROM etl.quarantine").fetchone()["n"]
    cities = [dict(location_id=r["location_id"], city=r["city"], file_rows=per_file.get(r["location_id"], 0), wh_rows=r["wh_rows"], distinct=r["distinct_hours"], match=per_file.get(r["location_id"]) == r["wh_rows"] == r["distinct_hours"],
                   first_day=r["first_day"], last_day=r["last_day"], flagged=r["flagged"], imputed=r["imputed"]) for r in rows]
    tot_files, tot_wh = sum(c["file_rows"] for c in cities), sum(c["wh_rows"] for c in cities)
    loaded_total = sum(r["rows_loaded"] for r in runs)
    return dict(cities=cities, files=len(inv["files"]), file_rows=tot_files, wh_rows=tot_wh, duplicates=sum(c["wh_rows"] - c["distinct"] for c in cities), quarantined=quar,
                runs=[dict(run_id=r["run_id"], rows_read=r["rows_read"], rows_loaded=r["rows_loaded"], table_after=r["rows_in_table_after"], when=r["started_at"]) for r in runs], loaded_total_all_runs=loaded_total)


def flagged() -> dict:
    """Where the 'flagged, kept' readings are: by city x variable, by month, and real examples with their normal value."""
    with db.connect() as c:
        grid = c.execute("""SELECT l.city, v.var, count(*) AS n FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
            CROSS JOIN (VALUES ('temperature_c'),('pressure_msl_hpa'),('wind_speed_kmh')) v(var)
            WHERE f.is_outlier AND f.qc_note LIKE '%%' || v.var || '%%' GROUP BY 1, 2""").fetchall()
        month = c.execute("""SELECT d.month, v.var, count(*) AS n FROM star.fact_weather_hourly f JOIN star.dim_date d USING (date_key)
            CROSS JOIN (VALUES ('temperature_c'),('pressure_msl_hpa'),('wind_speed_kmh')) v(var)
            WHERE f.is_outlier AND f.qc_note LIKE '%%' || v.var || '%%' GROUP BY 1, 2 ORDER BY 1""").fetchall()
        tot = c.execute("SELECT count(*) AS n, count(*) FILTER (WHERE is_outlier) AS f FROM star.fact_weather_hourly").fetchone()
        ex = c.execute("""WITH pick AS (SELECT f.location_key, f.date_key, f.time_key, f.temperature_c, f.pressure_msl_hpa, f.wind_speed_kmh, f.qc_note, d.month, d.full_date
                FROM star.fact_weather_hourly f JOIN star.dim_date d USING (date_key) WHERE f.is_outlier ORDER BY md5(f.date_key::text || f.location_key::text) LIMIT 400)
            SELECT l.city, p.full_date, p.time_key AS hour, p.qc_note, p.temperature_c, p.pressure_msl_hpa, p.wind_speed_kmh,
                (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY w.wind_speed_kmh) FROM star.fact_weather_hourly w JOIN star.dim_date dd USING (date_key)
                    WHERE w.location_key = p.location_key AND dd.month = p.month AND w.time_key = p.time_key) AS typical_wind,
                (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY w.temperature_c) FROM star.fact_weather_hourly w JOIN star.dim_date dd USING (date_key)
                    WHERE w.location_key = p.location_key AND dd.month = p.month AND w.time_key = p.time_key) AS typical_temp
            FROM pick p JOIN star.dim_location l USING (location_key) ORDER BY p.full_date LIMIT 8""").fetchall()
    cities = sorted({r["city"] for r in grid})
    out_ex = []
    for r in ex:
        var = "wind_speed_kmh" if "wind" in r["qc_note"] else "temperature_c" if "temperature" in r["qc_note"] else "pressure_msl_hpa"
        out_ex.append(dict(city=r["city"], when=f"{r['full_date']:%d %b %Y} {r['hour']:02d}:00", variable=var.replace("_", " "), value=float(r[var] if var != "pressure_msl_hpa" else r[var]),
                           typical=round(float(r["typical_wind"] if var == "wind_speed_kmh" else r["typical_temp"]), 1) if var != "pressure_msl_hpa" else None))
    return dict(cities=cities, grid=grid, by_month=month, total_hours=tot["n"], flagged_hours=tot["f"], examples=out_ex)


# ------------------------------------------------------------------ code
MODULES = [
    ("etl_weather.py", "The ETL itself: reads CSVs, validates, cleans, derives, loads.", ew, [
        ("extract", "extract", "Loop over every CSV and stack them into one table"), ("parse_raw_csv", "extract", "Read one file, rename columns to warehouse names"), ("validate_timestamps", "validate", "Reject unreadable times"),
        ("drop_duplicates", "validate", "Keep first copy of a city-hour"), ("check_ranges", "validate", "Reject impossible values"),
        ("fill_gaps", "clean", "Rebuild the full hourly calendar"), ("impute_short_gaps", "clean", "Fill gaps of 3 hours or less"),
        ("flag_outliers", "clean", "Flag unusual readings, never delete"), ("add_derived", "transform", "Heat index, wet-bulb, date and hour keys"),
        ("quality_rows", "clean", "Per city and measure: missing count, min, mean, max"), ("col_stats", "clean", "Before/after statistics logged for each step"),
        ("load_facts", "load", "Bulk copy to staging, then upsert"), ("run", "load", "Runs all five stages in order and writes the run log")]),
    ("derived.py", "The two formulas behind the “feels like” columns.", derived, [("heat_index_c", "transform", "Rothfusz heat-index regression"), ("wet_bulb_c", "transform", "Stull wet-bulb approximation"), ("heat_category", "transform", "Label a heat index: caution, danger..."), ("imd_season", "transform", "Month to Indian season")]),
]


def code() -> list[dict]:
    out = []
    for fname, role, mod, funcs in MODULES:
        path = config.ROOT / "src" / "wx" / fname
        out.append(dict(file=f"src/wx/{fname}", role=role, lines=len(path.read_text(encoding="utf-8").splitlines()),
                        functions=[dict(name=n, stage=s, summary=d, source=inspect.getsource(getattr(mod, n))) for n, s, d in funcs],
                        constants=[f"{k} = {getattr(mod, k)!r}" for k in ("COLUMN_MAP", "HARD_RANGES", "MAX_GAP_H", "INTERP_COLS", "OUTLIER_Z", "OUTLIER_COLS", "FACT_COLS") if hasattr(mod, k)]))
    return out
