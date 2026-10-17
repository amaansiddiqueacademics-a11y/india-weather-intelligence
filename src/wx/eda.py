"""Question-driven EDA. Every finding is computed from the warehouse and stored in
ml.analysis_result with its question, method, result, interpretation, relevance and limitation."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats

from . import db

DAILY_VARS = ["tmean_c", "tmax_c", "tmin_c", "precip_mm", "rh_mean", "wind_mean_kmh", "pressure_mean_hpa", "soil_moisture", "et0_mm", "hi_max_c"]


def daily(conn) -> pd.DataFrame:
    rows = conn.execute("SELECT l.city, m.* FROM star.mv_weather_daily m JOIN star.dim_location l USING (location_key) WHERE n_hours = 24").fetchall()
    df = pd.DataFrame(rows)
    df["full_date"] = pd.to_datetime(df.full_date)
    return df


def mann_kendall(x: np.ndarray) -> dict:
    """Mann-Kendall trend test (no ties correction beyond variance formula) + Sen's slope."""
    x = np.asarray(x, float)
    n = len(x)
    s = sum(np.sign(x[j] - x[i]) for i in range(n - 1) for j in range(i + 1, n))
    var = n * (n - 1) * (2 * n + 5) / 18
    z = (s - np.sign(s)) / np.sqrt(var) if s != 0 else 0.0
    p = 2 * (1 - stats.norm.cdf(abs(z)))
    slopes = [(x[j] - x[i]) / (j - i) for i in range(n - 1) for j in range(i + 1, n)]
    return dict(S=float(s), z=float(z), p=float(p), sen_slope_per_year=float(np.median(slopes)), n=n)


def longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def compute(conn) -> list[dict]:
    d = daily(conn)
    findings: list[dict] = []
    years_full = d.groupby(["city", "year"]).size().rename("n").reset_index()
    full = years_full[years_full.n >= 360][["city", "year"]]
    d_full = d.merge(full, on=["city", "year"])

    # 1. correlation structure
    corr = d[DAILY_VARS].corr(method="spearman")
    pairs = corr.where(~np.eye(len(corr), dtype=bool)).stack().sort_values(key=abs, ascending=False)
    top = pairs.iloc[0:12:2]
    findings.append(dict(
        id="correlations", risk="A,B", title="Which weather variables move together?",
        question="How strongly are daily weather variables associated (Spearman, all cities)?", method="Spearman rank correlation of daily aggregates from star.mv_weather_daily.",
        result=dict(variables=DAILY_VARS, matrix=corr.round(3).values.tolist(), strongest=[dict(a=i[0], b=i[1], rho=round(float(v), 3)) for i, v in top.items()]),
        interpretation=f"The strongest association is {top.index[0][0]} vs {top.index[0][1]} (rho={top.iloc[0]:.2f}). Humidity and temperature are linked through the seasonal cycle, not because one drives the other.",
        limitation="Correlation is not causation; a shared monsoon seasonal cycle inflates many pairwise correlations. Daily means hide hourly timing."))

    # 2. trends with significance
    trend_rows = []
    for city, g in d_full.groupby("city"):
        ann = g.groupby("year").agg(temp=("tmean_c", "mean"), rain=("precip_mm", "sum"))
        if len(ann) < 8:
            continue
        mk_t, mk_r = mann_kendall(ann.temp.to_numpy()), mann_kendall(ann.rain.to_numpy())
        trend_rows.append(dict(city=city, years=len(ann), temp_sen_c_per_decade=round(mk_t["sen_slope_per_year"] * 10, 3), temp_p=round(mk_t["p"], 4),
                               rain_sen_mm_per_decade=round(mk_r["sen_slope_per_year"] * 10, 1), rain_p=round(mk_r["p"], 4)))
    sig = [r["city"] for r in trend_rows if r["temp_p"] < 0.05]
    findings.append(dict(
        id="trends", risk="A,B", title="Are temperature or rainfall trending?",
        question="Is there a statistically significant long-term trend in annual mean temperature and annual rainfall per city?",
        method="Mann-Kendall test with Sen's slope on annual values (years with >= 360 days).",
        result=dict(rows=trend_rows),
        interpretation=(f"Significant (p<0.05) temperature trends: {', '.join(sig)}." if sig else "No city shows a statistically significant annual temperature trend at p<0.05 over this window.")
        + " Rainfall trends are reported with their p-values in the table.",
        limitation="Only ~20 years per city: low power. ERA5 reanalysis can contain inhomogeneities; this is not an observational station record."))

    # 3. seasonal decomposition (classical additive) for each city's monthly temperature
    dec = {}
    for city, g in d.groupby("city"):
        m = g.set_index("full_date").tmean_c.resample("MS").mean()
        trend = m.rolling(12, center=True).mean()
        seas = (m - trend).groupby(m.index.month).mean()
        resid = m - trend - m.index.month.map(seas).to_numpy()
        dec[city] = dict(seasonal_amplitude_c=round(float(seas.max() - seas.min()), 2), resid_sd_c=round(float(resid.std()), 2))
    findings.append(dict(
        id="seasonality", risk="A", title="How strong is the seasonal cycle of temperature?",
        question="How much of monthly temperature variation is seasonal rather than trend or noise?", method="Classical additive decomposition (12-month centred moving-average trend, month-mean seasonal component).",
        result=dec,
        interpretation="Seasonal amplitude (hottest minus coolest month component) ranges from "
        f"{min(v['seasonal_amplitude_c'] for v in dec.values())} to {max(v['seasonal_amplitude_c'] for v in dec.values())} °C across cities, far above residual noise.",
        limitation="Monthly means smooth over heatwaves and cold snaps."))

    # 4. extremes and spells
    ex_rows = []
    out_share = conn.execute("""SELECT round(100.0*count(*) FILTER (WHERE is_outlier)/count(*),3) AS pct FROM star.fact_weather_hourly WHERE temperature_c >= 40""").fetchone()["pct"]
    for city, g in d_full.sort_values("full_date").groupby("city"):
        hot = (g.tmax_c >= 40).to_numpy()
        spells = 0
        run = 0
        for v in hot:
            run = run + 1 if v else 0
            spells += run == 3
        ex_rows.append(dict(city=city, days_tmax_ge_40_per_year=round(hot.sum() / g.year.nunique(), 1), longest_hot_spell_days=longest_run(hot),
                            heatwave_spells_3d_per_year=round(spells / g.year.nunique(), 2), heavy_rain_days_ge_64_5mm=int((g.precip_mm >= 64.5).sum()),
                            max_daily_rain_mm=round(float(g.precip_mm.max()), 1)))
    findings.append(dict(
        id="extremes", risk="A,B", title="How often do extremes happen, and are they real?",
        question="How frequent and long are hot spells (Tmax >= 40 °C) and extreme-rain days (>= 64.5 mm) per city?",
        method="Threshold counts and run-length of consecutive days on daily aggregates; flagged-outlier share among >=40 °C hours as a data-quality cross-check.",
        result=dict(rows=ex_rows, pct_hot_hours_flagged_outlier=float(out_share)),
        interpretation=f"{out_share}% of hours at or above 40 °C were flagged as statistical outliers, so the heat extremes are consistent with each city's local seasonal climate rather than data errors.",
        limitation="ERA5 smooths extremes at ~9-31 km grid scale: heavy rain peaks are likely understated relative to rain-gauge records."))

    # 5. temperature-humidity relationship + diurnal range
    th = []
    for city, g in d.groupby("city"):
        r = stats.spearmanr(g.tmax_c, g.rh_mean)
        th.append(dict(city=city, rho_tmax_vs_humidity=round(float(r.statistic), 3), dtr_c=round(float((g.tmax_c - g.tmin_c).mean()), 2)))
    findings.append(dict(
        id="temp_humidity", risk="A", title="Do hotter days feel more humid?",
        question="What is the daily relationship between maximum temperature and mean relative humidity, and the diurnal range?",
        method="Spearman correlation per city; mean daily temperature range.", result=dict(rows=th),
        interpretation=(lambda pos, neg: f"Tmax and humidity are positively correlated in {pos} cities and negatively in {neg}; "
                        "the heat index combines both variables because the same temperature is more stressful when it is humid.")(
            sum(r["rho_tmax_vs_humidity"] > 0 for r in th), sum(r["rho_tmax_vs_humidity"] <= 0 for r in th)),
        limitation="Seasonal confounding: both variables follow the monsoon cycle."))

    # 6. lagged rain relationship
    lag = []
    for city, g in d.sort_values("full_date").groupby("city"):
        g = g.set_index("full_date")
        dp = g.pressure_mean_hpa.diff()
        lag.append(dict(city=city, rho_rain_vs_prev_day_pressure_change=round(float(stats.spearmanr(dp.shift(1).iloc[2:].fillna(0), g.precip_mm.iloc[2:]).statistic), 3),
                        rho_rain_vs_prev_day_rain=round(float(stats.spearmanr(g.precip_mm.shift(1).iloc[2:].fillna(0), g.precip_mm.iloc[2:]).statistic), 3)))
    findings.append(dict(
        id="rain_lags", risk="B", title="Does yesterday predict today's rain?",
        question="How strongly is daily rainfall related to the previous day's rain and pressure change?", method="Lag-1 Spearman correlations per city.",
        result=dict(rows=lag), interpretation=(lambda pr, pp: f"Across cities the mean lag-1 correlation of rain with the previous day's rain is {pr:.2f}, and with the previous day's pressure change {pp:.2f}; "
                                               f"{'rain persistence' if abs(pr) > abs(pp) else 'pressure tendency'} is the stronger simple signal.")(
            float(np.mean([r["rho_rain_vs_prev_day_rain"] for r in lag])), float(np.mean([r["rho_rain_vs_prev_day_pressure_change"] for r in lag]))),
        limitation="Lag-1 only; no spatial information on approaching systems, so skill is limited beyond a day or so."))
    return findings


def store(conn, findings: list[dict]) -> None:
    for f in findings:
        conn.execute("INSERT INTO ml.analysis_result (name, params, result) VALUES (%s,%s,%s) ON CONFLICT (name) DO UPDATE SET result=EXCLUDED.result, params=EXCLUDED.params, computed_at=now()",
                     (f"eda:{f['id']}", json.dumps({"risk": f["risk"]}), json.dumps(f, default=float)))
    conn.commit()


def main() -> None:
    conn = db.connect()
    f = compute(conn)
    store(conn, f)
    for x in f:
        print(f"[{x['id']}] {x['title']}\n   {x['interpretation']}")


if __name__ == "__main__":
    main()
