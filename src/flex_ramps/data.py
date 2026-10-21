"""Analysis frames built from the two PSE sources.

    quarter_hourly(api)        15-minute frame from the reports API (14 June 2024 onwards)
    hourly(api, legacy)        hourly frame: legacy export up to 13 June 2024, hourly means of the API afterwards

Columns (MW): demand, pv, wind, conv (domestic generation other than PV and wind), net_import (+ = import).
The index is the interval start in UTC on a complete grid; missing intervals are NaN.

Usage:
    q = quarter_hourly(api_frame)
    h = hourly(api_frame, legacy_frame)
"""
from __future__ import annotations

import pandas as pd

from flex_ramps.pse_api import API_START
from flex_ramps.ramps import TZ

COLUMNS = ["demand", "pv", "wind", "conv", "net_import"]


def _grid(frame: pd.DataFrame, step: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    grid = pd.date_range(frame.index[0], frame.index[-1], freq=step, name="ts_utc")
    return frame.reindex(grid)


def quarter_hourly(api: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=api.index)
    out["demand"] = api["demand"]
    out["pv"] = api["pv"]
    out["wind"] = api["wind"]
    out["conv"] = api["jg"] + api["jnwrb"] - api["pv"] - api["wind"]
    out["net_import"] = api["swm_p"] + api["swm_np"]
    return _grid(out[COLUMNS].sort_index(), "15min")


def hourly_from_quarters(q: pd.DataFrame) -> pd.DataFrame:
    """Hourly means of complete hours (all four quarter-hours present)."""
    if q.empty:
        return q
    groups = q.resample("1h")
    mean = groups.mean()
    count = groups.count()
    return mean.where(count == 4)


def legacy_columns(legacy: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=legacy.index)
    out["demand"] = legacy["demand"]
    out["pv"] = legacy["pv"]
    out["wind"] = legacy["wind"]
    out["conv"] = legacy["jwcd"] + legacy["njwcd"] - legacy["pv"] - legacy["wind"]
    out["net_import"] = legacy["swm_p"] + legacy["swm_np"]
    return out[COLUMNS]


def hourly(api: pd.DataFrame, legacy: pd.DataFrame | None) -> pd.DataFrame:
    seam = pd.Timestamp(API_START).tz_localize(TZ).tz_convert("UTC")
    parts = []
    if legacy is not None and not legacy.empty:
        old = legacy_columns(legacy)
        parts.append(old[old.index < seam])
    q = quarter_hourly(api)
    if not q.empty:
        h = hourly_from_quarters(q)
        parts.append(h[h.index >= seam])
    if not parts:
        return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], tz="UTC", name="ts_utc"))
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return _grid(out, "1h")


def first_complete_month(frame: pd.DataFrame, columns=("demand", "pv", "wind"), min_coverage: float = 0.9) -> str | None:
    """First local month 'YYYY-MM' in which all ``columns`` cover at least ``min_coverage`` of the month's intervals."""
    ok = frame[list(columns)].notna().all(axis=1)
    if ok.empty:
        return None
    step = pd.Series(ok.index[1:] - ok.index[:-1]).mode().iloc[0] if len(ok) > 1 else pd.Timedelta("1h")
    loc = ok.index.tz_convert(TZ)
    count = ok.groupby([loc.year, loc.month]).sum()
    for (y, m), n in count.items():
        start = pd.Timestamp(y, m, 1).tz_localize(TZ)
        expected = (start + pd.offsets.MonthBegin(1) - start) / step
        if n >= min_coverage * expected:
            return f"{y:04d}-{m:02d}"
    return None
