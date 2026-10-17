"""Risk C: weather-informed crop-yield model for the districts that contain a supported city.

Unit of analysis: district x crop x season x agricultural year (start year Y).
Weather hourly -> daily -> season-window summaries, joined to the district yield for year Y.
Chronological split by year: train <= 2014 | validation 2015-2016 | test 2017-2021.
Target: yield (t/ha) expressed relative to the district-crop-season training mean.
"""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import config, db
from .eda import longest_run
from .ml import VERSION, RISK_C, code_version, reg_metrics, store_eval, upsert_model

TRAIN_MAX, VAL_MAX, TEST_MAX = 2014, 2016, 2021
WEATHER_FEATS = ["kharif_rain_mm", "kharif_rainy_days", "kharif_dry_spell_d", "kharif_tmax_c", "kharif_hot_days",
                 "kharif_et0_mm", "kharif_soil", "rabi_rain_mm", "rabi_tmin_c", "rabi_tmax_c", "rabi_hot_days", "rabi_et0_mm"]


def weather_features(conn) -> pd.DataFrame:
    rows = conn.execute("""SELECT m.location_key, m.full_date, m.year, m.month, m.precip_mm, m.tmax_c, m.tmin_c, m.et0_mm, m.soil_moisture
                           FROM star.mv_weather_daily m
                           WHERE m.location_key IN (SELECT DISTINCT location_key FROM star.fact_agri_production) AND m.n_hours = 24""").fetchall()
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    out = []
    for (loc, y), g in d.groupby(["location_key", "year"]):
        k = g[(g.month >= 6) & (g.month <= 9)]
        kt = g[(g.month >= 6) & (g.month <= 10)]
        nxt = d[(d.location_key == loc) & (d.year == y + 1) & (d.month <= 3)]
        rab = pd.concat([g[g.month >= 11], nxt])
        if len(k) < 120 or len(rab) < 140:
            continue
        out.append(dict(location_key=loc, year=y, kharif_rain_mm=k.precip_mm.sum(), kharif_rainy_days=(k.precip_mm >= 2.5).sum(),
                        kharif_dry_spell_d=longest_run((k.sort_values("full_date").precip_mm < 2.5).to_numpy()), kharif_tmax_c=kt.tmax_c.mean(),
                        kharif_hot_days=(kt.tmax_c >= 38).sum(), kharif_et0_mm=kt.et0_mm.sum(), kharif_soil=kt.soil_moisture.mean(),
                        rabi_rain_mm=rab.precip_mm.sum(), rabi_tmin_c=rab.tmin_c.mean(), rabi_tmax_c=rab.tmax_c.mean(),
                        rabi_hot_days=(rab.tmax_c >= 35).sum(), rabi_et0_mm=rab.et0_mm.sum()))
    return pd.DataFrame(out)


def build(conn) -> pd.DataFrame:
    ag = pd.DataFrame(conn.execute("""SELECT a.location_key, l.city, c.crop_name, s.season_name, d.year AS year, a.yield_t_ha, a.area_ha
        FROM star.fact_agri_production a JOIN star.dim_location l USING (location_key) JOIN star.dim_crop c USING (crop_key)
        JOIN star.dim_crop_season s USING (crop_season_key) JOIN star.dim_date d ON d.date_key = a.year_start_date_key
        WHERE a.yield_t_ha > 0 AND s.season_name IN ('Kharif','Rabi','Whole Year')""").fetchall())
    wx = weather_features(conn)
    if wx.empty:
        return wx
    df = ag.merge(wx, on=["location_key", "year"]).sort_values(["location_key", "crop_name", "season_name", "year"])
    df = df[df.year <= TEST_MAX].copy()
    grp = ["location_key", "crop_name", "season_name"]
    tr = df[df.year <= TRAIN_MAX]
    stats_ = tr.groupby(grp).yield_t_ha.agg(g_mean="mean", g_n="count")
    df = df.join(stats_, on=grp)
    df = df[df.g_n >= 8].copy()  # need enough training history for a stable group baseline
    df["y_rel"] = df.yield_t_ha / df.g_mean
    df["lag1_rel"] = df.groupby(grp).y_rel.shift(1)
    df["lag1_yield"] = df.groupby(grp).yield_t_ha.shift(1)
    # weather anomalies relative to the location's TRAINING-period mean
    wtr = df[df.year <= TRAIN_MAX].groupby("location_key")[WEATHER_FEATS].mean()
    for c in WEATHER_FEATS:
        df[c + "_anom"] = df[c] - df.location_key.map(wtr[c])
    for s in ("Kharif", "Rabi", "Whole Year"):
        df[f"season_{s}"] = (df.season_name == s).astype(float)
    df["split"] = np.where(df.year <= TRAIN_MAX, "train", np.where(df.year <= VAL_MAX, "val", "test"))
    return df.dropna(subset=["lag1_rel"])


