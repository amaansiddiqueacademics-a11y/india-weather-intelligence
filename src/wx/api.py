"""FastAPI backend. Reads the warehouse only; the single outbound call is the clearly labelled live-conditions lookup."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, etl_proof, models_viz, olap, predict as predict_mod, wh_explore

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _warm_etl_evidence()  # defined below; resolved when the server starts
    yield


app = FastAPI(title="India Weather Intelligence API", version="1.0", lifespan=lifespan,
              description="Warehouse-backed analytics, OLAP, forecasting and risk modules for 10 Indian cities.")
_cache: dict[str, tuple[float, Any]] = {}


def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def conn():
    return db.connect()


def clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if hasattr(o, "as_tuple"):
        return float(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


_LOCS = pd.read_csv(config.LOCATIONS_CSV)
KNOWN_CITIES = set(_LOCS.location_id) | set(_LOCS.city)


@app.middleware("http")
async def reject_unknown_city(request: Request, call_next):
    """Unknown `city`/`location_id` -> 404 for every endpoint, instead of an empty 200 or a 500."""
    if request.url.path.startswith("/api/"):
        for name in ("city", "location_id"):
            v = request.query_params.get(name)
            if v and v not in KNOWN_CITIES:
                return JSONResponse(status_code=404, content={"detail": f"unknown city '{v}'; use one of {sorted(_LOCS.location_id)}"})
    return await call_next(request)


@app.exception_handler(predict_mod.PredictionError)
async def prediction_error(_, exc):
    return JSONResponse(status_code=422, content={"error": "prediction_unavailable", "detail": str(exc)})


@app.exception_handler(ValueError)
async def value_error(_, exc):
    return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": str(exc)})


# ----------------------------------------------------------------- meta
@app.get("/api/meta")
def meta():
    with conn() as c:
        locs = c.execute("""SELECT location_id, city, state, region, district, latitude, longitude, grid_latitude, grid_longitude, elevation_m, agri_district,
            (SELECT min(full_date) FROM star.fact_weather_hourly f JOIN star.dim_date d USING (date_key) WHERE f.location_key=l.location_key) AS first_day,
            (SELECT max(full_date) FROM star.fact_weather_hourly f JOIN star.dim_date d USING (date_key) WHERE f.location_key=l.location_key) AS last_day,
            (SELECT count(*) FROM star.fact_weather_hourly f WHERE f.location_key=l.location_key) AS hourly_rows
            FROM star.dim_location l ORDER BY city""").fetchall()
        tot = c.execute("SELECT (SELECT count(*) FROM star.fact_weather_hourly) AS weather_rows, (SELECT count(*) FROM star.fact_agri_production) AS agri_rows").fetchone()
    return clean(dict(locations=locs, totals=tot, data_note="ERA5-based reanalysis via Open-Meteo (gridded estimates, not station observations)."))


@app.get("/api/health")
def health():
    with conn() as c:
        c.execute("SELECT 1")
    return {"status": "ok"}


# ------------------------------------------------------------- overview
@app.get("/api/overview")
def overview():
    def build():
        with conn() as c:
            stripes = c.execute("""SELECT l.city, m.year, round(avg(m.tmean_c)::numeric,2) AS temp
                FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key) GROUP BY l.city, m.year HAVING count(*) >= 360 ORDER BY 1, 2""").fetchall()
            cities = c.execute("""SELECT l.location_id, l.city, l.state, l.latitude, l.longitude, round(avg(m.tmean_c)::numeric,1) AS mean_temp_c,
                round((sum(m.precip_mm)/count(DISTINCT m.year))::numeric,0) AS annual_rain_mm,
                round((count(*) FILTER (WHERE m.hi_max_c >= 41)::numeric/count(DISTINCT m.year)),1) AS danger_heat_days_per_year,
                round((count(*) FILTER (WHERE m.precip_mm >= 64.5)::numeric/count(DISTINCT m.year)),2) AS heavy_rain_days_per_year
                FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key) GROUP BY l.location_id, l.city, l.state, l.latitude, l.longitude ORDER BY l.city""").fetchall()
            monthly = c.execute("""SELECT m.month, round(avg(m.tmean_c)::numeric,2) AS temp, round((sum(m.precip_mm)/count(DISTINCT (m.location_key, m.year)))::numeric,1) AS rain
                FROM star.mv_weather_daily m GROUP BY m.month ORDER BY 1""").fetchall()
        findings = []
        if cities:
            hot = max(cities, key=lambda r: r["danger_heat_days_per_year"]); wet = max(cities, key=lambda r: r["annual_rain_mm"]); dry = min(cities, key=lambda r: r["annual_rain_mm"])
            findings = [
                dict(text=f"{hot['city']} has the most days per year with a peak heat index of 41 °C or higher ({hot['danger_heat_days_per_year']}).", link="olap", why="star.mv_weather_daily, hi_max_c >= 41"),
                dict(text=f"{wet['city']} is the wettest supported city ({wet['annual_rain_mm']} mm/year); {dry['city']} is the driest ({dry['annual_rain_mm']} mm/year).", link="history", why="sum(precip_mm)/years"),
            ]
        return clean(dict(stripes=stripes, cities=cities, monthly=monthly, findings=findings))
    return cached("overview", 600, build)


@app.get("/api/live")
def live(city: str):
    """External-service snapshot (Open-Meteo forecast API), cached 10 min, clearly labelled as external."""
    with conn() as c:
        loc = c.execute("SELECT latitude, longitude, city FROM star.dim_location WHERE location_id=%s OR city=%s", (city, city)).fetchone()
    if not loc:
        raise HTTPException(404, "unknown city")

    def fetch():
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=12, headers={"User-Agent": config.USER_AGENT}, params=dict(
            latitude=loc["latitude"], longitude=loc["longitude"], timezone="Asia/Kolkata", forecast_days=7,
            current="temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m,is_day",
            hourly="temperature_2m,precipitation_probability,weather_code,visibility,uv_index",
            daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,sunrise,sunset,uv_index_max"))
        r.raise_for_status()
        j = r.json()
        return dict(source="Open-Meteo forecast API (external service, not our model)", retrieved_at=datetime.now().astimezone().isoformat(), city=loc["city"],
                    current=j["current"], units=j["current_units"], hourly=j["hourly"], daily=j["daily"])
    try:
        return cached(f"live:{city}", 600, fetch)
    except Exception as exc:  # network failure must not break the dashboard
        return JSONResponse(status_code=503, content={"error": "external_unavailable", "detail": f"Live lookup failed: {exc.__class__.__name__}"})


@app.get("/api/live/air")
def live_air(city: str):
    """External air-quality snapshot (Open-Meteo air-quality API), cached 30 min."""
    with conn() as c:
        loc = c.execute("SELECT latitude, longitude FROM star.dim_location WHERE location_id=%s OR city=%s", (city, city)).fetchone()
    if not loc:
        raise HTTPException(404, "unknown city")

    def fetch():
        r = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", timeout=12, headers={"User-Agent": config.USER_AGENT},
                         params=dict(latitude=loc["latitude"], longitude=loc["longitude"], timezone="Asia/Kolkata", current="us_aqi,pm2_5,pm10"))
        r.raise_for_status()
        return dict(source="Open-Meteo air-quality API (CAMS model estimate, external service)", current=r.json()["current"])
    try:
        return cached(f"air:{city}", 1800, fetch)
    except Exception as exc:
        return JSONResponse(status_code=503, content={"error": "external_unavailable", "detail": f"Air-quality lookup failed: {exc.__class__.__name__}"})


@app.get("/api/normal")
def normal(city: str, month: int, day: int, hour: int):
    """What is normal for this date and hour in the warehouse history (2002-2022)?"""
    with conn() as c:
        daily = c.execute("""SELECT round(avg(m.tmax_c)::numeric,1) AS tmax_mean, round(avg(m.tmin_c)::numeric,1) AS tmin_mean, round(stddev_samp(m.tmax_c)::numeric,2) AS tmax_sd,
                round(avg(m.precip_mm)::numeric,2) AS rain_mean_mm, round((100.0*count(*) FILTER (WHERE m.precip_mm >= 2.5)/count(*))::numeric,0) AS rainy_day_pct,
                count(DISTINCT m.year) AS years
            FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
            WHERE l.location_id=%s AND abs(extract(doy from m.full_date) - extract(doy from make_date(2021, %s, %s))) <= 3""", (city, month, day)).fetchone()
        hourly = c.execute("""SELECT round(avg(f.temperature_c)::numeric,1) AS temp_mean, round(stddev_samp(f.temperature_c)::numeric,2) AS temp_sd, round(avg(f.heat_index_c)::numeric,1) AS hi_mean
            FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) JOIN star.dim_date d USING (date_key)
            WHERE l.location_id=%s AND d.month=%s AND abs(d.day - %s) <= 7 AND f.time_key=%s""", (city, month, day, hour)).fetchone()
    return clean(dict(daily=daily, hourly=hourly, basis="warehouse history 2002-2022, +/-3 days (daily) and +/-7 days at this hour (hourly)"))


# -------------------------------------------------------------- history
GRAINS = {"daily": ("m.full_date", "m.full_date"), "monthly": ("date_trunc('month', m.full_date)::date", "1"), "annual": ("m.year", "1")}


@app.get("/api/history")
def history(city: str, start: int = 2002, end: int = 2022, grain: str = "monthly"):
    if grain not in GRAINS:
        raise ValueError("grain must be daily, monthly or annual")
    key = GRAINS[grain][0]
    with conn() as c:
        rows = c.execute(f"""SELECT {key} AS period, round(avg(m.tmean_c)::numeric,2) AS temp_mean_c, round(max(m.tmax_c)::numeric,1) AS temp_max_c, round(min(m.tmin_c)::numeric,1) AS temp_min_c,
            round(sum(m.precip_mm)::numeric,1) AS rain_mm, round(avg(m.rh_mean)::numeric,1) AS humidity_pct, round(avg(m.wind_mean_kmh)::numeric,1) AS wind_kmh,
            round(avg(m.pressure_mean_hpa)::numeric,1) AS pressure_hpa, round(max(m.hi_max_c)::numeric,1) AS heat_index_max_c
            FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key)
            WHERE l.location_id=%s AND m.year BETWEEN %s AND %s GROUP BY 1 ORDER BY 1""", (city, start, end)).fetchall()
    return clean(dict(grain=grain, rows=rows))


@app.get("/api/history/distribution")
def distribution(city: str, variable: str = "temperature_c", bins: int = 30):
    cols = {"temperature_c", "relative_humidity_pct", "pressure_msl_hpa", "wind_speed_kmh", "heat_index_c"}
    if variable not in cols:
        raise ValueError(f"variable must be one of {sorted(cols)}")
    with conn() as c:
        st = c.execute(f"""SELECT count({variable}) AS n, min({variable}) AS lo, max({variable}) AS hi, avg({variable}) AS mean, stddev_samp({variable}) AS sd,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY {variable}) AS median, percentile_cont(0.05) WITHIN GROUP (ORDER BY {variable}) AS p05,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY {variable}) AS p95 FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) WHERE l.location_id=%s""", (city,)).fetchone()
        h = c.execute(f"""SELECT width_bucket({variable}, %s, %s + 0.0001, %s) AS b, count(*) AS n FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
            WHERE l.location_id=%s GROUP BY 1 ORDER BY 1""", (st["lo"], st["hi"], bins, city)).fetchall()
    w = (st["hi"] - st["lo"]) / bins
    return clean(dict(stats=st, histogram=[dict(x=st["lo"] + (r["b"] - 0.5) * w, n=r["n"]) for r in h]))


@app.get("/api/history/on-this-day")
def on_this_day(city: str, month: int, day: int):
    with conn() as c:
        rows = c.execute("""SELECT m.year, round(m.tmax_c::numeric,1) AS tmax_c, round(m.tmin_c::numeric,1) AS tmin_c, round(m.precip_mm::numeric,1) AS rain_mm
            FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key) WHERE l.location_id=%s AND m.month=%s AND extract(day from m.full_date)=%s ORDER BY 1""", (city, month, day)).fetchall()
    return clean(dict(rows=rows))


@app.get("/api/history/records")
def records(city: str):
    """Extreme days on record for a city (daily aggregates, 2002-2022)."""
    q = """WITH d AS (SELECT m.* FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key) WHERE l.location_id=%s AND m.n_hours=24)
        SELECT * FROM (
         (SELECT 'Hottest day' AS label, full_date, round(tmax_c::numeric,1) AS value, 'degC' AS unit FROM d ORDER BY tmax_c DESC LIMIT 1)
         UNION ALL (SELECT 'Coldest night', full_date, round(tmin_c::numeric,1), 'degC' FROM d ORDER BY tmin_c ASC LIMIT 1)
         UNION ALL (SELECT 'Wettest day', full_date, round(precip_mm::numeric,1), 'mm' FROM d ORDER BY precip_mm DESC LIMIT 1)
         UNION ALL (SELECT 'Peak heat index', full_date, round(hi_max_c::numeric,1), 'degC' FROM d ORDER BY hi_max_c DESC LIMIT 1)
         UNION ALL (SELECT 'Strongest gust', full_date, round(gust_max_kmh::numeric,0), 'km/h' FROM d ORDER BY gust_max_kmh DESC LIMIT 1)) r"""
    with conn() as c:
        rows = c.execute(q, (city,)).fetchall()
    return clean(dict(rows=rows))


def _daily_frame(city: str):
    import pandas as pd
    with conn() as c:
        rows = c.execute("""SELECT m.full_date, m.year, m.month, m.tmean_c, m.tmax_c, m.tmin_c, m.precip_mm, m.hi_max_c, m.gust_max_kmh FROM star.mv_weather_daily m
            JOIN star.dim_location l USING (location_key) WHERE l.location_id=%s AND m.n_hours=24 ORDER BY m.full_date""", (city,)).fetchall()
    if not rows:
        raise HTTPException(404, "no data for this city")
    df = pd.DataFrame(rows)
    df["full_date"] = pd.to_datetime(df["full_date"])
    return df


def _longest_run(mask) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def _wet_window(df, width: int = 4) -> list[int]:
    """The `width` consecutive calendar months (wrapping the year) that hold the most rain: the city's own rainy season."""
    mm = df.groupby("month").precip_mm.sum().reindex(range(1, 13), fill_value=0.0)
    best = max(range(12), key=lambda i: sum(mm.iloc[(i + k) % 12] for k in range(width)))
    return [((best + k) % 12) + 1 for k in range(width)]


