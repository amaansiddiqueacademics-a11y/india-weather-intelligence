"""OLAP operations and predefined analytical SQL.

All SQL is assembled from whitelisted fragments; user input only ever reaches the
database as bound parameters. Never exposes free-form SQL.
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
import psycopg

from . import db

# ------------------------------------------------------------------ OLAP
TIME_LEVELS = {  # level -> (select/group expressions, label)
    "all": [], "year": ["m.year"], "season": ["m.imd_season"], "year_season": ["m.year", "m.imd_season"],
    "month": ["m.month"], "year_month": ["m.year", "m.month"], "day": ["m.full_date"],
}
GEO_LEVELS = {"all": [], "region": ["l.region"], "state": ["l.state"], "city": ["l.city"]}
TIME_PARENT = {"year": "all", "season": "all", "year_season": "year", "month": "season", "year_month": "year", "day": "year_month"}
MEASURES = {
    "avg_temp_c": ("round(avg(m.tmean_c)::numeric, 2)", "Average daily mean temperature (°C)"),
    "avg_tmax_c": ("round(avg(m.tmax_c)::numeric, 2)", "Average daily maximum temperature (°C)"),
    "max_temp_c": ("round(max(m.tmax_c)::numeric, 1)", "Highest hourly temperature (°C)"),
    "min_temp_c": ("round(min(m.tmin_c)::numeric, 1)", "Lowest hourly temperature (°C)"),
    "total_rain_mm": ("round(sum(m.precip_mm)::numeric, 0)", "Total rainfall (mm) over selected days"),
    "rainy_days": ("count(*) FILTER (WHERE m.precip_mm >= 2.5)", "Rainy days (>= 2.5 mm)"),
    "heavy_rain_days": ("count(*) FILTER (WHERE m.precip_mm >= 35.5)", "Heavy-rain days (>= 35.5 mm)"),
    "hot_days": ("count(*) FILTER (WHERE m.tmax_c >= 40)", "Days with maximum >= 40 °C"),
    "avg_humidity_pct": ("round(avg(m.rh_mean)::numeric, 1)", "Average relative humidity (%)"),
    "avg_hi_max_c": ("round(avg(m.hi_max_c)::numeric, 1)", "Average daily peak heat index (°C)"),
    "days": ("count(*)", "Number of days aggregated"),
}


def _where(f: dict) -> tuple[str, dict]:
    clauses, params = [], {}
    if f.get("cities"):
        clauses.append("l.location_id = ANY(%(cities)s)"); params["cities"] = list(f["cities"])
    if f.get("region"):
        clauses.append("l.region = %(region)s"); params["region"] = f["region"]
    if f.get("state"):
        clauses.append("l.state = %(state)s"); params["state"] = f["state"]
    if f.get("year_from") is not None:
        clauses.append("m.year >= %(year_from)s"); params["year_from"] = int(f["year_from"])
    if f.get("year_to") is not None:
        clauses.append("m.year <= %(year_to)s"); params["year_to"] = int(f["year_to"])
    if f.get("seasons"):
        clauses.append("m.imd_season = ANY(%(seasons)s)"); params["seasons"] = list(f["seasons"])
    if f.get("months"):
        clauses.append("m.month = ANY(%(months)s)"); params["months"] = [int(x) for x in f["months"]]
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def aggregate(conn: psycopg.Connection, time_level: str, geo_level: str, measures: list[str], filters: dict | None = None,
              limit: int = 500) -> dict:
    """Group the daily cube by a time level and a geography level (roll-up/drill-down/slice/dice)."""
    if time_level not in TIME_LEVELS or geo_level not in GEO_LEVELS:
        raise ValueError("unknown level")
    measures = [m for m in measures if m in MEASURES] or ["avg_temp_c"]
    dims = TIME_LEVELS[time_level] + GEO_LEVELS[geo_level]
    where, params = _where(filters or {})
    sel = ", ".join([f"{d} AS {d.split('.')[1]}" for d in dims] + [f"{MEASURES[m][0]} AS {m}" for m in measures])
    sql = (f"SELECT {sel}\nFROM star.mv_weather_daily m\nJOIN star.dim_location l USING (location_key)"
           f"{where}" + (f"\nGROUP BY {', '.join(dims)}\nORDER BY {', '.join(str(i + 1) for i in range(len(dims)))}" if dims else "")
           + f"\nLIMIT {int(limit)}")
    t0 = time.time()
    rows = conn.execute(sql, params).fetchall()
    return dict(sql=sql, params=params, rows=_jsonable(rows), columns=list(rows[0].keys()) if rows else [],
                elapsed_ms=round((time.time() - t0) * 1000, 1), measure_labels={m: MEASURES[m][1] for m in measures})


def pivot(conn, row_dim: str, col_dim: str, measure: str, filters: dict | None = None) -> dict:
    """Cross-tabulation: SQL groups by both dimensions, the result is pivoted."""
    dims = {"city": "l.city", "region": "l.region", "year": "m.year", "season": "m.imd_season", "month": "m.month"}
    if row_dim not in dims or col_dim not in dims or row_dim == col_dim or measure not in MEASURES:
        raise ValueError("invalid pivot")
    where, params = _where(filters or {})
    sql = (f"SELECT {dims[row_dim]} AS row_key, {dims[col_dim]} AS col_key, {MEASURES[measure][0]} AS value\n"
           f"FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key){where}\nGROUP BY 1, 2 ORDER BY 1, 2")
    t0 = time.time()
    df = pd.DataFrame(conn.execute(sql, params).fetchall())
    if df.empty:
        return dict(sql=sql, rows=[], columns=[], elapsed_ms=0)
    wide = df.pivot(index="row_key", columns="col_key", values="value").astype(float)
    return dict(sql=sql, params=params, row_label=row_dim, col_labels=[str(c) for c in wide.columns],
                rows=[dict(row=str(i), values=[None if pd.isna(v) else float(v) for v in r]) for i, r in zip(wide.index, wide.to_numpy())],
                elapsed_ms=round((time.time() - t0) * 1000, 1), measure_label=MEASURES[measure][1])


def _jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k: (float(v) if hasattr(v, "as_tuple") else (v.isoformat() if hasattr(v, "isoformat") else v)) for k, v in r.items()})
    return out


# ------------------------------------------------------- predefined questions
QUESTIONS: dict[str, dict[str, Any]] = {
    "rollup_region": dict(
        title="Roll-up: how much rain does each region get, and what is India's total?",
        params=[],
        sql="""SELECT COALESCE(l.region, 'ALL INDIA') AS region, round(sum(m.precip_mm)::numeric / count(DISTINCT (m.location_key, m.year)), 0) AS rain_mm_per_city_year,
       count(DISTINCT l.city) AS cities
FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
GROUP BY ROLLUP (l.region) ORDER BY rain_mm_per_city_year DESC""",
        chart=dict(type="bar", x="region", y="rain_mm_per_city_year")),
    "drilldown_city": dict(
        title="Drill-down: from one city's years to its months",
        params=["city"],
        sql="""SELECT m.month, round(avg(m.tmean_c)::numeric,1) AS avg_temp_c, round(sum(m.precip_mm)::numeric / count(DISTINCT m.year), 0) AS rain_mm_per_year,
       count(*) FILTER (WHERE m.tmax_c >= 40) AS days_over_40c
FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
WHERE l.city = %(city)s GROUP BY m.month ORDER BY m.month""",
        chart=dict(type="bar", x="month", y="rain_mm_per_year")),
    "slice_monsoon": dict(
        title="Slice: only the monsoon season, which city gets the most rain?",
        sql="""SELECT l.city, round(sum(m.precip_mm)::numeric / count(DISTINCT m.year), 0) AS monsoon_rain_mm_per_year,
       count(*) FILTER (WHERE m.precip_mm >= 35.5) AS heavy_rain_days
FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
WHERE m.imd_season = 'Monsoon' GROUP BY l.city ORDER BY monsoon_rain_mm_per_year DESC""",
        chart=dict(type="bar", x="city", y="monsoon_rain_mm_per_year")),
    "dice_summer": dict(
        title="Dice: hot days in the pre-monsoon season, 2015 to 2022, for the five hottest cities",
        sql="""SELECT l.city, m.year, count(*) FILTER (WHERE m.tmax_c >= 40) AS days_over_40c
FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
WHERE m.imd_season = 'Pre-monsoon' AND m.year BETWEEN 2015 AND 2022
  AND l.city IN (SELECT l2.city FROM star.mv_weather_daily m2 JOIN star.dim_location l2 USING (location_key)
                 GROUP BY l2.city ORDER BY count(*) FILTER (WHERE m2.tmax_c >= 40) DESC LIMIT 5)
GROUP BY l.city, m.year ORDER BY l.city, m.year""",
        chart=dict(type="bar", x="year", y="days_over_40c", group="city")),
    "season_temp": dict(
        title="Which cities are hottest in each season?",
        sql="""SELECT l.city, m.imd_season AS season, round(avg(m.tmean_c)::numeric,2) AS avg_temp_c,
       round(avg(m.tmax_c)::numeric,2) AS avg_tmax_c
FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
GROUP BY l.city, m.imd_season ORDER BY m.imd_season, avg_temp_c DESC""",
        chart=dict(type="bar", x="city", y="avg_temp_c", group="season")),
    "annual_trend": dict(
        title="How have temperature and rainfall changed across years?",
        sql="""WITH yearly AS (
  SELECT l.city, m.year, avg(m.tmean_c) AS temp, sum(m.precip_mm) AS rain
  FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
  GROUP BY l.city, m.year)
SELECT city, round((regr_slope(temp, year)*10)::numeric,3) AS temp_trend_c_per_decade,
       round(regr_r2(temp, year)::numeric,3) AS temp_r2,
       round((regr_slope(rain, year)*10)::numeric,0) AS rain_trend_mm_per_decade,
       round(regr_r2(rain, year)::numeric,3) AS rain_r2, count(*) AS years
FROM yearly GROUP BY city ORDER BY temp_trend_c_per_decade DESC""",
        chart=dict(type="bar", x="city", y="temp_trend_c_per_decade")),
    "extreme_frequency": dict(
        title="Which locations experience the most frequent extreme conditions?",
        sql="""SELECT l.city,
       round((count(*) FILTER (WHERE m.tmax_c >= 40)::numeric / count(DISTINCT m.year)),1) AS days_ge_40c_per_year,
       round((count(*) FILTER (WHERE m.hi_max_c >= 41)::numeric / count(DISTINCT m.year)),1) AS danger_heat_index_days_per_year,
       round((count(*) FILTER (WHERE m.precip_mm >= 35.5)::numeric / count(DISTINCT m.year)),2) AS heavy_rain_days_per_year
FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
GROUP BY l.city ORDER BY danger_heat_index_days_per_year DESC""",
        chart=dict(type="bar", x="city", y="danger_heat_index_days_per_year")),
    "heat_month_hour": dict(
        title="How does the heat index vary by month and time of day?",
        params=["city"],
        sql="""SELECT d.month, t.time_key AS hour, round(avg(f.heat_index_c)::numeric,1) AS avg_heat_index_c
FROM star.fact_weather_hourly f
JOIN star.dim_date d USING (date_key) JOIN star.dim_time t USING (time_key)
JOIN star.dim_location l USING (location_key)
WHERE l.city = %(city)s GROUP BY d.month, t.time_key ORDER BY d.month, t.time_key""",
        chart=dict(type="heatmap", x="hour", y="month", z="avg_heat_index_c")),
    "rain_variability": dict(
        title="Which locations have the greatest rainfall variability?",
        sql="""WITH yearly AS (
  SELECT l.city, m.year, sum(m.precip_mm) AS rain
  FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
  GROUP BY l.city, m.year HAVING count(*) >= 360)
SELECT city, round(avg(rain)::numeric,0) AS mean_annual_rain_mm, round(stddev_samp(rain)::numeric,0) AS sd_mm,
       round((stddev_samp(rain)/avg(rain))::numeric,3) AS coefficient_of_variation, count(*) AS full_years
FROM yearly GROUP BY city ORDER BY coefficient_of_variation DESC""",
        chart=dict(type="bar", x="city", y="coefficient_of_variation")),
    "agri_weather": dict(
        title="How do crop yields relate to seasonal rainfall in the supported districts?",
        sql="""WITH wx AS (
  SELECT location_key, year AS ag_year, sum(precip_mm) FILTER (WHERE month BETWEEN 6 AND 9) AS monsoon_rain_mm,
         avg(tmax_c) FILTER (WHERE month BETWEEN 3 AND 5) AS premonsoon_tmax_c
  FROM star.mv_weather_daily GROUP BY location_key, year HAVING count(*) >= 360),
ag AS (
  SELECT a.location_key, c.crop_name, d.year AS ag_year, a.yield_t_ha
  FROM star.fact_agri_production a JOIN star.dim_crop c USING (crop_key)
  JOIN star.dim_crop_season s USING (crop_season_key) JOIN star.dim_date d ON d.date_key = a.year_start_date_key
  WHERE s.season_name = 'Kharif' AND a.yield_t_ha IS NOT NULL)
