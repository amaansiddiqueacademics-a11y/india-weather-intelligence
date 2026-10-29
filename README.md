# India Weather Intelligence: A Weather Data Warehouse and Data Analysis Project

A **data analysis and data warehousing project** on 21 years of hourly weather for 10 Indian cities. It covers the full workflow: a validated ETL into a PostgreSQL star-schema warehouse, SQL/OLAP and exploratory analysis, forecast models for heat and heavy rain tested honestly against simple baselines, and a FastAPI backend feeding an interactive dashboard.

**Data:** ECMWF ERA5 hourly weather (2002-2022) via Open-Meteo, plus district crop statistics from the India Data Portal.

The free Open-Meteo archive API has a daily request quota per IP address. If `wx.download` reports "Daily API request limit exceeded", re-run it after the quota resets (00:00 UTC, 05:30 IST); files already saved are skipped.

## Pipeline

```
Open-Meteo ERA5 CSVs + India Data Portal crop CSV
  -> ETL (validate, clean, transform, load; idempotent; run logs and quarantine)
  -> PostgreSQL: star schema + snowflake schema + materialised views
  -> SQL / OLAP  ->  EDA  ->  ML models + evaluation + registry
  -> FastAPI (/api/*)  ->  static dashboard (web/)
```

## Cities

Delhi, Mumbai, Kolkata, Chennai, Bengaluru, Hyderabad, Ahmedabad, Jaipur, Lucknow, Ludhiana (see `config/locations.csv`).

## Risk applications

| Risk | What it does | Notes |
|---|---|---|
| Urban heat | Temperature and heat index at +6 / +24 / +72 h with 90% conformal intervals | Weather-derived indicator, not health outcomes |
| Heavy rainfall | Probability of at least 64.5 mm in 24 h (IMD "heavy rain") at 24 / 48 / 72 h, isotonic-calibrated | Threshold chosen for >= 100 training events |
| Agriculture | Yield vs weather for Ahmedabad, Jaipur, Lucknow, Ludhiana | Weather models currently lose to a last-year-yield baseline; reported as is |

Electricity demand, flood and health outcome data were not obtained. The Early warnings tab shows cooling degree-days as a clearly labelled weather indicator only.

## Run

Requires Python 3.12+ (tested on 3.12 and 3.13) and a local PostgreSQL 16+ (tested on 16 and 18; user `postgres`, trust auth on 127.0.0.1; override with `WX_DSN`).

```
pip install -r requirements.txt
cd src
python -m wx.download      # crop CSV + 210 weather CSVs into data/raw (rate-limited, resumable)
python -m wx.etl_weather   # validate, clean, transform, load (idempotent)
python -m wx.etl_agri
python -m wx.warehouse     # snowflake schema and materialised views
python -m wx.eda
python -m wx.ml            # heat and rain models, registry, evaluation
python -m wx.ml_agri
python -m wx.warehouse     # re-run once so the snowflake also gets the model predictions and metrics
python -m uvicorn wx.api:app --port 8077   # dashboard at http://localhost:8077
cd .. && pytest            # 37 tests
```

Splits: train 2002-2015, validation 2016-2018, test 2019-2022 (agriculture: <=2014 / 2015-2016 / 2017-2021). No random shuffling.

## Dashboard

Pages follow a story order: Overview -> History -> Data pipeline -> Warehouse -> SQL and OLAP -> Early warnings -> All models -> Methodology.

Each model result has one home, so pages do not repeat each other:

| Page | What it shows about models |
|---|---|
| Overview | A past-day forecast test (split of years, step-by-step forecast, headline result, predicted-vs-actual proof) |
| Early warnings | Hazard context per risk (heat, rain, crops, power) and a try-a-day tool |
| All models | The single full scoreboard: every model, look-ahead and metric, plus what each model is |
| Methodology | The approach, rules, benefits and limits |

Models use the same names everywhere: **Decision-tree model**, **Linear model**, and the baselines **No change** and **Usual climate**.

Handy deep links:

- `#/overview?scene=clear&city=HYD` preview a scene for any city
- `#/history?city=BLR`
- `#/risk?tab=heat|rain|agri|power`

If a browser shows stale files after editing `web/`, bump the `?v=` stamp in `web/index.html`.

## API

All under `/api`; interactive docs at `/docs`.

| Area | Endpoints |
|---|---|
| Meta | `meta`, `health`, `overview`, `live`, `live/air`, `normal` |
| Pipeline extras | `etl/inventory`, `etl/source`, `etl/stress-test`, `etl/reconciliation`, `etl/flagged`, `etl/code` |
| History | `history`, `history/distribution`, `history/on-this-day`, `history/records`, `history/detail`, `history/year-vs-normal` |
| Risk and ML | `risk/hazards`, `risk/events`, `risk/crops`, `risk/signals`, `risk/cooling`, `models/crop`, `ml/summary`, `ml/breakdown`, `ml/predictions`, `ml/cases`, `ml/agri`, `POST predict`, `models/overview`, `POST models/explain` |
| Pipeline | `etl/runs`, `etl/run/{run_id}`, `eda` |
| Warehouse | `warehouse/schema`, `warehouse/preview`, `warehouse/profile`, `warehouse/trace`, `warehouse/compare`, `warehouse/comparisons` |
| OLAP and SQL | `olap/meta`, `olap/aggregate`, `olap/pivot`, `sql/questions`, `sql/run` |

## Layout

| Path | Contents |
|---|---|
| `sql/` | DDL: schemas, star, snowflake |
| `src/wx/` | download, ETL, warehouse, OLAP, EDA, features, ML, prediction, API |
| `web/` | Static SPA (vanilla JS + Plotly, no build step); one JS file per page. `web/data/` holds the India state outlines, `web/img/landmarks/` the offline fallback city photos |
| `tests/` | ETL and API/SQL tests (37 tests; the API/SQL ones need the loaded warehouse) |
| `models/` | Saved model artifacts (`.joblib`) |
| `reports/` | Logs and JSON reports |
| `.gitignore` | Keeps raw data (`/data/` at the repo root), logs and private notes (`prompt.md`) out of git |
| `docs/` | `DECISIONS.md` (sources, scope, trade-offs), `DATASET_FEASIBILITY.md` |

## Limitations

- ERA5 is gridded reanalysis, not station observation.
- scikit-learn Ridge/Logistic and HistGradientBoosting are used instead of LightGBM.
- No archived external forecasts, so comparison is against baselines, not a third-party forecast.
- Delhi, Mumbai, Kolkata, Chennai, Bengaluru and Hyderabad have no usable crop history, so agriculture covers four cities.

See `docs/DECISIONS.md` and the dashboard's Methodology page for details.

## Credits

- State outlines: [DataMeet India maps](https://github.com/datameet/maps) (CC BY 2.5 India), simplified.
- City photos: Wikipedia / Wikimedia Commons (loaded live; bundled thumbnails are used when Wikipedia is unreachable).