@app.get("/api/history/day-cycle")
def history_day_cycle(city: str):
    """Average temperature and humidity at each hour of the day, per IMD season."""
    def build():
        with conn() as c:
            rows = c.execute("""SELECT d.imd_season AS season, f.time_key AS hour, avg(f.temperature_c)::float AS temp, avg(f.relative_humidity_pct)::float AS rh
                FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) JOIN star.dim_date d USING (date_key)
                WHERE l.location_id=%s GROUP BY 1,2 ORDER BY 1,2""", (city,)).fetchall()
        return clean(dict(rows=rows))
    return cached(f"daycycle:{city}", 3600, build)


@app.get("/api/history/detail")
def history_detail(city: str, start: int = 2002, end: int = 2022):
    """Per-year indicators and month-by-month spread of daily temperature. `monsoon_rain` and `dry_spell` use the city's own wettest four months."""
    import numpy as np
    df = _daily_frame(city)
    df = df[(df.year >= start) & (df.year <= end)]
    wet = _wet_window(df)
    annual = []
    for y, g in df.groupby("year"):
        if len(g) < 360:
            continue
        mon = g[g.month.isin(wet)].sort_values("full_date")
        annual.append(dict(year=int(y), tmean=float(g.tmean_c.mean()), tmax_mean=float(g.tmax_c.mean()), tmin_mean=float(g.tmin_c.mean()), rain_mm=float(g.precip_mm.sum()),
                           rainy_days=int((g.precip_mm >= 2.5).sum()), heavy_days=int((g.precip_mm >= 35.5).sum()), hot_days=int((g.tmax_c >= 40).sum()), warm_nights=int((g.tmin_c >= 25).sum()),
                           hi_caution=int(((g.hi_max_c >= 27) & (g.hi_max_c < 32)).sum()), hi_extreme=int(((g.hi_max_c >= 32) & (g.hi_max_c < 41)).sum()), hi_danger=int((g.hi_max_c >= 41).sum()),
                           monsoon_rain=float(mon.precip_mm.sum()), dry_spell=int(_longest_run((mon.precip_mm < 2.5).to_numpy()))))
    box = []
    for m, g in df.groupby("month"):
        q = np.percentile(g.tmean_c, [0, 5, 25, 50, 75, 95, 100])
        box.append(dict(month=int(m), min=float(q[0]), p05=float(q[1]), q1=float(q[2]), median=float(q[3]), q3=float(q[4]), p95=float(q[5]), max=float(q[6]), tmax_median=float(g.tmax_c.median()), tmin_median=float(g.tmin_c.median())))
    return clean(dict(annual=annual, month_box=box, wet_months=wet))