SELECT l.city, ag.crop_name, count(*) AS years, round(corr(ag.yield_t_ha, wx.monsoon_rain_mm)::numeric,2) AS corr_yield_vs_monsoon_rain
FROM ag JOIN wx USING (location_key, ag_year) JOIN star.dim_location l USING (location_key)
GROUP BY l.city, ag.crop_name HAVING count(*) >= 10 ORDER BY corr_yield_vs_monsoon_rain DESC NULLS LAST LIMIT 25""",
        chart=dict(type="bar", x="crop_name", y="corr_yield_vs_monsoon_rain")),
    "model_errors": dict(
        title="Where do the forecasting models make the largest errors?",
        sql="""SELECT l.city, m.model_name, m.horizon_h, round(e.metric_value::numeric,3) AS mae_c
FROM star.fact_model_evaluation e JOIN star.dim_model m USING (model_key)
JOIN star.dim_location l USING (location_key)
WHERE e.scope='location' AND e.metric_name='mae' AND m.target='temperature_c' AND m.model_name IN ('hgb','persistence')
ORDER BY m.horizon_h, e.metric_value DESC""",
        chart=dict(type="bar", x="city", y="mae_c", group="model_name")),
}


def run_question(conn: psycopg.Connection, qid: str, params: dict | None = None) -> dict:
    q = QUESTIONS[qid]
    p = {k: (params or {}).get(k) for k in q.get("params", [])}
    if q.get("params") == ["city"] and not p["city"]:
        p["city"] = conn.execute("SELECT city FROM star.dim_location ORDER BY location_key LIMIT 1").fetchone()["city"]
    t0 = time.time()
    rows = conn.execute(q["sql"], p).fetchall()
    return dict(id=qid, title=q["title"], sql=q["sql"], params=p, chart=q["chart"], rows=_jsonable(rows),
                columns=list(rows[0].keys()) if rows else [], elapsed_ms=round((time.time() - t0) * 1000, 1),
                interpretation=interpret(qid, rows))


def interpret(qid: str, rows: list[dict]) -> str:
    """Plain-language reading computed from the returned rows (never hard-coded numbers)."""
    if not rows:
        return "No rows returned: the warehouse has no data for this question yet."
    r0, rl = rows[0], rows[-1]
    if qid == "rollup_region":
        tot = next((r for r in rows if r["region"] == "ALL INDIA"), None); part = [r for r in rows if r["region"] != "ALL INDIA"]
        return f"{part[0]['region']} is the wettest region ({part[0]['rain_mm_per_city_year']:.0f} mm per city-year) and {part[-1]['region']} the driest ({part[-1]['rain_mm_per_city_year']:.0f} mm). The ROLLUP row adds the all-India level ({tot['rain_mm_per_city_year']:.0f} mm) without a second query."
    if qid == "drilldown_city":
        wet = max(rows, key=lambda r: r["rain_mm_per_year"]); hot = max(rows, key=lambda r: r["avg_temp_c"])
        return f"Month {wet['month']} is the wettest ({wet['rain_mm_per_year']:.0f} mm in an average year) and month {hot['month']} the warmest ({hot['avg_temp_c']} °C). Drilling from year to month shows what the yearly total hides."
    if qid == "slice_monsoon":
        return f"Slicing to the monsoon only: {r0['city']} gets {r0['monsoon_rain_mm_per_year']:.0f} mm a year, {rl['city']} just {rl['monsoon_rain_mm_per_year']:.0f} mm."
    if qid == "dice_summer":
        tot: dict[str, int] = {}
        for r in rows:
            tot[r["city"]] = tot.get(r["city"], 0) + r["days_over_40c"]
        c, n = max(tot.items(), key=lambda kv: kv[1])
        return f"Dicing to five cities, one season and eight years: {c} had {n} days at or above 40 °C, the most of the group."
    if qid == "season_temp":
        hot = max(rows, key=lambda r: r["avg_temp_c"])
        cold = min(rows, key=lambda r: r["avg_temp_c"])
        return f"{hot['city']} in {hot['season']} is the warmest combination ({hot['avg_temp_c']} °C mean); {cold['city']} in {cold['season']} is the coolest ({cold['avg_temp_c']} °C)."
    if qid == "annual_trend":
        return (f"Fastest warming in this reanalysis record: {r0['city']} ({r0['temp_trend_c_per_decade']} °C/decade, R²={r0['temp_r2']}); "
                f"slowest: {rl['city']} ({rl['temp_trend_c_per_decade']} °C/decade). Rainfall trends have low R² for most cities, so treat them as weak evidence.")
    if qid == "extreme_frequency":
        return f"{r0['city']} averages {r0['danger_heat_index_days_per_year']} days a year with a peak heat index of 41 °C or more, the most of any supported city."
    if qid == "heat_month_hour":
        hot = max(rows, key=lambda r: r["avg_heat_index_c"])
        return f"The highest average heat index ({hot['avg_heat_index_c']} °C) occurs in month {hot['month']} at {hot['hour']:02d}:00 local time."
    if qid == "rain_variability":
        return (f"{r0['city']} has the most variable annual rainfall (coefficient of variation {r0['coefficient_of_variation']}); "
                f"{rl['city']} the least ({rl['coefficient_of_variation']}).")
    if qid == "agri_weather":
        return (f"Strongest positive link between Kharif yield and Jun-Sep rainfall: {r0['crop_name']} in {r0['city']} (r={r0['corr_yield_vs_monsoon_rain']}, {r0['years']} years). "
                "Correlation across a handful of years is not causation; irrigation and technology trends also move yields.")
    if qid == "model_errors":
        worst = max(rows, key=lambda r: r["mae_c"])
        return f"The largest temperature error is {worst['mae_c']} °C for {worst['model_name']} at +{worst['horizon_h']} h in {worst['city']}."
    return ""


# ------------------------------------------------- star vs snowflake comparison
COMPARISONS = {
    "region_season_temp": dict(
        title="Average temperature by region and season",
        star="""SELECT l.region, d.imd_season AS season, round(avg(f.temperature_c)::numeric,2) AS avg_temp_c
