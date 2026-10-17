"""Risk A (heat) and Risk B (heavy rainfall) models: training, evaluation, registry.

Chronological split (no shuffling):  train 2002-2015 | validation 2016-2018 | test 2019-2022.
Validation data is used for model selection, probability calibration, conformal
interval width and decision thresholds; the test period is touched once for scoring.
"""
from __future__ import annotations

import io
import json
import subprocess
import time

import joblib
import numpy as np
import pandas as pd
import psycopg
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (average_precision_score, brier_score_loss, f1_score, mean_absolute_error,
                             mean_squared_error, precision_score, r2_score, recall_score, roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import config, db
from .features import ISSUE_HOURS, STATIC_COLS, attach_climatology, climatology, make_features

SPLITS = {"train": (2002, 2015), "val": (2016, 2018), "test": (2019, 2022)}
TEMP_HORIZONS = (6, 24, 72)
RAIN_HORIZONS = (24, 48, 72)
RAIN_THRESHOLD_CANDIDATES = (64.5, 35.5, 20.0)  # IMD "heavy" (24 h), "rather heavy", moderate-heavy
HI_EVENT_C = 32.0  # NWS "extreme caution" heat-index band and above
VERSION = "v1"
RISK_A, RISK_B, RISK_C = "A: Urban heat", "B: Heavy rainfall", "C: Agricultural weather"


def split_of(year: int) -> str:
    for name, (lo, hi) in SPLITS.items():
        if lo <= year <= hi:
            return name
    return "none"


def code_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "no-git"


def load_hourly(conn: psycopg.Connection, since_date_key: int = 0) -> pd.DataFrame:
    sql = """SELECT l.location_id, l.latitude, l.longitude, coalesce(l.elevation_m,0) AS elevation_m,
                    (to_date(f.date_key::text,'YYYYMMDD') + f.time_key * interval '1 hour') AS obs_time,
                    f.temperature_c, f.relative_humidity_pct, f.dew_point_c, f.pressure_msl_hpa, f.cloud_cover_pct,
                    f.wind_speed_kmh, f.wind_gust_kmh, f.wind_direction_deg, f.heat_index_c, f.apparent_temperature_c,
                    f.soil_moisture_m3m3, f.shortwave_radiation_wm2, f.precipitation_mm
             FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
             WHERE f.date_key >= %s
             ORDER BY l.location_id, obs_time"""
    return pd.DataFrame(conn.execute(sql, (since_date_key,)).fetchall())


def build_dataset(hourly: pd.DataFrame, rain_thr: float | None) -> pd.DataFrame:
    parts = []
    for loc, g in hourly.groupby("location_id", sort=False):
        g = g.set_index("obs_time").sort_index()
        f = make_features(g)
        for c in STATIC_COLS:
            f[c] = g[c].iloc[0]
        for h in TEMP_HORIZONS:
            f[f"y_temp_{h}"] = g["temperature_c"].shift(-h)
            f[f"y_hi_{h}"] = g["heat_index_c"].shift(-h)
        rain24 = g["precipitation_mm"].rolling(24, min_periods=24).sum()
        for h in RAIN_HORIZONS:
            f[f"y_rainamt_{h}"] = rain24.shift(-h)
        f["location_id"] = loc
        parts.append(f[f.index.hour.isin(ISSUE_HOURS)])
    df = pd.concat(parts)
    df["split"] = df.index.year.map(split_of)
    return df[df.split != "none"]


def pick_rain_threshold(df: pd.DataFrame) -> float:
    """Highest documented threshold that still leaves >= 100 training events."""
    tr = df[df.split == "train"]
    for thr in RAIN_THRESHOLD_CANDIDATES:
        if (tr["y_rainamt_24"] >= thr).sum() >= 100:
            return thr
    return RAIN_THRESHOLD_CANDIDATES[-1]


def reg_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return dict(mae=float(mean_absolute_error(y, p)), rmse=float(np.sqrt(mean_squared_error(y, p))), r2=float(r2_score(y, p)))


def cls_metrics(y: np.ndarray, prob: np.ndarray, thr: float | None) -> dict:
    out = dict(n=int(len(y)), prevalence=float(y.mean()), brier=float(brier_score_loss(y, prob)))
    if 0 < y.sum() < len(y):
        out.update(pr_auc=float(average_precision_score(y, prob)), roc_auc=float(roc_auc_score(y, prob)))
    if thr is not None:
        pred = prob >= thr
        out.update(precision=float(precision_score(y, pred, zero_division=0)), recall=float(recall_score(y, pred, zero_division=0)),
                   f1=float(f1_score(y, pred, zero_division=0)), threshold=float(thr))
    return out


def best_f1_threshold(y: np.ndarray, prob: np.ndarray) -> float:
    grid = np.unique(np.quantile(prob, np.linspace(0.5, 0.999, 120)))
    scores = [f1_score(y, prob >= t, zero_division=0) for t in grid]
    return float(grid[int(np.argmax(scores))])


def calibration_curve(y: np.ndarray, prob: np.ndarray, bins: int = 8) -> list[dict]:
    q = np.unique(np.quantile(prob, np.linspace(0, 1, bins + 1)))
    idx = np.clip(np.digitize(prob, q[1:-1]), 0, len(q) - 2)
    return [dict(mean_pred=float(prob[idx == b].mean()), observed=float(y[idx == b].mean()), n=int((idx == b).sum()))
            for b in range(len(q) - 1) if (idx == b).any()]


# --------------------------------------------------------------- regression
def hgb_candidates():
    return [dict(learning_rate=0.06, max_leaf_nodes=31, max_iter=250, l2_regularization=1.0),
            dict(learning_rate=0.1, max_leaf_nodes=63, max_iter=250, l2_regularization=1.0)]


def run_regression(df: pd.DataFrame, feats: list[str], target: str, base_col: str, clim_col: str) -> dict:
    """Fit ridge + HGB for one target; return predictions and artifacts for every model."""
    sets = {s: df[df.split == s] for s in ("train", "val", "test")}
    X = {s: d[feats].to_numpy(float) for s, d in sets.items()}
    y = {s: d[target].to_numpy(float) for s, d in sets.items()}
    ridge = make_pipeline(StandardScaler(), Ridge(alpha=10.0)).fit(X["train"], y["train"])
    best, best_mae = None, np.inf
    for params in hgb_candidates():
        m = HistGradientBoostingRegressor(random_state=0, **params).fit(X["train"], y["train"])
        mae = mean_absolute_error(y["val"], m.predict(X["val"]))
        if mae < best_mae:
            best, best_mae, best_params = m, mae, params
    q90 = float(np.quantile(np.abs(y["val"] - best.predict(X["val"])), 0.9))   # split-conformal half-width (90%)
    q90r = float(np.quantile(np.abs(y["val"] - ridge.predict(X["val"])), 0.9))
    preds = {
        "persistence": (sets["test"][base_col].to_numpy(float), None, None),
        "climatology": (sets["test"][clim_col].to_numpy(float), None, None),
        "ridge": (ridge.predict(X["test"]), ridge.predict(X["test"]) - q90r, ridge.predict(X["test"]) + q90r),
        "hgb": (best.predict(X["test"]), best.predict(X["test"]) - q90, best.predict(X["test"]) + q90),
    }
    return dict(models={"ridge": ridge, "hgb": best}, conformal={"ridge": q90r, "hgb": q90}, preds=preds,
                y_test=y["test"], test=sets["test"], hgb_params=best_params, n_train=len(sets["train"]))


# ------------------------------------------------------------ classification
def run_rain(df: pd.DataFrame, feats: list[str], h: int, thr: float) -> dict:
    ycol = f"event_{h}"
    sets = {s: df[df.split == s] for s in ("train", "val", "test")}
    X = {s: d[feats].to_numpy(float) for s, d in sets.items()}
    y = {s: d[ycol].to_numpy(int) for s, d in sets.items()}
    clim = df[df.split == "train"].groupby(["location_id", df[df.split == "train"].index.month])[ycol].mean().to_dict()

    def clim_p(d: pd.DataFrame) -> np.ndarray:
        return np.array([clim.get((l, m), 0.0) for l, m in zip(d.location_id, d.index.month)])

    logit = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=600)).fit(X["train"], y["train"])
    hgb = HistGradientBoostingClassifier(learning_rate=0.05, max_iter=250, max_leaf_nodes=15, l2_regularization=2.0, random_state=0)
    hgb.fit(X["train"], y["train"])
    out = {"climatology": dict(prob_val=clim_p(sets["val"]), prob_test=clim_p(sets["test"]), thr=None)}
    out["persistence"] = dict(prob_val=(sets["val"]["rain_24h"] >= thr).astype(float).to_numpy(),
                              prob_test=(sets["test"]["rain_24h"] >= thr).astype(float).to_numpy(), thr=0.5)
    cal = {}
    for name, m in (("logistic", logit), ("hgb", hgb)):
        pv, pt = m.predict_proba(X["val"])[:, 1], m.predict_proba(X["test"])[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(pv, y["val"])  # calibrate on validation only
        cal[name] = iso
        pv_c, pt_c = iso.predict(pv), iso.predict(pt)
        out[name] = dict(prob_val=pv_c, prob_test=pt_c, thr=best_f1_threshold(y["val"], pv_c))
    out["climatology"]["thr"] = best_f1_threshold(y["val"], out["climatology"]["prob_val"])
    return dict(results=out, models={"logistic": logit, "hgb": hgb}, calibrators=cal, y_test=y["test"], y_val=y["val"],
                test=sets["test"], n_train=len(sets["train"]))


# ----------------------------------------------------------------- storing
def upsert_model(conn, name, family, risk, target, h, baseline) -> int:
    return conn.execute(
        "INSERT INTO star.dim_model (model_name, model_family, risk_module, target, horizon_h, version, is_baseline) VALUES (%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (model_name, target, horizon_h, version) DO UPDATE SET model_family=EXCLUDED.model_family RETURNING model_key",
        (name, family, risk, target, h, VERSION, baseline)).fetchone()["model_key"]


def store_predictions(conn, model_key: int, h: int, test: pd.DataFrame, pred, lo, hi, prob, actual, loc_keys) -> None:
    """Persist held-out predictions (issue hour 12:00 only, to keep the fact table browsable)."""
    mask = (test.index.hour == 12)
    t = test[mask]
    issue = t.index
    target_t = issue + pd.Timedelta(hours=h)
    rows = pd.DataFrame({
        "model_key": model_key, "location_key": t.location_id.map(loc_keys).to_numpy(),
        "issue_date_key": issue.strftime("%Y%m%d").astype(int), "issue_time_key": issue.hour,
        "target_date_key": target_t.strftime("%Y%m%d").astype(int), "target_time_key": target_t.hour, "horizon_h": h,
        "predicted": np.asarray(pred)[mask], "pred_lower": None if lo is None else np.asarray(lo)[mask],
        "pred_upper": None if hi is None else np.asarray(hi)[mask], "probability": None if prob is None else np.asarray(prob)[mask],
        "actual": np.asarray(actual)[mask]})
    buf = io.StringIO()
    rows.to_csv(buf, index=False, header=False, na_rep="\\N")
    buf.seek(0)
    cols = "model_key,location_key,issue_date_key,issue_time_key,target_date_key,target_time_key,horizon_h,predicted,pred_lower,pred_upper,probability,actual"
    with conn.cursor().copy(f"COPY star.fact_prediction ({cols}) FROM STDIN WITH (FORMAT csv, NULL '\\N')") as cp:
        cp.write(buf.read())


def store_eval(conn, model_key, scope, value, location_key, metrics: dict, n: int) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO star.fact_model_evaluation (model_key, location_key, scope, scope_value, metric_name, metric_value, n_samples) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            [(model_key, location_key, scope, value, k, v, n) for k, v in metrics.items() if isinstance(v, float) and np.isfinite(v)])