def _runs(mask, dates) -> list[tuple[int, int, int]]:
    """(start_index, end_index, length) of every run of True values."""
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j, j - i + 1))
            i = j + 1
        else:
            i += 1
    return out


@app.get("/api/history/events")
def history_events(city: str, start: int = 2002, end: int = 2022):
    """Dated evidence behind the History cards: hottest days, longest hot streaks, longest dry spells, wettest days and the rainy-season window."""
    import numpy as np
    df = _daily_frame(city)
    df = df[(df.year >= start) & (df.year <= end)].sort_values("full_date").reset_index(drop=True)
    n_years = max(1, df.year.nunique())
    wet = _wet_window(df)
    day = lambda r: r.full_date.strftime("%Y-%m-%d")  # noqa: E731
    hot = df.nlargest(10, "hi_max_c")
    hot_days = [dict(date=day(r), tmax=round(float(r.tmax_c), 1), tmin=round(float(r.tmin_c), 1), feels=round(float(r.hi_max_c), 1), rain=round(float(r.precip_mm), 1)) for r in hot.itertuples()]
    danger = bool((df.hi_max_c >= 41).any())
    thr = 41.0 if danger else 32.0
    mask = (df.hi_max_c >= thr).to_numpy()
    streaks = sorted(_runs(mask, df.full_date), key=lambda t: -t[2])[:5]
    hot_streaks = [dict(start=day(df.iloc[a]), end=day(df.iloc[b]), days=int(n)) for a, b, n in streaks]
    by_month = [dict(month=m, danger=float(((df.month == m) & (df.hi_max_c >= 41)).sum() / n_years), hot=float(((df.month == m) & (df.hi_max_c >= 32) & (df.hi_max_c < 41)).sum() / n_years),
                     tmax_over_40=float(((df.month == m) & (df.tmax_c >= 40)).sum() / n_years)) for m in range(1, 13)]
    # dry spells inside the rainy season, per year, longest first
    spells = []
    for y, g in df[df.month.isin(wet)].groupby("year"):
        g = g.reset_index(drop=True)
        for a, b, n in _runs((g.precip_mm < 2.5).to_numpy(), g.full_date):
            spells.append(dict(year=int(y), start=day(g.iloc[a]), end=day(g.iloc[b]), days=int(n)))
    spells.sort(key=lambda s: -s["days"])
    longest_by_year = sorted({s["year"]: s for s in reversed(spells)}.values(), key=lambda s: s["year"])
    wettest = [dict(date=day(r), mm=round(float(r.precip_mm), 1)) for r in df.nlargest(8, "precip_mm").itertuples()]
    # first and last "real rain week" (7-day total >= 30 mm) around the rainy season, per year
    first_m = wet[0]
    onset, offset = [], []
    for y, g in df.groupby("year"):
        g = g.reset_index(drop=True)
        r7 = g.precip_mm.rolling(7).sum()
        win = g[(g.month >= max(1, first_m - 1)) & (g.month <= min(12, wet[-1] + 1)) & (r7 >= 30)] if wet[0] <= wet[-1] else g[r7 >= 30]
        if len(win):
            onset.append(dict(year=int(y), date=day(win.iloc[0]), doy=int(win.iloc[0].full_date.dayofyear)))
            offset.append(dict(year=int(y), date=day(win.iloc[-1]), doy=int(win.iloc[-1].full_date.dayofyear)))
    return clean(dict(wet_months=wet, danger_exists=danger, threshold=thr, hot_days=hot_days, hot_streaks=hot_streaks, hot_by_month=by_month, dry_spells=spells[:8], longest_by_year=longest_by_year, wettest_days=wettest,
                      onset=onset, offset=offset, median_onset_doy=int(np.median([o["doy"] for o in onset])) if onset else None, median_offset_doy=int(np.median([o["doy"] for o in offset])) if offset else None))


