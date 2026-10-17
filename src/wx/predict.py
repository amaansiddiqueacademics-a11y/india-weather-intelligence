"""Inference service: builds features from the warehouse and applies a saved model."""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

import joblib
import numpy as np
import pandas as pd
import psycopg

from . import config
from .derived import heat_category
from .features import ISSUE_HOURS, STATIC_COLS, attach_climatology, make_features

HEAT_MODELS = ("hgb", "ridge", "persistence", "climatology")
RAIN_MODELS = ("hgb", "logistic", "climatology", "persistence")
HEAT_HORIZONS, RAIN_HORIZONS = (6, 24, 72), (24, 48, 72)
WINDOW_H = 24 * 8


class PredictionError(ValueError):
    """Raised when a valid prediction cannot be produced (maps to HTTP 422)."""


@lru_cache(maxsize=32)
def _artifact(name: str) -> dict:
    path = config.MODELS_DIR / name
    if not path.exists():
        raise PredictionError(f"model artifact {name} is not available (train models first)")
    return joblib.load(path)


def _window(conn: psycopg.Connection, location_id: str, issue: datetime) -> pd.DataFrame:
    start = issue - timedelta(hours=WINDOW_H)
    rows = conn.execute(
        """SELECT l.latitude, l.longitude, coalesce(l.elevation_m,0) AS elevation_m,
                  (to_date(f.date_key::text,'YYYYMMDD') + f.time_key * interval '1 hour') AS obs_time,
                  f.temperature_c, f.relative_humidity_pct, f.dew_point_c, f.pressure_msl_hpa, f.cloud_cover_pct,
                  f.wind_speed_kmh, f.wind_gust_kmh, f.wind_direction_deg, f.heat_index_c, f.apparent_temperature_c,
                  f.soil_moisture_m3m3, f.shortwave_radiation_wm2, f.precipitation_mm
           FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
           WHERE l.location_id=%s AND (to_date(f.date_key::text,'YYYYMMDD') + f.time_key * interval '1 hour') BETWEEN %s AND %s
           ORDER BY obs_time""", (location_id, start, issue)).fetchall()
    if len(rows) < WINDOW_H:
        raise PredictionError(f"not enough history before {issue:%Y-%m-%d %H:%M} for {location_id}: need {WINDOW_H} hourly rows, found {len(rows)}")
    return pd.DataFrame(rows).set_index("obs_time")


