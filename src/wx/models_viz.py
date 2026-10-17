"""Model-explanation data for the dashboard: coefficients, real decision trees, response curves, importance.

Everything is computed from the saved model artifacts and the 2019-2022 test period in the warehouse.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance

from . import db, ml, predict as pr
from .features import attach_climatology

TARGETS = {"temp": ("heat_temp_{h}h_v1.joblib", "y_temp_{h}", "Temperature", "°C"),
           "hi": ("heat_hi_{h}h_v1.joblib", "y_hi_{h}", "Heat index", "°C"),
           "rain": ("rain_{h}h_v1.joblib", "event_{h}", "Heavy rain (>= 64.5 mm in 24 h)", "probability")}
VALID_H = {"temp": (6, 24, 72), "hi": (6, 24, 72), "rain": (24, 48, 72)}


def _check(target: str, h: int) -> None:
    if target not in TARGETS or h not in VALID_H[target]:
        raise pr.PredictionError(f"target must be one of {list(TARGETS)} with horizon in {VALID_H.get(target)}")


@lru_cache(maxsize=1)
def _test_frame() -> pd.DataFrame:
    """Test-period feature rows for all cities (without climatology features, added per artifact)."""
    conn = db.connect()
    try:
        hourly = ml.load_hourly(conn, since_date_key=20181201)
    finally:
        conn.close()
    df = ml.build_dataset(hourly, None)
    return df[df.split == "test"].copy()


@lru_cache(maxsize=12)
def sample(target: str, h: int, n: int = 2500) -> tuple[pd.DataFrame, dict]:
    _check(target, h)
    art = pr._artifact(TARGETS[target][0].format(h=h))
    df = _test_frame().copy()
    for col, key in (("clim_temp", "clim_temp"), ("clim_hi", "clim_hi")):
        df[col] = np.concatenate([attach_climatology(d, loc, art[key], "c").to_numpy() for loc, d in df.groupby("location_id", sort=False)])
    df["temp_anom"], df["hi_anom"] = df.temp - df.clim_temp, df.hi - df.clim_hi
    ycol = TARGETS[target][1].format(h=h)
    if target == "rain":
        amt = df[f"y_rainamt_{h}"]
        df["y"] = (amt >= art["rain_threshold_mm"]).astype(float).where(amt.notna())
    else:
        df["y"] = df[ycol]
    df = df.dropna(subset=art["features"] + ["y"])
    if target == "rain":  # keep every event so the response curve is visible, plus a random slice of non-events
        pos, neg = df[df.y == 1], df[df.y == 0]
        df = pd.concat([pos.sample(min(len(pos), 400), random_state=0), neg.sample(min(len(neg), n), random_state=0)])
    else:
        df = df.sample(min(len(df), n), random_state=0)
    return df, art


def _predict(art: dict, target: str, name: str, X: np.ndarray) -> np.ndarray:
    if target == "rain":
        raw = art["models"][name].predict_proba(X)[:, 1]
        return art["calibrators"][name].predict(raw)
    return art["models"][name].predict(X)


def _coefs(art: dict, target: str) -> dict:
    name = "ridge" if target != "rain" else "logistic"
    pipe = art["models"][name]
    est = pipe.steps[-1][1]
    coef = est.coef_.ravel()
    rows = sorted(zip(art["features"], coef), key=lambda t: -abs(t[1]))[:12]
    return dict(model="Ridge regression" if target != "rain" else "Logistic regression", intercept=float(np.ravel(est.intercept_)[0]),
                coefficients=[dict(feature=f, value=float(v)) for f, v in rows], note="Inputs are standardised, so each bar is the effect of a one-standard-deviation change.")


@lru_cache(maxsize=12)
def overview(target: str, h: int) -> dict:
    df, art = sample(target, h)
    feats = art["features"]
    X = df[feats].to_numpy(float)
    xs, y = df["temp" if target != "rain" else "rain_72h"].to_numpy(float), df["y"].to_numpy(float)
    keep = np.random.default_rng(0).choice(len(df), min(len(df), 700), replace=False) if target != "rain" else np.arange(len(df))
    ols = None
    if target != "rain":
        b, a = np.polyfit(xs, y, 1)
        r2 = 1 - np.sum((y - (a + b * xs)) ** 2) / np.sum((y - y.mean()) ** 2)
        ols = dict(slope=float(b), intercept=float(a), r2=float(r2))
    # permutation importance of the boosted model (on a 1500-row slice)
    k = min(len(df), 1500)
    idx = np.random.default_rng(1).choice(len(df), k, replace=False)
    scoring = "average_precision" if target == "rain" else "neg_mean_absolute_error"
    est = art["models"]["hgb"]
    pi = permutation_importance(est, X[idx], y[idx], scoring=scoring, n_repeats=2, random_state=0)
    imp = sorted(zip(feats, pi.importances_mean), key=lambda t: -t[1])[:12]
    pts = [dict(x=float(xs[i]), y=float(y[i])) for i in keep]
    return dict(target=target, horizon_h=h, label=TARGETS[target][2], unit=TARGETS[target][3], n_rows=len(df), linear=_coefs(art, target),
                scatter=dict(x_feature="temp" if target != "rain" else "rain_72h", points=pts, ols=ols),
                importance=dict(metric="rise in error when the input is shuffled" if target != "rain" else "drop in PR-AUC when the input is shuffled",
                                rows=[dict(feature=f, value=float(v)) for f, v in imp]),
                ensemble=dict(n_trees=len(art["models"]["hgb"]._predictors), features=len(feats), conformal_halfwidth=art.get("conformal", {}).get("hgb")))


def _tree_nodes(model, feats: list[str], max_depth: int) -> list[dict]:
    nodes = model._predictors[0][0].nodes
    out = []
    for i, n in enumerate(nodes):
        if n["depth"] > max_depth:
            continue
        leaf = bool(n["is_leaf"]) or bool(n["depth"] == max_depth)
        out.append(dict(id=i, depth=int(n["depth"]), leaf=leaf, count=int(n["count"]), value=float(n["value"]),
                        feature=None if bool(n["is_leaf"]) else feats[int(n["feature_idx"])], threshold=None if bool(n["is_leaf"]) else float(n["num_threshold"]),
                        left=None if leaf else int(n["left"]), right=None if leaf else int(n["right"])))
    return out


def _path(model, x: np.ndarray) -> list[int]:
    nodes, i, path = model._predictors[0][0].nodes, 0, [0]
    while not nodes[i]["is_leaf"]:
        n = nodes[i]
        i = int(n["left"]) if x[int(n["feature_idx"])] <= n["num_threshold"] else int(n["right"])
        path.append(i)
    return path


def _row(conn, target: str, h: int, location_id: str, issue: datetime, overrides: dict) -> tuple[dict, np.ndarray, dict]:
    art = pr._artifact(TARGETS[target][0].format(h=h))
    row, _ = pr._row_features(conn, location_id, issue, art)
    r = row[art["features"]].iloc[0].copy()
    for k, v in (overrides or {}).items():
        if k in r.index:
            r[k] = float(v)
    if "temp" in (overrides or {}):
        r["temp_anom"] = r["temp"] - row["clim_temp"].iloc[0]
    return art, r.to_numpy(float), {k: float(r[k]) for k in art["features"]}


def explain_row(location_id: str, issue: datetime, target: str, h: int, overrides: dict, sweep: str | None = None) -> dict:
    _check(target, h)
    conn = db.connect()
    try:
        rng = conn.execute("SELECT min(date_key) AS a, max(date_key) AS b FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key) WHERE l.location_id=%s", (location_id,)).fetchone()
        if rng["a"] is None:
            raise pr.PredictionError(f"no warehouse data for {location_id}")
        pr.validate_request(location_id, issue, "rain" if target == "rain" else "heat", h, "hgb", (datetime.strptime(str(rng["a"]), "%Y%m%d"), datetime.strptime(str(rng["b"]), "%Y%m%d").replace(hour=23)))
        art, x, feats = _row(conn, target, h, location_id, issue, overrides)
        t_target = issue + timedelta(hours=h)
        if t_target <= datetime.strptime(str(rng["b"]), "%Y%m%d").replace(hour=23):
            if target == "rain":
                r2 = conn.execute("""SELECT sum(f.precipitation_mm) AS s, count(*) AS n FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
                    WHERE l.location_id=%s AND (to_date(f.date_key::text,'YYYYMMDD') + f.time_key * interval '1 hour') > %s AND (to_date(f.date_key::text,'YYYYMMDD') + f.time_key * interval '1 hour') <= %s""", (location_id, t_target - timedelta(hours=24), t_target)).fetchone()
                observed = float(r2["s"]) if r2["n"] == 24 else None
            else:
                observed = pr._observed(conn, location_id, t_target, "temperature_c" if target == "temp" else "heat_index_c")
                observed = None if observed is None else float(observed)
        else:
            observed = None
    finally:
        conn.close()
    X = x.reshape(1, -1)
    hgb = art["models"]["hgb"]
    lin_name = "ridge" if target != "rain" else "logistic"
    out = dict(observed=observed, target_time=t_target.isoformat(), conformal=art.get("conformal"), rain_threshold_mm=art.get("rain_threshold_mm"), thresholds=art.get("thresholds"), features=feats, ridge=float(_predict(art, target, "ridge" if target != "rain" else "logistic", X)[0]), hgb=float(_predict(art, target, "hgb", X)[0]), linear_name=lin_name,
               path=_path(hgb, x), baseline_persistence=float(feats["temp" if target == "temp" else "hi"]) if target != "rain" else None)
    # staged predictions: how the boosted ensemble converges as trees are added
    if target == "rain":
        staged = [float(p[0, 1]) for p in hgb.staged_predict_proba(X)]
        staged = [float(art["calibrators"]["hgb"].predict([p])[0]) for p in staged[::5]]
    else:
        staged = [float(p[0]) for p in hgb.staged_predict(X)][::5]
    out["staged"] = dict(step=5, values=staged)
    out["tree"] = dict(nodes=_tree_nodes(hgb, art["features"], 3), baseline=float(np.ravel(hgb._baseline_prediction)[0]), n_trees=len(hgb._predictors))
    if sweep and sweep in art["features"]:
        df, _ = sample(target, h)
        lo, hi = np.percentile(df[sweep], [2, 98])
        grid = np.linspace(lo, hi, 40)
        M = np.repeat(X, len(grid), axis=0)
        j = art["features"].index(sweep)
        M[:, j] = grid
        if sweep == "temp" and "temp_anom" in art["features"]:
            M[:, art["features"].index("temp_anom")] = grid - feats["clim_temp"]
        out["sweep"] = dict(feature=sweep, x=grid.tolist(), ridge=_predict(art, target, lin_name, M).tolist(), hgb=_predict(art, target, "hgb", M).tolist(), current=feats[sweep])
    return out