@app.get("/api/history/year-vs-normal")
def year_vs_normal(city: str, year: int, start: int = 2002, end: int = 2022):
    """A chosen year against the day-by-day normal (mean and +/-1 SD of the other years): temperature and cumulative rain."""
    import numpy as np
    df = _daily_frame(city)
    df = df[(df.year >= start) & (df.year <= end)].copy()
    df = df[~((df.full_date.dt.month == 2) & (df.full_date.dt.day == 29))]
    df["key"] = df.full_date.dt.strftime("%m-%d")
    keys = sorted(df.key.unique())
    if year not in set(df.year):
        raise ValueError("year outside the selected range")
    piv_t = df.pivot_table(index="key", columns="year", values="tmean_c").reindex(keys)
    piv_r = df.pivot_table(index="key", columns="year", values="precip_mm").reindex(keys).fillna(0).cumsum()
    others = [y for y in piv_t.columns if y != year]

    def smooth(a, w=7):
        k = np.ones(w) / w
        ext = np.concatenate([a[-w:], a, a[:w]])
        return np.convolve(ext, k, mode="same")[w:-w]
    nt, st = smooth(piv_t[others].mean(axis=1).to_numpy()), smooth(piv_t[others].std(axis=1).to_numpy())
    yt = smooth(piv_t[year].to_numpy())
    nr, sr = piv_r[others].mean(axis=1).to_numpy(), piv_r[others].std(axis=1).to_numpy()
    yr = piv_r[year].to_numpy()
    anom = yt - nt
    w30 = np.convolve(anom, np.ones(30) / 30, mode="valid")
    i = int(np.argmax(np.abs(w30)))
    dates = [f"2001-{k}" for k in keys]
    return clean(dict(year=year, dates=dates, normal_t=nt.tolist(), sd_t=st.tolist(), year_t=yt.tolist(), normal_r=nr.tolist(), sd_r=sr.tolist(), year_r=yr.tolist(),
                      summary=dict(mean_anomaly=float(anom.mean()), strongest_30d=dict(start=dates[i], anomaly=float(w30[i])), rain_pct_of_normal=float(100 * yr[-1] / nr[-1]), rain_year_mm=float(yr[-1]), rain_normal_mm=float(nr[-1]), n_other_years=len(others))))


# ------------------------------------------------------------- risk lab data
@app.get("/api/risk/hazards")
def risk_hazards(city: str):
    """Average number of hazard days per month (and per year) for one city."""
    df = _daily_frame(city)
    n_years = df.year.nunique()
    defs = {"heat_danger": df.hi_max_c >= 41, "very_hot": df.tmax_c >= 40, "heavy_rain": df.precip_mm >= 35.5, "extreme_rain": df.precip_mm >= 64.5, "strong_wind": df.gust_max_kmh >= 60}
    months = []
    for m in range(1, 13):
        sel = df.month == m
        months.append(dict(month=m, **{k: float(v[sel].sum() / n_years) for k, v in defs.items()}))
    return clean(dict(years=int(n_years), months=months, annual={k: float(v.sum() / n_years) for k, v in defs.items()}))


@app.get("/api/risk/events")
def risk_events(city: str):
    """Concrete hazard days for one city: worst heat days, wettest days, longest hot streak, and hazard-day counts per year."""
    df = _daily_frame(city)
    iso = lambda d: d.strftime("%Y-%m-%d")
    hot = df.sort_values("hi_max_c", ascending=False).head(8)
    wet = df.sort_values("precip_mm", ascending=False).head(8)
    very_hot = df.tmax_c >= 40
    streak_len = streak_end = 0
    cur = 0
    for i, v in enumerate(very_hot.to_numpy()):
        cur = cur + 1 if v else 0
        if cur > streak_len:
            streak_len, streak_end = cur, i
    streak = dict(days=int(streak_len), start=iso(df.full_date.iloc[streak_end - streak_len + 1]) if streak_len else None, end=iso(df.full_date.iloc[streak_end]) if streak_len else None)
    yearly = [dict(year=int(y), very_hot=int((g.tmax_c >= 40).sum()), heat_danger=int((g.hi_max_c >= 41).sum()), heavy_rain=int((g.precip_mm >= 35.5).sum()), extreme_rain=int((g.precip_mm >= 64.5).sum()), rain_mm=float(g.precip_mm.sum())) for y, g in df.groupby("year") if len(g) >= 360]
    return clean(dict(hot=[dict(date=iso(r.full_date), tmax=float(r.tmax_c), hi=float(r.hi_max_c), tmin=float(r.tmin_c)) for r in hot.itertuples()],
                      wet=[dict(date=iso(r.full_date), mm=float(r.precip_mm)) for r in wet.itertuples()], streak=streak, yearly=yearly))


_AGRI: dict = {}


def _agri_state():
    """Lazy-load the crop frame and the saved yield models (same features and anomaly baseline as training)."""
    if not _AGRI:
        import joblib
        from . import config
        from .ml import VERSION
        from .ml_agri import TRAIN_MAX, WEATHER_FEATS, build
        with conn() as c:
            df = build(c)
        _AGRI.update(df=df, models=joblib.load(config.MODELS_DIR / f"agri_yield_{VERSION}.joblib"), wtr=df[df.year <= TRAIN_MAX].groupby("location_key")[WEATHER_FEATS].mean(), feats=WEATHER_FEATS)
    return _AGRI


