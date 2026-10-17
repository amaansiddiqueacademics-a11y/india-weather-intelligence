# Dataset feasibility report

| Need | Source | Verified |
|---|---|---|
| Hourly weather, 10 cities | Open-Meteo Historical Weather API (ECMWF ERA5 / ERA5-Land reanalysis). https://open-meteo.com/en/docs/historical-weather-api | Downloaded as CSV: 210 files (10 cities x 2002-2022), ~0.75 MB each, 14 variables, units checked by the ETL (0 mismatches). |
| District crop area, production, yield | India Data Portal, "Crop Wise Area Production Yield" (DES, Ministry of Agriculture). ODC-BY licence. | Downloaded: 455,359 rows, 16 columns, 1997-2022, all states. 2,552 rows fall in the 4 usable city districts; 2,529 accepted, 23 rejected by validation. |

## What the data is
- **Weather** is gridded reanalysis (model estimates assimilating observations), not station data. Each city is one grid cell (actual cell coordinates and elevation are stored in `dim_location`). Timestamps are local time (Asia/Kolkata, UTC+5:30, no DST). The series is complete: 0 missing values and 0 duplicates were found, so imputation steps are implemented but affected 0 rows.
- **Crops** are annual, district-level administrative statistics by crop and season (Kharif, Rabi, Whole Year, ...). Years are agricultural years (e.g. 2010-2011), stored against 1 April of the start year.

## Joinability
Weather (point, hourly) joins to crops (district, annual) only through the city-to-district mapping in `config/locations.csv`. Delhi, Mumbai, Kolkata, Chennai, Bengaluru and Hyderabad have no usable crop history in the file (missing district or fewer than 5 years), so the agriculture module covers Ahmedabad, Jaipur, Lucknow and Ludhiana. A city point is not a district average; this is a stated limitation.

## Task support
| Task | Supported? |
|---|---|
| Heat: temperature and heat-index forecasting | Yes (hourly, 21 years). Heat bands are a weather-derived indicator, not a health outcome. |
| Heavy rainfall exceedance | Yes, as a rainfall-threshold target. ERA5 smooths extremes, so events are likely under-represented. |
| Flood risk | No: no flood outcome data obtained. |
| Crop yield from weather | Exploratory only: 4 districts, limited years. |
| Electricity demand, health outcomes | Not attempted: no outcome dataset obtained. |
| Comparison with archived external forecasts | Not done. |

IMD station/gridded data and Kaggle crop files require manual registration or login and were not used.