def _row_features(conn, location_id: str, issue: datetime, art: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    g = _window(conn, location_id, issue)
    f = make_features(g)
    for c in STATIC_COLS:
        f[c] = g[c].iloc[0]
    f["clim_temp"] = attach_climatology(f, location_id, art["clim_temp"], "c").to_numpy()
    f["clim_hi"] = attach_climatology(f, location_id, art["clim_hi"], "c").to_numpy()
    f["temp_anom"], f["hi_anom"] = f.temp - f.clim_temp, f.hi - f.clim_hi
    row = f.loc[[pd.Timestamp(issue)]]
    if row[art["features"]].isna().any(axis=1).iloc[0]:
        raise PredictionError("input features contain missing values for this issue time")
    return row, g


def _observed(conn, location_id: str, t: datetime, col: str = "temperature_c") -> float | None:
    r = conn.execute(f"SELECT f.{col} AS v FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) "
                     "WHERE l.location_id=%s AND f.date_key=%s AND f.time_key=%s", (location_id, int(t.strftime("%Y%m%d")), t.hour)).fetchone()
    return None if r is None else r["v"]


def validate_request(location_id: str, issue: datetime, risk: str, horizon_h: int, model: str, data_range: tuple[datetime, datetime]) -> None:
    if risk not in ("heat", "rain"):
        raise PredictionError("risk must be 'heat' or 'rain'")
    if horizon_h not in (HEAT_HORIZONS if risk == "heat" else RAIN_HORIZONS):
        raise PredictionError(f"horizon must be one of {HEAT_HORIZONS if risk == 'heat' else RAIN_HORIZONS} hours for {risk}")
    if model not in (HEAT_MODELS if risk == "heat" else RAIN_MODELS):
        raise PredictionError(f"model must be one of {HEAT_MODELS if risk == 'heat' else RAIN_MODELS}")
    if issue.hour not in ISSUE_HOURS or issue.minute:
        raise PredictionError(f"models were trained for issue times at hours {list(ISSUE_HOURS)} (local time)")
    lo, hi = data_range
    if issue < lo + timedelta(hours=WINDOW_H) or issue > hi:
        raise PredictionError(f"issue time must lie within {lo + timedelta(hours=WINDOW_H):%Y-%m-%d} .. {hi:%Y-%m-%d} (warehouse coverage)")


def predict(conn: psycopg.Connection, location_id: str, issue: datetime, risk: str, horizon_h: int, model: str) -> dict:
    rng = conn.execute("SELECT min(date_key) AS a, max(date_key) AS b FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) WHERE l.location_id=%s", (location_id,)).fetchone()
    if rng["a"] is None:
        raise PredictionError(f"no warehouse data for location {location_id}")
    data_range = (datetime.strptime(str(rng["a"]), "%Y%m%d"), datetime.strptime(str(rng["b"]), "%Y%m%d") + timedelta(hours=23))
    validate_request(location_id, issue, risk, horizon_h, model, data_range)
    target_time = issue + timedelta(hours=horizon_h)
    out = dict(location_id=location_id, risk=risk, horizon_h=horizon_h, model=model, issue_time=issue.isoformat(), target_time=target_time.isoformat(),
               kind="own-model prediction (not observed data and not an external forecast)")
    reg_meta = {r["model_name"]: r for r in conn.execute(
        "SELECT m.model_name, m.target, r.metrics, r.trained_at, r.code_version FROM star.dim_model m JOIN ml.model_registry r USING (model_key) WHERE m.horizon_h=%s", (horizon_h,))}
    if risk == "heat":
        results = {}
        for tname, target in (("temp", "temperature_c"), ("hi", "heat_index_c")):
            art = _artifact(f"heat_{tname}_{horizon_h}h_v1.joblib")
            row, g = _row_features(conn, location_id, issue, art)
            if model == "persistence":
                p, lo, hi = float(row["temp" if tname == "temp" else "hi"].iloc[0]), None, None
            elif model == "climatology":
                p, lo, hi = float(row["clim_temp" if tname == "temp" else "clim_hi"].iloc[0]), None, None
            else:
                p = float(art["models"][model].predict(row[art["features"]].to_numpy(float))[0])
                q = art["conformal"][model]
                lo, hi = p - q, p + q
            results[target] = dict(predicted=round(p, 2), lower_90=None if lo is None else round(lo, 2), upper_90=None if hi is None else round(hi, 2),
                                   observed_at_target=_observed(conn, location_id, target_time, target))
        hi_pred = results["heat_index_c"]["predicted"]
        out.update(results=results, risk_category=heat_category(hi_pred), unit="°C", n_features=len(art["features"]),
                   inputs=dict(temperature_c=round(float(row["temp"].iloc[0]), 1), relative_humidity_pct=round(float(row["rh"].iloc[0]), 0),
                               wind_kmh=round(float(row["wind"].iloc[0]), 1), pressure_hpa=round(float(row["pres"].iloc[0]), 1),
                               temp_24h_ago_c=round(float(row["temp_l24"].iloc[0]), 1)),
                   explanation=(f"Forecast heat index {hi_pred} °C at the target time falls in the weather-derived band '{heat_category(hi_pred)}'. "
                                "The heat index combines temperature and humidity into how hot it feels; it is an indicator, not a medical diagnosis or an official warning."))
        meta_key = "hgb" if model not in reg_meta else model
    else:
        art = _artifact(f"rain_{horizon_h}h_v1.joblib")
        row, g = _row_features(conn, location_id, issue, art)
        x = row[art["features"]].to_numpy(float)
        if model == "persistence":
            prob = float(row["rain_24h"].iloc[0] >= art["rain_threshold_mm"]); thr = 0.5
        elif model == "climatology":
            prob = float(_clim_rain(conn, location_id, issue.month, horizon_h)); thr = art["thresholds"]["climatology"]
        else:
            raw = float(art["models"][model].predict_proba(x)[0, 1])
            prob = float(art["calibrators"][model].predict([raw])[0]); thr = art["thresholds"][model]
        obs = None
        if target_time <= data_range[1]:
            r = conn.execute("""SELECT sum(f.precipitation_mm) AS s, count(*) AS n FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
                WHERE l.location_id=%s AND (to_date(f.date_key::text,'YYYYMMDD') + f.time_key * interval '1 hour') > %s AND (to_date(f.date_key::text,'YYYYMMDD') + f.time_key * interval '1 hour') <= %s""",
                             (location_id, target_time - timedelta(hours=24), target_time)).fetchone()
            obs = None if r["n"] < 24 else float(r["s"])
        out.update(results=dict(probability_heavy_rain=round(prob, 4), rain_threshold_mm_24h=art["rain_threshold_mm"], decision_threshold=None if thr is None else round(float(thr), 4),
                                flagged=None if thr is None else bool(prob >= thr), observed_24h_rain_mm=obs),
                   unit="probability", n_features=len(art["features"]), risk_category="Elevated" if thr is not None and prob >= thr else "Not flagged",
                   inputs=dict(rain_last_24h_mm=round(float(row["rain_24h"].iloc[0]), 1), rain_last_72h_mm=round(float(row["rain_72h"].iloc[0]), 1),
                               relative_humidity_pct=round(float(row["rh"].iloc[0]), 0), pressure_hpa=round(float(row["pres"].iloc[0]), 1),
                               pressure_change_24h_hpa=round(float(row["pres_d24"].iloc[0]), 1), soil_moisture=round(float(row["soil"].iloc[0]), 3)),
                   explanation=(f"Estimated probability that the 24 h ending at the target time brings at least {art['rain_threshold_mm']:g} mm of rain: {prob:.1%}. "
                                "Heavy rain is rare, so even a well-calibrated low probability can matter. This is rainfall, not flood risk: flooding also depends on drainage and terrain."))
        meta_key = "hgb_calibrated" if model == "hgb" else "logistic_calibrated" if model == "logistic" else model
    meta = reg_meta.get(meta_key)
    out["model_info"] = None if meta is None else dict(name=meta_key, version="v1", trained_at=meta["trained_at"].isoformat(), code_version=meta["code_version"],
                                                         historical_test_metrics=(meta["metrics"] or {}).get("test"), note="Test metrics describe average skill on 2019-2022, not the certainty of this single prediction.")
    if meta is None:
        out["model_info"] = dict(name=model, version="v1", note="Baseline model: no registry entry; see /api/ml/summary for its test metrics.")
    return out


def _clim_rain(conn, location_id: str, month: int, horizon_h: int) -> float:
    r = conn.execute("""SELECT avg(p.probability) AS p FROM star.fact_prediction p JOIN star.dim_model m USING (model_key) JOIN star.dim_location l USING (location_key)
                        WHERE l.location_id=%s AND m.model_name='climatology' AND m.risk_module LIKE 'B%%' AND m.horizon_h=%s""", (location_id, horizon_h)).fetchone()
    return float(r["p"] or 0.0)