@app.get("/api/models/crop")
def model_crop(city: str, crop: str | None = None, year: int = 2019, rain: float | None = None, dry: float | None = None, hot: float | None = None, last: float | None = None):
    """Predict a district Kharif yield from season weather and last year's yield; any input can be overridden (what-if)."""
    st = _agri_state()
    df = st["df"]
    d = df[(df.city == cityname_for(city)) & (df.season_name == "Kharif") & (df.split == "test")]
    if d.empty:
        raise HTTPException(404, "no crop test rows for this city")
    crops = sorted(d.crop_name.unique().tolist())
    crop = crop if crop in crops else crops[0]
    r = d[(d.crop_name == crop) & (d.year == year)]
    if r.empty:
        raise HTTPException(404, "no row for this crop and year")
    row = r.iloc[0].copy()
    hist = df[(df.location_key == row.location_key) & (df.season_name == "Kharif")].drop_duplicates("year")
    defaults = dict(rain=float(row.kharif_rain_mm), dry=float(row.kharif_dry_spell_d), hot=float(row.kharif_hot_days), last=float(row.lag1_yield))
    for k, col in (("rain", "kharif_rain_mm"), ("dry", "kharif_dry_spell_d"), ("hot", "kharif_hot_days")):
        v = locals()[k]
        if v is not None:
            row[col] = v
    if last is not None:
        row["lag1_yield"] = last
        row["lag1_rel"] = last / row.g_mean
    for c in st["feats"]:
        row[c + "_anom"] = row[c] - st["wtr"].loc[row.location_key, c]
    out = {}
    for name in ("hgb", "ridge"):
        est, feats = st["models"][name]
        out[name] = float(est.predict(row[feats].to_frame().T.astype(float))[0] * row.g_mean)
    rng = lambda col, lo=0.0: [lo, float(hist[col].max() * 1.25)]
    return clean(dict(city=row.city, crop=crop, crops=crops, year=int(year), years=sorted(d.year.unique().tolist()), actual=float(row.yield_t_ha), crop_average=float(row.g_mean), last_year=float(row.lag1_yield), pred=out, defaults=defaults,
                      ranges=dict(rain=rng("kharif_rain_mm"), dry=rng("kharif_dry_spell_d"), hot=rng("kharif_hot_days"), last=[0.0, float(row.g_mean * 2.2)])))


def cityname_for(location_id: str) -> str:
    with conn() as c:
        r = c.execute("SELECT city FROM star.dim_location WHERE location_id=%s", (location_id,)).fetchone()
    if not r:
        raise HTTPException(404, "unknown city")
    return r["city"]


@app.get("/api/risk/crops")
def risk_crops(city: str):
    """Monsoon rain, dry spell and a Kharif yield index (yield / the crop's own average) per year for a crop district city."""
    import pandas as pd
    from .ml_agri import weather_features
    with conn() as c:
        loc = c.execute("SELECT location_key, city FROM star.dim_location WHERE location_id=%s", (city,)).fetchone()
        ag = pd.DataFrame(c.execute("""SELECT cr.crop_name, d.year AS year, a.yield_t_ha FROM star.fact_agri_production a JOIN star.dim_crop cr USING (crop_key)
            JOIN star.dim_crop_season s USING (crop_season_key) JOIN star.dim_date d ON d.date_key=a.year_start_date_key
            WHERE a.location_key=%s AND s.season_name='Kharif' AND a.yield_t_ha>0""", (loc["location_key"],)).fetchall())
        wx = weather_features(c)
    if ag.empty or wx.empty:
        raise HTTPException(404, "no crop data for this city")
    wx = wx[wx.location_key == loc["location_key"]]
    ag["idx"] = 100 * ag.yield_t_ha / ag.groupby("crop_name").yield_t_ha.transform("mean")
    yi = ag.groupby("year").idx.mean()
    df = wx.set_index("year")[["kharif_rain_mm", "kharif_dry_spell_d", "kharif_hot_days"]].join(yi, how="inner").reset_index()
    normal = float(df.kharif_rain_mm.mean())
    df["rain_pct"] = 100 * df.kharif_rain_mm / normal
    return clean(dict(city=loc["city"], normal_rain_mm=normal, n_crops=int(ag.crop_name.nunique()), years=df.to_dict("records")))


@app.get("/api/risk/signals")
def risk_signals(city: str):
    """Early-warning signals per city: heatwave spells, hot nights, 3-day rain accumulation, rain concentration and the longest wet spell."""
    import numpy as np
    df = _daily_frame(city)
    iso = lambda d: d.strftime("%Y-%m-%d")

    def runs(mask):
        out, start = [], None
        for i, v in enumerate(list(mask) + [False]):
            if v and start is None:
                start = i
            elif not v and start is not None:
                out.append((start, i - 1)); start = None
        return out

    p95_tmax, p95_tmin = float(df.tmax_c.quantile(0.95)), float(df.tmin_c.quantile(0.95))
    hot = (df.tmax_c >= p95_tmax).to_numpy()
    spells = [(a, b) for a, b in runs(hot) if b - a + 1 >= 3]
    worst = max(spells, key=lambda r: (r[1] - r[0], df.tmax_c.iloc[r[0]:r[1] + 1].sum())) if spells else None
    df["r3"] = df.precip_mm.rolling(3).sum()
    wet = runs((df.precip_mm >= 2.5).to_numpy())
    wlong = max(wet, key=lambda r: r[1] - r[0]) if wet else None
    yearly = []
    for y, g in df.groupby("year"):
        if len(g) < 360:
            continue
        idx = g.index
        sp = [r for r in spells if df.year.iloc[r[0]] == y]
        top5 = float(g.precip_mm.nlargest(5).sum() / max(g.precip_mm.sum(), 1e-9))
        yearly.append(dict(year=int(y), spells=len(sp), spell_days=int(sum(b - a + 1 for a, b in sp)), hot_nights=int((g.tmin_c >= p95_tmin).sum()), flood3=int((g.r3 >= 100).sum()), top5_share=100 * top5))
    return clean(dict(p95_tmax=p95_tmax, p95_tmin=p95_tmin, yearly=yearly,
        worst_spell=None if worst is None else dict(start=iso(df.full_date.iloc[worst[0]]), end=iso(df.full_date.iloc[worst[1]]), days=worst[1] - worst[0] + 1, tmax=[float(x) for x in df.tmax_c.iloc[worst[0]:worst[1] + 1]], peak=float(df.tmax_c.iloc[worst[0]:worst[1] + 1].max())),
        longest_wet=None if wlong is None else dict(start=iso(df.full_date.iloc[wlong[0]]), end=iso(df.full_date.iloc[wlong[1]]), days=wlong[1] - wlong[0] + 1, rain=[float(x) for x in df.precip_mm.iloc[wlong[0]:wlong[1] + 1]], total=float(df.precip_mm.iloc[wlong[0]:wlong[1] + 1].sum()))))