def trend_baseline(df: pd.DataFrame) -> pd.Series:
    """Per-group linear trend fitted on training years only, extrapolated."""
    pred = pd.Series(index=df.index, dtype=float)
    for _, g in df.groupby(["location_key", "crop_name", "season_name"]):
        tr = g[g.split == "train"]
        if len(tr) >= 5:
            b = np.polyfit(tr.year, tr.yield_t_ha, 1)
            pred.loc[g.index] = np.polyval(b, g.year)
        else:
            pred.loc[g.index] = g.g_mean
    return pred.clip(lower=0)


def main() -> dict:
    conn = db.connect()
    df = build(conn)
    if df.empty:
        print("no joinable weather/agri rows yet")
        return {}
    wfeat = [c + "_anom" for c in WEATHER_FEATS]
    seas = [c for c in df.columns if c.startswith("season_") and c != "season_name"]
    feat_full = wfeat + ["lag1_rel"] + seas
    tr, va, te = (df[df.split == s] for s in ("train", "val", "test"))
    y_te = te.yield_t_ha.to_numpy()
    preds = {"group_mean": te.g_mean.to_numpy(), "last_year": te.lag1_yield.to_numpy(), "group_trend": trend_baseline(df).loc[te.index].to_numpy()}
    models: dict = {}
    for name, feats, est in (
        ("ridge_weather_only", wfeat + seas, make_pipeline(StandardScaler(), Ridge(alpha=30.0))),
        ("ridge", feat_full, make_pipeline(StandardScaler(), Ridge(alpha=30.0))),
        ("hgb", feat_full, HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=150, l2_regularization=5.0, random_state=0)),
    ):
        est.fit(tr[feats], tr.y_rel)
        models[name] = (est, feats)
        preds[name] = est.predict(te[feats]) * te.g_mean.to_numpy()
    metrics = {k: reg_metrics(y_te, v) for k, v in preds.items()}
    # does weather add anything beyond the last-year signal? (paired comparison of absolute errors)
    err = {k: np.abs(y_te - v) for k, v in preds.items()}
    best_base = min(("group_mean", "last_year", "group_trend"), key=lambda k: metrics[k]["mae"])
    verdict = {k: dict(mae_vs_best_baseline_pct=round(100 * (metrics[k]["mae"] / metrics[best_base]["mae"] - 1), 1)) for k in ("ridge_weather_only", "ridge", "hgb")}
    by_city = {c: {m: round(float(np.mean(err[m][(te.city == c).to_numpy()])), 3) for m in preds} for c in te.city.unique()}
    sample = te.assign(**{f"pred_{k}": v for k, v in preds.items()})[["city", "crop_name", "season_name", "year", "yield_t_ha", "pred_hgb", "pred_ridge", "pred_group_mean"]].round(3)
    result = dict(n_train=len(tr), n_val=len(va), n_test=len(te), districts=sorted(df.city.unique().tolist()), crops=int(df.crop_name.nunique()),
                  metrics=metrics, best_baseline=best_base, vs_best_baseline=verdict, mae_by_city=by_city, test_period="2017-2021",
                  predictions=sample.head(800).to_dict("records"),
                  caveat="Few districts and years; weather is a single city point, not district-wide; irrigation, inputs and prices are not modelled.")

    conn.execute("DELETE FROM star.fact_model_evaluation WHERE model_key IN (SELECT model_key FROM star.dim_model WHERE risk_module=%s)", (RISK_C,))
    config.MODELS_DIR.mkdir(exist_ok=True)
    artifact = config.MODELS_DIR / f"agri_yield_{VERSION}.joblib"
    joblib.dump({k: v for k, v in models.items()}, artifact)
    for name, m in metrics.items():
        fam = "baseline" if name in ("group_mean", "last_year", "group_trend") else "linear" if name.startswith("ridge") else "gradient boosting"
        mk = upsert_model(conn, name, fam, RISK_C, "yield_t_ha", 0, fam == "baseline")
        store_eval(conn, mk, "overall", "all", None, m, len(y_te))
        if name in models:
            conn.execute("INSERT INTO ml.model_registry (model_key, artifact_path, train_period, val_period, test_period, n_train, features, params, metrics, code_version) "
                         "VALUES (%s,%s,'2002-2014','2015-2016','2017-2021',%s,%s,%s,%s,%s) ON CONFLICT (model_key) DO UPDATE SET metrics=EXCLUDED.metrics, trained_at=now(), artifact_path=EXCLUDED.artifact_path",
                         (mk, str(artifact.relative_to(config.ROOT)), len(tr), json.dumps(models[name][1]), json.dumps({}), json.dumps({"test": m}), code_version()))
    conn.execute("INSERT INTO ml.analysis_result (name, params, result) VALUES ('agri_model', '{}', %s) ON CONFLICT (name) DO UPDATE SET result=EXCLUDED.result, computed_at=now()",
                 (json.dumps(result, default=float),))
    conn.commit()
    print(json.dumps({k: result[k] for k in ("n_train", "n_val", "n_test", "districts", "metrics", "best_baseline", "vs_best_baseline")}, indent=1, default=float)[:2500])
    return result


if __name__ == "__main__":
    main()
