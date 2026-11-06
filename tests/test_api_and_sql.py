"""API, SQL/OLAP and inference tests. They run against the loaded PostgreSQL warehouse
(`python -m wx.etl_weather` etc. must have been run) and skip cleanly if it is absent."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wx import db, olap

try:
    _c = db.connect()
    _n = _c.execute("SELECT count(*) AS n FROM star.fact_weather_hourly").fetchone()["n"]
    _c.close()
except Exception:  # pragma: no cover
    _n = 0
pytestmark = pytest.mark.skipif(_n == 0, reason="warehouse not loaded")

from wx.api import app  # noqa: E402

client = TestClient(app)


def test_foreign_keys_hold():
    with db.connect() as c:
        orphans = c.execute("""SELECT count(*) AS n FROM star.fact_weather_hourly f LEFT JOIN star.dim_location l USING (location_key) WHERE l.location_key IS NULL""").fetchone()["n"]
    assert orphans == 0


def test_etl_is_idempotent_by_primary_key():
    with db.connect() as c:
        dup = c.execute("""SELECT count(*) AS n FROM (SELECT location_key, date_key, time_key FROM star.fact_weather_hourly GROUP BY 1,2,3 HAVING count(*) > 1) d""").fetchone()["n"]
        full = c.execute("SELECT count(*) AS n FROM star.fact_weather_hourly").fetchone()["n"]
        snow = c.execute("SELECT count(*) AS n FROM snow.fact_weather_hourly").fetchone()["n"]
    assert dup == 0 and full == snow


def test_rollup_equals_sum_of_drilldown():
    with db.connect() as c:
        top = olap.aggregate(c, "all", "all", ["total_rain_mm", "days"])["rows"][0]
        by_city = olap.aggregate(c, "all", "city", ["total_rain_mm", "days"])["rows"]
    assert sum(r["days"] for r in by_city) == top["days"]
    assert abs(sum(r["total_rain_mm"] for r in by_city) - top["total_rain_mm"]) <= len(by_city)  # per-row rounding


def test_slice_and_dice_change_results():
    with db.connect() as c:
        allr = olap.aggregate(c, "season", "all", ["days"])["rows"]
        sliced = olap.aggregate(c, "season", "all", ["days"], {"seasons": ["Monsoon"]})["rows"]
        diced = olap.aggregate(c, "year", "city", ["avg_temp_c"], {"cities": ["BOM", "DEL"], "year_from": 2010, "year_to": 2012})["rows"]
    assert len(sliced) == 1 and sliced[0]["imd_season"] == "Monsoon" and len(allr) == 4
    assert {r["city"] for r in diced} == {"Mumbai", "Delhi"} and {r["year"] for r in diced} == {2010, 2011, 2012}


def test_known_values_against_raw_sql():
    with db.connect() as c:
        a = olap.aggregate(c, "year", "city", ["max_temp_c"], {"cities": ["DEL"], "year_from": 2015, "year_to": 2015})["rows"][0]["max_temp_c"]
        b = c.execute("""SELECT round(max(temperature_c)::numeric,1) AS v FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
                         WHERE l.location_id='DEL' AND f.date_key BETWEEN 20150101 AND 20151231""").fetchone()["v"]
    assert a == float(b)


def test_olap_rejects_unknown_levels():
    with db.connect() as c:
        with pytest.raises(ValueError):
            olap.aggregate(c, "decade; DROP TABLE x", "city", ["days"])


def test_star_and_snowflake_queries_agree():
    with db.connect() as c:
        for cid in olap.COMPARISONS:
            assert olap.compare_schemas(c, cid)["results_identical"], cid


def test_endpoints_ok():
    for url in ["/api/health", "/api/meta", "/api/overview", "/api/history?city=BOM&grain=annual", "/api/etl/runs", "/api/warehouse/schema?kind=star",
                "/api/warehouse/schema?kind=snow", "/api/olap/aggregate?time_level=year&geo_level=region", "/api/olap/pivot", "/api/sql/questions",
                "/api/sql/run?id=season_temp", "/api/eda", "/api/ml/summary"]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text[:200])


def test_invalid_inputs_give_clear_errors():
    assert client.get("/api/history?city=BOM&grain=hourly").status_code == 400
    assert client.get("/api/warehouse/schema?kind=bogus").status_code == 400
    assert client.get("/api/warehouse/preview?kind=star&table=pg_user").status_code == 404
    assert client.get("/api/sql/run?id=nope").status_code == 404


def test_prediction_endpoint_contract():
    body = {"location_id": "BOM", "issue_time": "2021-06-15T12:00:00", "risk": "heat", "horizon_h": 24, "model": "hgb"}
    r = client.post("/api/predict", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["issue_time"] < j["target_time"] and j["results"]["temperature_c"]["lower_90"] < j["results"]["temperature_c"]["upper_90"]
    assert j["model_info"]["historical_test_metrics"]["mae"] > 0


def test_rain_probability_is_a_probability():
    r = client.post("/api/predict", json={"location_id": "DEL", "issue_time": "2021-07-20T06:00:00", "risk": "rain", "horizon_h": 24, "model": "hgb"})
    assert 0 <= r.json()["results"]["probability_heavy_rain"] <= 1


@pytest.mark.parametrize("patch,msg", [({"issue_time": "2021-06-15T13:00:00"}, "issue times"), ({"horizon_h": 5}, "horizon"), ({"issue_time": "2035-01-01T12:00:00"}, "within"),
                                       ({"model": "magic"}, "model"), ({"risk": "flood"}, "risk")])
def test_prediction_validation(patch, msg):
    body = {"location_id": "BOM", "issue_time": "2021-06-15T12:00:00", "risk": "heat", "horizon_h": 24, "model": "hgb", **patch}
    r = client.post("/api/predict", json=body)
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_no_future_leakage_in_features():
    """Features at time t must not change when later observations change."""
    import pandas as pd
    from wx.features import make_features
    idx = pd.date_range("2020-01-01", periods=300, freq="h")
    base = pd.DataFrame({c: 1.0 for c in ["temperature_c", "relative_humidity_pct", "dew_point_c", "pressure_msl_hpa", "cloud_cover_pct", "wind_speed_kmh", "wind_gust_kmh",
                                          "wind_direction_deg", "heat_index_c", "apparent_temperature_c", "soil_moisture_m3m3", "shortwave_radiation_wm2", "precipitation_mm"]}, index=idx)
    changed = base.copy()
    changed.iloc[250:] = 99.0
    a, b = make_features(base).iloc[200], make_features(changed).iloc[200]
    assert a.equals(b)


def test_history_detail_contract():
    j = client.get("/api/history/detail?city=BOM&start=2005&end=2015").json()
    assert len(j["annual"]) == 11 and len(j["month_box"]) == 12
    a = j["annual"][0]
    assert a["hi_caution"] + a["hi_extreme"] + a["hi_danger"] <= 366 and a["rain_mm"] >= a["monsoon_rain"] >= 0
    for b in j["month_box"]:
        assert b["min"] <= b["p05"] <= b["q1"] <= b["median"] <= b["q3"] <= b["p95"] <= b["max"]


def test_year_vs_normal_contract_and_validation():
    j = client.get("/api/history/year-vs-normal?city=DEL&year=2015").json()
    assert len(j["dates"]) == 365 == len(j["normal_t"]) == len(j["year_r"])
    assert j["year_r"] == sorted(j["year_r"])  # cumulative rain never decreases
    assert 0 < j["summary"]["rain_pct_of_normal"] < 400
    assert client.get("/api/history/year-vs-normal?city=DEL&year=1990").status_code == 400
    assert client.get("/api/history/records?city=DEL").status_code == 200


def test_risk_endpoints_contract():
    h = client.get("/api/risk/hazards?city=BOM").json()
    assert len(h["months"]) == 12 and h["annual"]["heavy_rain"] >= h["annual"]["extreme_rain"] >= 0
    c = client.get("/api/risk/cooling?city=DEL").json()
    assert len(c["monthly"]) == 12 and c["ranking"][0]["cdd_per_year"] >= c["ranking"][-1]["cdd_per_year"]
    heat = client.get("/api/ml/cases?kind=heat").json()["cases"]
    assert heat and all(r["actual"] >= 30 for r in heat) and heat == sorted(heat, key=lambda r: -r["actual"])
    rain = client.get("/api/ml/cases?kind=rain").json()
    assert rain["summary"]["caught"] <= rain["summary"]["events"] and 0 < rain["threshold"] < 1


@pytest.mark.parametrize("path", ["/api/history?city=XXX", "/api/history/distribution?city=XXX", "/api/risk/hazards?city=Atlantis",
                                  "/api/ml/predictions?location_id=ZZ&model=hgb&target=temperature_c&horizon_h=24"])
def test_unknown_city_is_404(path):
    r = client.get(path)
    assert r.status_code == 404 and "unknown city" in r.json()["detail"]


def test_city_accepts_id_or_name():
    assert client.get("/api/history?city=BOM").status_code == 200
    assert client.get("/api/live", params={"city": "Mumbai"}).status_code in (200, 503)  # 503 = external service down, still a known city


def test_dashboard_map_file_is_served():
    r = client.get("/data/india_states.geojson")
    assert r.status_code == 200
    names = {f["properties"]["name"] for f in r.json()["features"]}
    import pandas as pd
    from wx import config
    assert set(pd.read_csv(config.LOCATIONS_CSV).state) <= names  # every city's state has an outline


def test_rain_climatology_baseline_follows_the_month():
    """'Usual climate' for heavy rain is the training-period rate for that city AND month, as in wx.ml."""
    def prob(month):
        r = client.post("/api/predict", json={"location_id": "BOM", "issue_time": f"2021-{month:02d}-15T06:00:00", "risk": "rain", "horizon_h": 24, "model": "climatology"})
        assert r.status_code == 200
        return r.json()["results"]["probability_heavy_rain"]
    july, january = prob(7), prob(1)
    assert july > 0.02 and january < 0.005  # Mumbai: monsoon vs dry season (the old code returned one annual rate for every month)


@pytest.mark.parametrize("risk,model,target,name", [
    ("heat", "persistence", "heat_index_c", "persistence"), ("heat", "climatology", "heat_index_c", "climatology"),
    ("heat", "ridge", "heat_index_c", "ridge"), ("heat", "hgb", "heat_index_c", "hgb"),
    ("rain", "hgb", "rain_24h_ge_64.5mm", "hgb_calibrated"), ("rain", "climatology", "rain_24h_ge_64.5mm", "climatology")])
def test_model_info_describes_the_model_that_answered(risk, model, target, name):
    j = client.post("/api/predict", json={"location_id": "DEL", "issue_time": "2021-05-10T12:00:00", "risk": risk, "horizon_h": 24, "model": model}).json()
    mi = j["model_info"]
    assert (mi["name"], mi["target"]) == (name, target)
    summary = next(r for r in client.get("/api/ml/summary").json()["results"] if (r["model"], r["target"], r["horizon_h"]) == (name, target, 24))
    key = "mae" if risk == "heat" else "pr_auc"
    assert mi["historical_test_metrics"][key] == pytest.approx(summary["metrics"][key])
    assert mi["baseline"] is (model in ("persistence", "climatology"))


def test_ml_breakdown_accepts_risk_names():
    by_name = client.get("/api/ml/breakdown", params={"risk": "heat", "target": "temperature_c", "horizon_h": 24}).json()
    assert by_name and by_name == client.get("/api/ml/breakdown", params={"risk": "A", "target": "temperature_c", "horizon_h": 24}).json()
    assert client.get("/api/ml/breakdown", params={"risk": "%", "target": "temperature_c", "horizon_h": 24}).status_code == 400


def test_olap_rejects_unknown_measure():
    assert client.get("/api/olap/aggregate", params={"measures": "avg_temp_c,bogus"}).status_code == 400
    assert client.get("/api/olap/aggregate", params={"measures": "avg_temp_c,total_rain_mm"}).status_code == 200


def _observed(loc, ts, col):
    from wx import db
    with db.connect() as c:
        return c.execute(f"""SELECT f.{col} AS v FROM star.fact_weather_hourly f JOIN star.dim_location l USING (location_key)
                             WHERE l.location_id=%s AND f.date_key=%s AND f.time_key=%s""", (loc, int(ts.strftime("%Y%m%d")), ts.hour)).fetchone()["v"]


@pytest.mark.parametrize("h,back_h", [(6, 18), (24, 0), (72, 0)])
def test_no_change_baseline_uses_same_hour_of_day(h, back_h):
    """'No change' repeats the latest value at the target's hour of day: +6 h uses the reading 18 h before issue."""
    from datetime import datetime, timedelta
    issue = datetime(2021, 5, 10, 6)
    j = client.post("/api/predict", json={"location_id": "BOM", "issue_time": issue.isoformat(), "risk": "heat", "horizon_h": h, "model": "persistence"}).json()
    src = issue - timedelta(hours=back_h)
    assert j["results"]["temperature_c"]["predicted"] == pytest.approx(_observed("BOM", src, "temperature_c"), abs=0.01)
    assert j["results"]["heat_index_c"]["predicted"] == pytest.approx(_observed("BOM", src, "heat_index_c"), abs=0.01)