@app.get("/api/risk/cooling")
def risk_cooling(city: str, base: float = 24.0):
    """Cooling degree-days: how far each day's mean temperature sits above `base` degC, a weather indicator of air-conditioning pressure."""
    df = _daily_frame(city)
    df["cdd"] = (df.tmean_c - base).clip(lower=0)
    annual = [dict(year=int(y), cdd=float(g.cdd.sum())) for y, g in df.groupby("year") if len(g) >= 360]
    monthly = [dict(month=int(m), cdd=float(g.cdd.sum() / df.year.nunique())) for m, g in df.groupby("month")]
    with conn() as c:
        rank = c.execute("""SELECT l.city, l.location_id, round((sum(greatest(m.tmean_c - %s, 0)) / count(DISTINCT m.year))::numeric, 0) AS cdd_per_year
            FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key) WHERE m.n_hours=24 GROUP BY l.city, l.location_id ORDER BY 3 DESC""", (base,)).fetchall()
    return clean(dict(base=base, annual=annual, monthly=monthly, ranking=rank))


@app.get("/api/ml/cases")
def ml_cases(kind: str = "heat", city: str | None = None):
    """Case studies from the 2019-2022 test period: the model's 24-hour-ahead forecast against what happened."""
    with conn() as c:
        if kind == "heat":
            rows = c.execute("""SELECT l.city, to_date(p.target_date_key::text,'YYYYMMDD') AS target_date, round(p.actual::numeric,1) AS actual, round(p.predicted::numeric,1) AS predicted,
                round(p.pred_lower::numeric,1) AS lo, round(p.pred_upper::numeric,1) AS hi, round(q.predicted::numeric,1) AS naive
                FROM star.fact_prediction p JOIN star.dim_model m USING (model_key) JOIN star.dim_location l USING (location_key)
                JOIN star.fact_prediction q ON q.location_key=p.location_key AND q.issue_date_key=p.issue_date_key AND q.issue_time_key=p.issue_time_key
                JOIN star.dim_model mq ON mq.model_key=q.model_key AND mq.model_name='persistence' AND mq.target=m.target AND mq.horizon_h=m.horizon_h
                WHERE m.model_name='hgb' AND m.target='temperature_c' AND m.horizon_h=24 AND (%(c)s::text IS NULL OR l.location_id=%(c)s) ORDER BY p.actual DESC LIMIT 8""", dict(c=city)).fetchall()
            return clean(dict(kind="heat", cases=rows))
        mk = c.execute("SELECT m.model_key, (r.params->>'decision_threshold')::float AS thr FROM star.dim_model m JOIN ml.model_registry r USING (model_key) WHERE m.model_name='hgb_calibrated' AND m.horizon_h=24 AND m.risk_module LIKE 'B%%'").fetchone()
        if not mk:
            raise HTTPException(404, "rain model not trained")
        agg = c.execute("""SELECT count(*) FILTER (WHERE p.actual=1) AS events, count(*) FILTER (WHERE p.actual=1 AND p.probability>=%(t)s) AS caught,
            count(*) FILTER (WHERE p.actual=0 AND p.probability>=%(t)s) AS false_alarms, count(*) AS days, round(avg(p.probability) FILTER (WHERE p.actual=1)::numeric,3) AS avg_p_events, round(avg(p.probability) FILTER (WHERE p.actual=0)::numeric,4) AS avg_p_other
            FROM star.fact_prediction p JOIN star.dim_location l USING (location_key) WHERE p.model_key=%(k)s AND (%(c)s::text IS NULL OR l.location_id=%(c)s)""", dict(t=mk["thr"], k=mk["model_key"], c=city)).fetchone()
        ev = c.execute("""SELECT l.city, to_date(p.target_date_key::text,'YYYYMMDD') AS target_date, round(p.probability::numeric,3) AS probability FROM star.fact_prediction p JOIN star.dim_location l USING (location_key)
            WHERE p.model_key=%(k)s AND p.actual=1 AND (%(c)s::text IS NULL OR l.location_id=%(c)s) ORDER BY p.probability DESC LIMIT 12""", dict(k=mk["model_key"], c=city)).fetchall()
        return clean(dict(kind="rain", threshold=mk["thr"], summary=agg, events=ev))


@app.get("/api/eda")
def eda():
    with conn() as c:
        rows = c.execute("SELECT result, computed_at FROM ml.analysis_result WHERE name LIKE 'eda:%' ORDER BY name").fetchall()
    return clean([dict(**r["result"], computed_at=r["computed_at"]) for r in rows])


# ------------------------------------------------------------------ ETL
@app.get("/api/etl/runs")
def etl_runs():
    with conn() as c:
        return clean(c.execute("SELECT * FROM etl.run_log ORDER BY run_id DESC").fetchall())


@app.get("/api/etl/run/{run_id}")
def etl_run(run_id: int):
    with conn() as c:
        run = c.execute("SELECT * FROM etl.run_log WHERE run_id=%s", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "run not found")
        steps = c.execute("SELECT step_no, stage, step_name, purpose, columns, rows_affected, before_stats, after_stats FROM etl.step_log WHERE run_id=%s ORDER BY step_no", (run_id,)).fetchall()
        quality = c.execute("""SELECT phase, variable, sum(n_rows) AS n_rows, sum(n_missing) AS n_missing, round(avg(pct_missing)::numeric,4) AS pct_missing,
            min(v_min) AS v_min, round(avg(v_mean)::numeric,3) AS v_mean, max(v_max) AS v_max, sum(n_outliers) AS n_outliers
            FROM etl.quality_metrics WHERE run_id=%s GROUP BY phase, variable ORDER BY variable, phase""", (run_id,)).fetchall()
        q = c.execute("SELECT count(*) AS n FROM etl.quarantine WHERE run_id=%s", (run_id,)).fetchone()["n"]
        qs = c.execute("SELECT location_id, obs_time, column_name, bad_value, reason FROM etl.quarantine WHERE run_id=%s LIMIT 25", (run_id,)).fetchall()
        flagged = c.execute("""SELECT l.city, count(*) FILTER (WHERE is_outlier) AS outlier_hours, count(*) AS hours
            FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) GROUP BY l.city ORDER BY 1""").fetchall() if run["pipeline"] == "weather" else []
    recon = dict(rows_read=run["rows_read"], rejected=run["rows_rejected"], rows_added_gap_fill=run["rows_added_gap_fill"], transformed=run["rows_transformed"],
                 loaded=run["rows_loaded"], rows_in_table_after=run["rows_in_table_after"])
    return clean(dict(run=run, steps=steps, quality=quality, quarantine_count=q, quarantine_sample=qs, reconciliation=recon, outliers_by_city=flagged))


# ------------------------------------------------------- ETL evidence
def _warm_etl_evidence():
    """Pre-compute the slow evidence queries in the background so the first page view is instant."""
    import threading
    threading.Thread(target=lambda: (etl_inv_warm(), etl_recon(), etl_flagged(), etl_stress()), daemon=True).start()


