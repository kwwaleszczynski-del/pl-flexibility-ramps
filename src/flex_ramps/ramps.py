"""Residual load, ramps and their statistics.

All series are indexed by interval START in UTC on a regular grid (15 minutes or 1 hour); gaps are NaN. A ramp
over horizon h is labelled by its start t:

    ramp_h(t) = L(t + h) - L(t),   L = demand - PV - wind   (residual load)

Calendar grouping (days, months, years, hours of the day) uses Polish local time.

Usage:
    rl = residual_load(frame)
    r1 = ramp(rl, pd.Timedelta("1h"))
    ev = evening_ramps(rl)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "Europe/Warsaw"
LAT, LON = 52.0, 19.4          # approximate geographic centre of Poland, for sunset times
QUANTILES = {"p01": 0.01, "p05": 0.05, "p50": 0.50, "p95": 0.95, "p99": 0.99}
HORIZONS = {"15min": pd.Timedelta("15min"), "1h": pd.Timedelta("1h"), "3h": pd.Timedelta("3h")}
MIDDAY = (10, 16)              # local hours [10:00, 16:00) searched for the midday minimum
EVENING_START = pd.Timedelta("4h")   # evening ramps start between 4 h before sunset and sunset


def step_of(index: pd.DatetimeIndex) -> pd.Timedelta:
    """Grid step of an index (the most common difference between neighbours)."""
    diffs = pd.Series(index[1:] - index[:-1])
    if diffs.empty:
        raise ValueError("need at least two timestamps")
    return pd.Timedelta(diffs.mode().iloc[0])


def regular(series: pd.Series, step: pd.Timedelta | None = None) -> pd.Series:
    """Series on a complete grid from its first to its last timestamp; missing intervals become NaN."""
    step = step or step_of(series.index)
    grid = pd.date_range(series.index[0], series.index[-1], freq=step, name=series.index.name)
    return series.reindex(grid)


def residual_load(frame: pd.DataFrame, pv_scale: float = 1.0) -> pd.Series:
    """Demand minus PV and wind generation (MW); ``pv_scale`` multiplies the PV profile (scenarios)."""
    return (frame["demand"] - pv_scale * frame["pv"] - frame["wind"]).rename("residual")


def ramp(rl: pd.Series, horizon: pd.Timedelta, step: pd.Timedelta | None = None) -> pd.Series:
    """Change of the residual load over ``horizon``, labelled by the start of the ramp (NaN across gaps)."""
    s = regular(rl, step)
    step = step or step_of(s.index)
    k = horizon / step
    if k < 1 or abs(k - round(k)) > 1e-9:
        raise ValueError(f"horizon {horizon} is not a multiple of the step {step}")
    return (s.shift(-int(round(k))) - s).rename(f"ramp_{horizon}")


def local_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return index.tz_convert(TZ)


def distribution(values: pd.Series) -> dict[str, float]:
    """Count, quantiles, extremes and mean absolute value of a ramp sample."""
    v = values.dropna()
    out = {"n": float(len(v))}
    if v.empty:
        return out | {k: np.nan for k in (*QUANTILES, "min", "max", "mean_abs")}
    q = v.quantile(list(QUANTILES.values()))
    out.update({k: float(q.iloc[i]) for i, k in enumerate(QUANTILES)})
    out.update({"min": float(v.min()), "max": float(v.max()), "mean_abs": float(v.abs().mean())})
    return out


def period_keys(index: pd.DatetimeIndex, freq: str) -> pd.Index:
    """Local calendar label of each timestamp: 'YYYY' for freq='year', 'YYYY-MM' for freq='month'."""
    loc = local_index(index)
    if freq == "year":
        return pd.Index([f"{y:04d}" for y in loc.year])
    return pd.Index([f"{y:04d}-{m:02d}" for y, m in zip(loc.year, loc.month)])


def ramp_table(rl: pd.Series, horizons: dict[str, pd.Timedelta], freq: str) -> pd.DataFrame:
    """Ramp distribution per local calendar period; columns '<horizon>_<statistic>'."""
    rows: dict[str, dict] = {}
    for label, h in horizons.items():
        r = ramp(rl, h)
        for key, part in r.groupby(period_keys(r.index, freq)):
            stats = distribution(part)
            rows.setdefault(key, {}).update({f"{label}_{k}": v for k, v in stats.items()})
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def duration_curves(rl: pd.Series, n_points: int = 201) -> pd.DataFrame:
    """Residual-load duration curve per local calendar year: index = % of time exceeded, columns = years."""
    pct = np.linspace(0.0, 100.0, n_points)
    out = {}
    for year, part in rl.dropna().groupby(period_keys(rl.dropna().index, "year")):
        out[year] = np.quantile(part.to_numpy(), 1.0 - pct / 100.0)
    return pd.DataFrame(out, index=pd.Index(pct, name="pct_time"))


def sunset_utc(days: pd.DatetimeIndex, lat: float = LAT, lon: float = LON) -> pd.DatetimeIndex:
    """Sunset (UTC) on the given calendar dates, NOAA solar-position approximation (accurate to about a minute).

    ``days`` are naive midnights of the local dates; the result is tz-aware UTC.
    """
    doy = days.dayofyear.to_numpy().astype(float)
    g = 2.0 * np.pi / 365.0 * (doy - 1.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    phi = np.radians(lat)
    cos_ha = np.cos(np.radians(90.833)) / (np.cos(phi) * np.cos(decl)) - np.tan(phi) * np.tan(decl)
    ha = np.degrees(np.arccos(np.clip(cos_ha, -1.0, 1.0)))
    minutes = 720.0 - 4.0 * (lon - ha) - eqtime
    return (days + pd.to_timedelta(minutes, unit="min")).tz_localize("UTC")


def _local_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Naive local midnight of each timestamp (the local calendar date)."""
    return local_index(index).tz_localize(None).normalize()