def test_heat_intervals_cover_about_90_percent():
    rows = [r for r in client.get("/api/ml/summary").json()["results"] if r["model"] in ("hgb", "ridge") and r["target"] in ("temperature_c", "heat_index_c")]
    assert len(rows) == 12
    for r in rows:
        assert 0.885 <= r["metrics"]["interval_coverage_90"] <= 0.915, (r["target"], r["horizon_h"], r["model"])


def test_live_interval_matches_stored_test_prediction():
    """/api/predict and the stored test-set predictions use the same rolling 90% width for a given issue time."""
    from wx import db
    j = client.post("/api/predict", json={"location_id": "DEL", "issue_time": "2021-05-10T12:00:00", "risk": "heat", "horizon_h": 24, "model": "hgb"}).json()
    live = j["results"]["temperature_c"]
    with db.connect() as c:
        s = c.execute("""SELECT p.predicted, p.pred_lower, p.pred_upper FROM star.fact_prediction p JOIN star.dim_model m USING (model_key)
                         JOIN star.dim_location l USING (location_key) WHERE l.location_id='DEL' AND m.model_name='hgb' AND m.target='temperature_c'
                         AND m.horizon_h=24 AND p.issue_date_key=20210510 AND p.issue_time_key=12""").fetchone()
    assert live["predicted"] == pytest.approx(s["predicted"], abs=0.01)
    assert live["upper_90"] - live["lower_90"] == pytest.approx(s["pred_upper"] - s["pred_lower"], abs=0.02)