def etl_inv_warm():
    try:
        etl_inventory()
    except Exception:  # warm-up only; the endpoint reports real errors
        pass


@app.get("/api/etl/inventory")
def etl_inventory():
    return cached("etl_inv", 3600, etl_proof.inventory)


@app.get("/api/etl/source")
def etl_source(city: str, year: int):
    return cached(f"etl_src_{city}_{year}", 3600, lambda: etl_proof.source_profile(city, year))


@app.get("/api/etl/stress-test")
def etl_stress():
    return cached("etl_stress", 600, etl_proof.stress_test)


@app.get("/api/etl/reconciliation")
def etl_recon():
    return clean(cached("etl_recon", 600, etl_proof.reconciliation))


@app.get("/api/etl/flagged")
def etl_flagged():
    return clean(cached("etl_flagged", 3600, etl_proof.flagged))


@app.get("/api/etl/code")
def etl_code():
    return cached("etl_code", 3600, etl_proof.code)


# ------------------------------------------------------------ warehouse
FK_SQL = """SELECT cl.relname AS tbl, a.attname AS col, fn.nspname AS ref_schema, fcl.relname AS ref_tbl, fa.attname AS ref_col
FROM pg_constraint con JOIN pg_class cl ON cl.oid=con.conrelid JOIN pg_namespace n ON n.oid=cl.relnamespace
JOIN pg_class fcl ON fcl.oid=con.confrelid JOIN pg_namespace fn ON fn.oid=fcl.relnamespace
JOIN LATERAL unnest(con.conkey, con.confkey) AS k(attnum, fattnum) ON true
JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.attnum JOIN pg_attribute fa ON fa.attrelid=con.confrelid AND fa.attnum=k.fattnum
WHERE con.contype='f' AND n.nspname=%s"""
PK_SQL = """SELECT cl.relname AS tbl, a.attname AS col FROM pg_constraint con JOIN pg_class cl ON cl.oid=con.conrelid JOIN pg_namespace n ON n.oid=cl.relnamespace
JOIN LATERAL unnest(con.conkey) AS k(attnum) ON true JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.attnum WHERE con.contype='p' AND n.nspname=%s"""


@app.get("/api/warehouse/schema")
def wh_schema(kind: str = "star"):
    if kind not in ("star", "snow"):
        raise ValueError("kind must be star or snow")

    def build():
        with conn() as c:
            cols = c.execute("""SELECT table_name, column_name, data_type, is_nullable, ordinal_position FROM information_schema.columns WHERE table_schema=%s
                AND table_name IN (SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE') ORDER BY table_name, ordinal_position""", (kind, kind)).fetchall()
            fks, pks = c.execute(FK_SQL, (kind,)).fetchall(), c.execute(PK_SQL, (kind,)).fetchall()
            tables: dict[str, dict] = {}
            for r in cols:
                t = tables.setdefault(r["table_name"], dict(name=r["table_name"], kind="fact" if r["table_name"].startswith("fact_") else "dimension", columns=[], rows=None))
                t["columns"].append(dict(name=r["column_name"], type=r["data_type"], nullable=r["is_nullable"] == "YES", pk=False, fk=None))
            for p in pks:
                for col in tables[p["tbl"]]["columns"]:
                    col["pk"] |= col["name"] == p["col"]
            for f in fks:
                for col in tables[f["tbl"]]["columns"]:
                    if col["name"] == f["col"]:
                        col["fk"] = f"{f['ref_tbl']}.{f['ref_col']}"
            for name, t in tables.items():
                t["rows"] = c.execute(f"SELECT count(*) AS n FROM {kind}.{name}").fetchone()["n"]
        rels = [dict(from_table=f["tbl"], from_col=f["col"], to_table=f["ref_tbl"], to_col=f["ref_col"], cardinality="many-to-one") for f in fks if f["ref_schema"] == kind]
        return clean(dict(schema=kind, tables=list(tables.values()), relationships=rels))
    return cached(f"schema:{kind}", 300, build)


@app.get("/api/warehouse/preview")
def wh_preview(kind: str, table: str, limit: int = Query(10, ge=1, le=50)):
    schema = wh_schema(kind)
    if table not in {t["name"] for t in schema["tables"]}:
        raise HTTPException(404, "unknown table")
    with conn() as c:
        rows = c.execute(f"SELECT * FROM {kind}.{table} LIMIT %s", (limit,)).fetchall()
    return clean(dict(table=table, rows=rows))


@app.get("/api/warehouse/profile")
def wh_profile():
    """Everything the Warehouse page needs: table sizes, fill rates of the hourly fact and chart data."""
    def build():
        sch = wh_schema("star")
        with conn() as c:
            sz = wh_explore.sizes(c)
            num = [x["name"] for x in next(t for t in sch["tables"] if t["name"] == "fact_weather_hourly")["columns"]
                   if x["type"] in wh_explore.NUM_TYPES and not x["pk"] and not x["fk"]]
            cols, ch = wh_explore.weather_columns(c, num), wh_explore.charts(c)
            mv = c.execute("SELECT count(*) AS n, pg_total_relation_size('star.mv_weather_daily')/1048576.0 AS mb FROM star.mv_weather_daily").fetchone()
            sz["mv_weather_daily"] = round(float(mv["mb"]), 2)
            ch["mv_rows"] = mv["n"]
        return clean(dict(sizes=sz, weather_columns=cols, charts=ch))
    return cached("wh_profile", 1800, build)


@app.get("/api/warehouse/trace")
def wh_trace(table: str, pick: str = "first", location: str = "BOM", n: int = Query(0, ge=0, le=500)):
    sch = wh_schema("star")
    if table not in {t["name"] for t in sch["tables"]}:
        raise HTTPException(404, "unknown table")
    with conn() as c:
        return clean(wh_explore.trace(c, sch, table, pick, location, n))


@app.get("/api/warehouse/compare")
def wh_compare(id: str):
    if id not in olap.COMPARISONS:
        raise HTTPException(404, "unknown comparison")
    with conn() as c:
        return clean(olap.compare_schemas(c, id))


@app.get("/api/warehouse/comparisons")
def wh_comparisons():
    return [dict(id=k, title=v["title"]) for k, v in olap.COMPARISONS.items()]


# ----------------------------------------------------------------- OLAP
@app.get("/api/olap/meta")
def olap_meta():
    return dict(time_levels=list(olap.TIME_LEVELS), geo_levels=list(olap.GEO_LEVELS), measures={k: v[1] for k, v in olap.MEASURES.items()},
                time_parent=olap.TIME_PARENT, seasons=["Winter", "Pre-monsoon", "Monsoon", "Post-monsoon"])