def register(conn, model_key, artifact, train_n, feats, params, metrics) -> None:
    conn.execute(
        "INSERT INTO ml.model_registry (model_key, artifact_path, train_period, val_period, test_period, n_train, features, params, metrics, code_version) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (model_key) DO UPDATE SET artifact_path=EXCLUDED.artifact_path, trained_at=now(), "
        "n_train=EXCLUDED.n_train, features=EXCLUDED.features, params=EXCLUDED.params, metrics=EXCLUDED.metrics, code_version=EXCLUDED.code_version",
        (model_key, str(artifact.relative_to(config.ROOT)), "2002-2015", "2016-2018", "2019-2022", train_n,
         json.dumps(feats), json.dumps(params), json.dumps(metrics), code_version()))


def main() -> dict:
    t0 = time.time()
    conn = db.connect()
    hourly = load_hourly(conn)
    loc_keys = {r["location_id"]: r["location_key"] for r in conn.execute("SELECT location_id, location_key FROM star.dim_location")}
    df = build_dataset(hourly, None)
    train_mask = df.split == "train"
    # climatology from TRAINING rows only
    hourly_idx = hourly.set_index("obs_time")
    tr_h = hourly_idx[hourly_idx.index.year <= SPLITS["train"][1]]
    clim_t, clim_hi = climatology(tr_h, "temperature_c"), climatology(tr_h, "heat_index_c")
    df["clim_temp"] = np.concatenate([attach_climatology(d, loc, clim_t, "c").to_numpy() for loc, d in df.groupby("location_id", sort=False)])
    df["clim_hi"] = np.concatenate([attach_climatology(d, loc, clim_hi, "c").to_numpy() for loc, d in df.groupby("location_id", sort=False)])
    df["temp_anom"], df["hi_anom"] = df.temp - df.clim_temp, df.hi - df.clim_hi
    base_feats = [c for c in df.columns if c not in ("location_id", "split") and not c.startswith("y_") and not c.startswith("event_")]
    thr = pick_rain_threshold(df)
    for h in RAIN_HORIZONS:
        df[f"event_{h}"] = (df[f"y_rainamt_{h}"] >= thr).astype(float).where(df[f"y_rainamt_{h}"].notna())
    df = df.dropna(subset=base_feats + ["y_temp_72", "y_hi_72", "y_rainamt_72"]).copy()
    for h in RAIN_HORIZONS:
        df[f"event_{h}"] = df[f"event_{h}"].astype(int)
    print(f"dataset {df.shape}, rain threshold {thr} mm/24h, prevalence train {df[df.split=='train']['event_24'].mean():.4f}", flush=True)

    # clean slate for idempotent re-runs
    conn.execute("DELETE FROM star.fact_prediction"); conn.execute("DELETE FROM star.fact_model_evaluation"); conn.execute("DELETE FROM ml.model_registry")
    config.MODELS_DIR.mkdir(exist_ok=True)
    report: dict = {"rain_threshold_mm_24h": thr, "heat_event_hi_c": HI_EVENT_C, "heat": {}, "rain": {}}
    season_of = lambda idx: pd.Series(idx.month, index=idx).map(lambda m: "Winter" if m < 3 else "Pre-monsoon" if m < 6 else "Monsoon" if m < 10 else "Post-monsoon")

    # ---------------- Risk A
    for tname, base_col, clim_col, tgt_name in (("temp", "temp", "clim_temp", "temperature_c"), ("hi", "hi", "clim_hi", "heat_index_c")):
        for h in TEMP_HORIZONS:
            col = f"y_{tname}_{h}"
            r = run_regression(df, base_feats, col, base_col, clim_col)
            test, y = r["test"], r["y_test"]
            artifact = config.MODELS_DIR / f"heat_{tname}_{h}h_{VERSION}.joblib"
            joblib.dump(dict(models=r["models"], conformal=r["conformal"], features=base_feats, clim_temp=clim_t, clim_hi=clim_hi,
                             target=tgt_name, horizon_h=h, version=VERSION), artifact)
            rep = {}
            for name, (p, lo, hi) in r["preds"].items():
                fam = "baseline" if name in ("persistence", "climatology") else "linear" if name == "ridge" else "gradient boosting"
                mk = upsert_model(conn, name, fam, RISK_A, tgt_name, h, name in ("persistence", "climatology"))
                m = reg_metrics(y, p)
                if lo is not None:
                    m["interval_coverage_90"] = float(np.mean((y >= lo) & (y <= hi)))
                    m["interval_width"] = float(np.mean(hi - lo))
                if tname == "hi":  # heat-stress event: forecast HI >= 32 C
                    ev, pe = y >= HI_EVENT_C, p >= HI_EVENT_C
                    m.update(event_precision=float(precision_score(ev, pe, zero_division=0)), event_recall=float(recall_score(ev, pe, zero_division=0)),
                             event_f1=float(f1_score(ev, pe, zero_division=0)))
                store_eval(conn, mk, "overall", "all", None, m, len(y))
                for loc, idx in test.groupby("location_id").indices.items():
                    store_eval(conn, mk, "location", loc, loc_keys[loc], {k: v for k, v in reg_metrics(y[idx], p[idx]).items()}, len(idx))
                seas = season_of(test.index).to_numpy()
                for s in np.unique(seas):
                    ix = np.where(seas == s)[0]
                    store_eval(conn, mk, "season", s, None, reg_metrics(y[ix], p[ix]), len(ix))
                store_predictions(conn, mk, h, test, p, lo, hi, None, y, loc_keys)
                if name in ("ridge", "hgb"):
                    register(conn, mk, artifact, r["n_train"], base_feats, dict(r["hgb_params"]) if name == "hgb" else {"alpha": 10.0},
                             {"test": m, "conformal_halfwidth_90": r["conformal"][name]})
                rep[name] = m
            report["heat"][f"{tgt_name}_{h}h"] = rep
            print("A", tgt_name, h, {k: round(v["mae"], 3) for k, v in rep.items()}, flush=True)

    # ---------------- Risk B
    calib_curves = {}
    for h in RAIN_HORIZONS:
        r = run_rain(df, base_feats, h, thr)
        test, y = r["test"], r["y_test"]
        artifact = config.MODELS_DIR / f"rain_{h}h_{VERSION}.joblib"
        thr_h = {k: v["thr"] for k, v in r["results"].items()}
        joblib.dump(dict(models=r["models"], calibrators=r["calibrators"], thresholds=thr_h, features=base_feats, clim_temp=clim_t, clim_hi=clim_hi,
                         rain_threshold_mm=thr, horizon_h=h, version=VERSION), artifact)
        rep = {}
        for name, res in r["results"].items():
            fam = "baseline" if name in ("persistence", "climatology") else "linear" if name == "logistic" else "gradient boosting"
            tgt = f"rain_24h_ge_{thr:g}mm"
            mk = upsert_model(conn, name if name in ("persistence", "climatology") else f"{name}_calibrated", fam, RISK_B, tgt, h, name in ("persistence", "climatology"))
            prob = res["prob_test"]
            m = cls_metrics(y, prob, res["thr"])
            m["brier_skill_vs_climatology"] = 1 - m["brier"] / brier_score_loss(y, r["results"]["climatology"]["prob_test"])
            store_eval(conn, mk, "overall", "all", None, m, len(y))
            for loc, idx in test.groupby("location_id").indices.items():
                lm = cls_metrics(y[idx], prob[idx], res["thr"])
                store_eval(conn, mk, "location", loc, loc_keys[loc], {k: v for k, v in lm.items() if k in ("pr_auc", "brier", "prevalence", "recall", "precision")}, len(idx))
            seas = season_of(test.index).to_numpy()
            for s in np.unique(seas):
                ix = np.where(seas == s)[0]
                sm = cls_metrics(y[ix], prob[ix], res["thr"])
                store_eval(conn, mk, "season", s, None, {k: v for k, v in sm.items() if k in ("pr_auc", "brier", "prevalence")}, len(ix))
            store_predictions(conn, mk, h, test, prob, None, None, prob, y, loc_keys)
            if name in ("logistic", "hgb"):
                register(conn, mk, artifact, r["n_train"], base_feats, {"calibration": "isotonic (validation)", "rain_threshold_mm": thr, "decision_threshold": res["thr"]}, {"test": m})
                calib_curves[f"{name}_{h}h"] = calibration_curve(y, prob)
            rep[name] = m
        report["rain"][f"{h}h"] = rep
        print("B", h, {k: (round(v.get("pr_auc", float("nan")), 3), round(v["brier"], 4)) for k, v in rep.items()}, flush=True)

    conn.execute(
        "INSERT INTO ml.analysis_result (name, params, result) VALUES ('rain_calibration_curves', %s, %s) ON CONFLICT (name) DO UPDATE SET result=EXCLUDED.result, computed_at=now()",
        (json.dumps({"threshold_mm": thr}), json.dumps(calib_curves)))
    conn.commit()
    config.REPORTS_DIR.mkdir(exist_ok=True)
    (config.REPORTS_DIR / "ml_report.json").write_text(json.dumps(report, indent=2))
    print(f"done in {time.time()-t0:.0f}s")
    return report


if __name__ == "__main__":
    main()