def day_lengths(days: pd.DatetimeIndex, step: pd.Timedelta) -> pd.Series:
    """Number of grid intervals in each local day (92, 96 or 100 quarter-hours; 23, 24 or 25 hours)."""
    start = days.tz_localize(TZ)
    end = (days + pd.Timedelta(days=1)).tz_localize(TZ)
    return pd.Series(np.asarray((end - start) / step), index=days)


def evening_ramps(rl: pd.Series) -> pd.DataFrame:
    """Per local day: the steepest 1-hour and 3-hour residual-load rise starting in the 4 hours before sunset, and
    the lift from the midday minimum to the evening peak (maximum between 1 h before and 4 h after sunset, at most
    until midnight).

    A measure is NaN when its window has missing data or the day is not complete in the series.
    """
    s = regular(rl)
    t = s.index
    step = step_of(t)
    days = _local_days(t)
    ss = sunset_utc(days)                       # sunset of each timestamp's local day
    start_window = (t >= ss - EVENING_START) & (t <= ss)
    peak_window = (t >= ss - pd.Timedelta("1h")) & (t <= ss + pd.Timedelta("4h"))
    hour = local_index(t).hour
    midday = (hour >= MIDDAY[0]) & (hour < MIDDAY[1])
    key = pd.Index(days, name="day")
    unique = days.unique()
    full_day = (pd.Series(1, index=t).groupby(key).sum() == day_lengths(unique, step)).reindex(unique, fill_value=False)

    def complete(values: pd.Series, window: np.ndarray) -> pd.Series:
        ok = values.where(window).notna().groupby(key).sum() == pd.Series(window, index=t).groupby(key).sum()
        return ok.reindex(unique, fill_value=False) & full_day

    out = pd.DataFrame(index=pd.Index(unique, name="day"))
    out["sunset_local"] = sunset_utc(unique).tz_convert(TZ).strftime("%H:%M")
    for label, h in (("ramp_1h", pd.Timedelta("1h")), ("ramp_3h", pd.Timedelta("3h"))):
        r = ramp(s, h)
        out[label] = r.where(start_window).groupby(key).max(min_count=1)
        out.loc[~complete(r, start_window), label] = np.nan
    peak = s.where(peak_window).groupby(key).max(min_count=1)
    low = s.where(midday).groupby(key).min(min_count=1)
    out["evening_peak"] = peak.where(complete(s, peak_window))
    out["midday_min"] = low.where(complete(s, midday))
    out["lift"] = out["evening_peak"] - out["midday_min"]
    return out


def midday_minima(frame: pd.DataFrame, rl: pd.Series) -> pd.DataFrame:
    """Per local day: the minimum residual load in local hours [10:00, 16:00) and what the system did at that moment
    (demand, PV, wind and, when available, conventional generation and net import). Days whose window is not
    complete are left out."""
    s = regular(rl)
    hour = local_index(s.index).hour
    s = s[(hour >= MIDDAY[0]) & (hour < MIDDAY[1])]
    expected = (MIDDAY[1] - MIDDAY[0]) * pd.Timedelta("1h") / step_of(rl.index)
    key = pd.Index(_local_days(s.index), name="day")
    ok = s.notna().groupby(key).sum() == expected
    s = s[ok.reindex(key).to_numpy()].dropna()
    if s.empty:
        return pd.DataFrame(columns=["time_local", "residual"])
    key = pd.Index(_local_days(s.index), name="day")
    at = s.groupby(key).idxmin()
    out = pd.DataFrame(index=at.index)
    out["time_local"] = pd.DatetimeIndex(at.to_numpy()).tz_convert(TZ).strftime("%H:%M")
    out["residual"] = s.loc[at.to_numpy()].to_numpy()
    for col in ("demand", "pv", "wind", "conv", "net_import"):
        if col in frame:
            out[col] = frame[col].reindex(at.to_numpy()).to_numpy()
    return out


def monthly_profile(rl: pd.Series, horizon: pd.Timedelta = pd.Timedelta("1h")) -> pd.DataFrame:
    """Mean ramp by local month (rows, 'YYYY-MM') and local start time of day (columns 'HH:MM')."""
    r = ramp(rl, horizon).dropna()
    loc = local_index(r.index)
    tod = pd.Index([f"{h:02d}:{m:02d}" for h, m in zip(loc.hour, loc.minute)], name="time")
    table = r.groupby([period_keys(r.index, "month"), tod]).mean().unstack()
    return table.reindex(columns=sorted(table.columns))


def trend(values: pd.Series) -> dict[str, float]:
    """OLS slope per step of the index position (e.g. per year for annual values) with the fitted end points."""
    v = values.dropna()
    if len(v) < 2:
        return {"slope": np.nan, "n": float(len(v))}
    x = np.arange(len(v), dtype=float)
    slope, intercept = np.polyfit(x, v.to_numpy(dtype=float), 1)
    return {"slope": float(slope), "intercept": float(intercept), "n": float(len(v)),
            "first": str(v.index[0]), "last": str(v.index[-1])}