@app.get("/api/olap/aggregate")
def olap_aggregate(time_level: str = "year", geo_level: str = "region", measures: str = "avg_temp_c", cities: str = "", seasons: str = "", months: str = "",
                   year_from: int | None = None, year_to: int | None = None, region: str | None = None, state: str | None = None):
    f = dict(cities=[x for x in cities.split(",") if x], seasons=[x for x in seasons.split(",") if x], months=[x for x in months.split(",") if x],
             year_from=year_from, year_to=year_to, region=region, state=state)
    with conn() as c:
        return clean(olap.aggregate(c, time_level, geo_level, measures.split(","), f))


@app.get("/api/olap/pivot")
def olap_pivot(row: str = "city", col: str = "season", measure: str = "avg_temp_c", year_from: int | None = None, year_to: int | None = None):
    with conn() as c:
        return clean(olap.pivot(c, row, col, measure, dict(year_from=year_from, year_to=year_to)))


@app.get("/api/sql/questions")
def sql_questions():
    return [dict(id=k, title=v["title"], sql=v["sql"], needs_city="city" in v.get("params", [])) for k, v in olap.QUESTIONS.items()]


@app.get("/api/sql/run")
def sql_run(id: str, city: str | None = None):
    if id not in olap.QUESTIONS:
        raise HTTPException(404, "unknown question")
    with conn() as c:
        return clean(olap.run_question(c, id, {"city": city}))


# ------------------------------------------------------------------- ML
@app.get("/api/ml/summary")
def ml_summary():
    def build():
        with conn() as c:
            rows = c.execute("""SELECT m.risk_module, m.target, m.horizon_h, m.model_name, m.model_family, m.is_baseline, e.metric_name, e.metric_value, e.n_samples
                FROM star.fact_model_evaluation e JOIN star.dim_model m USING (model_key) WHERE e.scope='overall' ORDER BY 1,2,3,4""").fetchall()
            reg = c.execute("SELECT m.model_name, m.risk_module, m.target, m.horizon_h, r.artifact_path, r.trained_at, r.train_period, r.val_period, r.test_period, r.n_train, r.code_version, r.params FROM ml.model_registry r JOIN star.dim_model m USING (model_key) ORDER BY 2,3,4,1").fetchall()
            cal = c.execute("SELECT result FROM ml.analysis_result WHERE name='rain_calibration_curves'").fetchone()
            agri = c.execute("SELECT result FROM ml.analysis_result WHERE name='agri_model'").fetchone()
        grouped: dict = {}
        for r in rows:
            k = (r["risk_module"], r["target"], r["horizon_h"], r["model_name"])
            g = grouped.setdefault(k, dict(risk=r["risk_module"], target=r["target"], horizon_h=r["horizon_h"], model=r["model_name"], family=r["model_family"], baseline=r["is_baseline"], n=r["n_samples"], metrics={}))
            g["metrics"][r["metric_name"]] = r["metric_value"]
        return clean(dict(results=list(grouped.values()), registry=reg, calibration=cal["result"] if cal else None, agri=({k: v for k, v in agri["result"].items() if k != "predictions"} if agri else None)))
    return cached("ml_summary", 300, build)


@app.get("/api/ml/breakdown")
def ml_breakdown(risk: str, target: str, horizon_h: int, scope: str = "location"):
    if scope not in ("location", "season"):
        raise ValueError("scope must be location or season")
    with conn() as c:
        rows = c.execute("""SELECT e.scope_value, m.model_name, e.metric_name, e.metric_value, e.n_samples FROM star.fact_model_evaluation e JOIN star.dim_model m USING (model_key)
            WHERE m.risk_module LIKE %s AND m.target=%s AND m.horizon_h=%s AND e.scope=%s ORDER BY 1,2""", (risk + "%", target, horizon_h, scope)).fetchall()
    return clean(rows)


@app.get("/api/ml/predictions")
def ml_predictions(location_id: str, model: str, target: str, horizon_h: int, year: int = 2020, month: int | None = None):
    with conn() as c:
        rows = c.execute("""SELECT to_date(p.target_date_key::text,'YYYYMMDD') + p.target_time_key * interval '1 hour' AS target_time, p.predicted, p.pred_lower, p.pred_upper, p.actual
            FROM star.fact_prediction p JOIN star.dim_model m USING (model_key) JOIN star.dim_location l USING (location_key)
            WHERE l.location_id=%s AND m.model_name=%s AND m.target=%s AND m.horizon_h=%s AND p.target_date_key BETWEEN %s AND %s ORDER BY 1""",
                         (location_id, model, target, horizon_h, year * 10000 + (month or 1) * 100 + 1, year * 10000 + (month or 12) * 100 + 31)).fetchall()
    return clean(rows)


@app.get("/api/ml/agri")
def ml_agri(city: str | None = None, crop: str | None = None):
    with conn() as c:
        r = c.execute("SELECT result FROM ml.analysis_result WHERE name='agri_model'").fetchone()
    if not r:
        return JSONResponse(status_code=503, content={"error": "not_available", "detail": "agricultural model has not been trained yet (needs weather for crop districts)"})
    res = r["result"]
    preds = [p for p in res["predictions"] if (not city or p["city"] == city) and (not crop or p["crop_name"] == crop)]
    return dict(res, predictions=preds, crop_options=sorted({p["crop_name"] for p in res["predictions"]}))


class PredictRequest(BaseModel):
    location_id: str = Field(min_length=2, max_length=4)
    issue_time: datetime
    risk: str
    horizon_h: int
    model: str = "hgb"


@app.post("/api/predict")
def predict_endpoint(req: PredictRequest):
    with conn() as c:
        return clean(predict_mod.predict(c, req.location_id.upper(), req.issue_time.replace(tzinfo=None), req.risk, req.horizon_h, req.model))



# ---------------------------------------------------------- model explanations
@app.get("/api/models/overview")
def models_overview(target: str = "temp", horizon_h: int = 24):
    return clean(models_viz.overview(target, horizon_h))


class ExplainRequest(BaseModel):
    location_id: str = Field(min_length=2, max_length=4)
    issue_time: datetime
    target: str = "temp"
    horizon_h: int = 24
    overrides: dict[str, float] = {}
    sweep: str | None = None


@app.post("/api/models/explain")
def models_explain(req: ExplainRequest):
    return clean(models_viz.explain_row(req.location_id.upper(), req.issue_time.replace(tzinfo=None), req.target, req.horizon_h, req.overrides, req.sweep))


WEB = Path(__file__).resolve().parents[2] / "web"
if WEB.exists():
    class NoCacheStatic(StaticFiles):
        """Always revalidate dashboard files so a normal refresh shows the latest design."""
        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            return resp

    app.mount("/", NoCacheStatic(directory=WEB, html=True), name="web")
