# Decisions

- **Task source.** `prompt.md` (India weather warehouse and risk platform) is the brief. The `CLAUDE.md` in the parent Downloads folder describes a different project (WeatherMind) and was used only for conventions (Python 3.12, FastAPI, pytest, decision log).
- **Database.** Local PostgreSQL 18 service (Docker daemon was not running). PostGIS is not used: locations are plain latitude/longitude columns. Schemas are applied from ordered `sql/*.sql` files with `IF NOT EXISTS`; no Alembic.
- **Primary weather source.** Open-Meteo Historical Weather API (ERA5/ERA5-Land reanalysis), hourly CSV, 14 variables, 2002-2022, 10 cities, local time Asia/Kolkata. Chosen because it is key-free, CSV, consistent across coordinates and gap-free. Trade-off: gridded reanalysis, not station observations.
- **Download limits.** The free API allows 5,000 weighted calls per hour. The downloader throttles, retries with exponential backoff and skips existing files, so it can be re-run safely.
- **Agricultural source.** India Data Portal "Crop Wise Area Production Yield" (district level, 1997-2022, ODC-BY). Only districts containing a supported city are loaded. Delhi, Mumbai, Kolkata, Chennai, Bengaluru (Urban) and Hyderabad have no usable crop history, so Risk C covers Ahmedabad, Jaipur, Lucknow and Ludhiana only.
- **Not obtained (disclosed, not faked).** Flood outcomes, electricity demand, health outcomes, archived external forecasts for comparison.
- **Star vs snowflake.** Same surrogate-key values in both so queries are comparable. Agricultural facts are at city grain in the star and at district grain in the snowflake (location -> district -> state -> region).
- **Rain target.** Event = at least 64.5 mm in the 24 h ending at the target time (IMD "heavy rain"), the highest candidate threshold with >= 100 training events. Probabilities are isotonic-calibrated on validation years; decision threshold maximises validation F1.
- **Heat target.** Temperature and heat index at +6/+24/+72 h; heat bands follow NWS heat-index thresholds. Weather-derived indicator only.
- **Uncertainty.** Split-conformal symmetric intervals from validation residuals (90%); empirical test coverage is reported.
- **Splits.** Train 2002-2015, validation 2016-2018, test 2019-2022 (agriculture: <=2014 / 2015-2016 / 2017-2021). Issue times 00/06/12/18 local.
- **Models.** scikit-learn Ridge/Logistic and HistGradientBoosting instead of LightGBM (not installed; avoids a new dependency).
- **Frontend.** Static SPA served by FastAPI (vanilla JS + Plotly), no build step.
