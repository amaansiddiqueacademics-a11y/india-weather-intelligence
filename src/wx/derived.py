"""Derived meteorological variables (pure functions, unit-tested)."""
from __future__ import annotations

import numpy as np


def heat_index_c(temp_c: np.ndarray, rh_pct: np.ndarray) -> np.ndarray:
    """NWS heat index (Rothfusz regression with Steadman simple form below ~80 F).

    Returns degrees Celsius. Defined for warm conditions; below ~26 C it converges
    to (approximately) the air temperature. It is a weather-derived indicator,
    not a medical measure.
    """
    t = np.asarray(temp_c, dtype=float) * 9 / 5 + 32
    r = np.asarray(rh_pct, dtype=float)
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + r * 0.094)
    full = (-42.379 + 2.04901523 * t + 10.14333127 * r - 0.22475541 * t * r
            - 6.83783e-3 * t**2 - 5.481717e-2 * r**2 + 1.22874e-3 * t**2 * r
            + 8.5282e-4 * t * r**2 - 1.99e-6 * t**2 * r**2)
    low_rh = (r < 13) & (t >= 80) & (t <= 112)
    adj_low = np.where(low_rh, -((13 - r) / 4) * np.sqrt(np.clip((17 - np.abs(t - 95)) / 17, 0, None)), 0.0)
    high_rh = (r > 85) & (t >= 80) & (t <= 87)
    adj_high = np.where(high_rh, ((r - 85) / 10) * ((87 - t) / 5), 0.0)
    hi_f = np.where((simple + t) / 2 >= 80, full + adj_low + adj_high, simple)
    return (hi_f - 32) * 5 / 9


def wet_bulb_c(temp_c: np.ndarray, rh_pct: np.ndarray) -> np.ndarray:
    """Stull (2011) wet-bulb approximation, valid for RH 5-99 % and -20..50 C."""
    t = np.asarray(temp_c, dtype=float)
    r = np.clip(np.asarray(rh_pct, dtype=float), 5, 99)
    return (t * np.arctan(0.151977 * np.sqrt(r + 8.313659)) + np.arctan(t + r)
            - np.arctan(r - 1.676331) + 0.00391838 * r**1.5 * np.arctan(0.023101 * r)
            - 4.686035)


# Heat categories follow the NWS heat-index bands (converted to C). Weather-derived only.
HEAT_BANDS = [(54.0, "Extreme danger"), (41.0, "Danger"), (32.0, "Extreme caution"), (27.0, "Caution")]


def heat_category(hi_c: float) -> str:
    for threshold, name in HEAT_BANDS:
        if hi_c >= threshold:
            return name
    return "Low"


def imd_season(month: int) -> str:
    """IMD seasons: winter Jan-Feb, pre-monsoon Mar-May, monsoon Jun-Sep, post-monsoon Oct-Dec."""
    if month in (1, 2):
        return "Winter"
    if month in (3, 4, 5):
        return "Pre-monsoon"
    if month in (6, 7, 8, 9):
        return "Monsoon"
    return "Post-monsoon"