FROM star.fact_weather_hourly f
JOIN star.dim_location l USING (location_key)
JOIN star.dim_date d USING (date_key)
GROUP BY l.region, d.imd_season ORDER BY 1, 2""",
        snow="""SELECT r.region_name AS region, s.season_name AS season, round(avg(f.temperature_c)::numeric,2) AS avg_temp_c
FROM snow.fact_weather_hourly f
JOIN snow.dim_location l USING (location_key)
JOIN snow.dim_district di USING (district_key)
JOIN snow.dim_state st USING (state_key)
JOIN snow.dim_region r USING (region_key)
JOIN snow.dim_date d USING (date_key)
JOIN snow.dim_month mo ON mo.month_key = d.month_key
JOIN snow.dim_season s USING (season_key)
GROUP BY r.region_name, s.season_name ORDER BY 1, 2"""),
    "crop_type_yield": dict(
        title="Average yield by crop type and state",
        star="""SELECT l.state, c.crop_type, round(avg(a.yield_t_ha)::numeric,2) AS avg_yield_t_ha
FROM star.fact_agri_production a
JOIN star.dim_location l USING (location_key)
JOIN star.dim_crop c USING (crop_key)
GROUP BY l.state, c.crop_type ORDER BY 1, 2""",
        snow="""SELECT st.state_name AS state, ct.crop_type, round(avg(a.yield_t_ha)::numeric,2) AS avg_yield_t_ha
FROM snow.fact_agri_production a
JOIN snow.dim_district di USING (district_key)
JOIN snow.dim_state st USING (state_key)
JOIN snow.dim_crop c USING (crop_key)
JOIN snow.dim_crop_type ct USING (crop_type_key)
GROUP BY st.state_name, ct.crop_type ORDER BY 1, 2"""),
    "city_year_rain": dict(
        title="Total rainfall by city and year",
        star="""SELECT l.city, d.year, round(sum(f.precipitation_mm::numeric),0) AS rain_mm
FROM star.fact_weather_hourly f
JOIN star.dim_location l USING (location_key)
JOIN star.dim_date d USING (date_key)
GROUP BY l.city, d.year ORDER BY 1, 2""",
        snow="""SELECT l.city, d.year_key AS year, round(sum(f.precipitation_mm::numeric),0) AS rain_mm
FROM snow.fact_weather_hourly f
JOIN snow.dim_location l USING (location_key)
JOIN snow.dim_date d USING (date_key)
GROUP BY l.city, d.year_key ORDER BY 1, 2"""),
}


def compare_schemas(conn: psycopg.Connection, cid: str) -> dict:
    c = COMPARISONS[cid]
    out: dict[str, Any] = {"id": cid, "title": c["title"]}
    results = {}
    for kind in ("star", "snow"):
        t0 = time.time()
        rows = _jsonable(conn.execute(c[kind]).fetchall())
        out[kind] = dict(sql=c[kind], elapsed_ms=round((time.time() - t0) * 1000, 1), n_rows=len(rows),
                         joins=c[kind].upper().count("JOIN "), rows=rows[:60])
        results[kind] = [tuple(r.values()) for r in rows]
    out["results_identical"] = results["star"] == results["snow"]
    return out
