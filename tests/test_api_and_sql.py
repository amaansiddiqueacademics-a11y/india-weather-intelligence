"""API, SQL/OLAP and inference tests. They run against the loaded PostgreSQL warehouse
(`python -m wx.etl_weather` etc. must have been run) and skip cleanly if it is absent."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from wx import db, olap, predict as pr

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
