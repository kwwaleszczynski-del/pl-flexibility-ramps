"""Fundamental data of the PSE reports API, read with the pse-opendata client (https://pypi.org/project/pse-opendata/).

Report ``his-wlk-cal`` ("Wielkosci podstawowe", fundamental data), 15-minute intervals, business dates in
Europe/Warsaw, available from 14 June 2024, MW:

    demand   demand of the national power system (KSE)
    pv       total generation of photovoltaic sources (units on and off the balancing market)
    wi       total generation of wind sources (units on and off the balancing market)
    jg       generation of scheduling units active on the balancing market
    jnwrb    generation of units not actively participating in the balancing market
    swm_p    balance of synchronous cross-border exchange, + = import
    swm_np   balance of asynchronous cross-border exchange, + = import

jg + jnwrb is the total domestic generation (PV and wind units sit in both terms). pse-opendata downloads the API (no
key) and caches whole months as Parquet files in ``cache_dir`` (``api/his-wlk-cal/<YYYY-MM>.parquet``); a month is
downloaded again until the revision window of the report (60 days) has passed. Frames returned here are indexed by
interval START (UTC).

Usage:
    from flex_ramps.pse_api import load_mix
    df, status = load_mix(date(2024, 6, 14), date(2026, 10, 21), cache_dir=Path("data"))
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import pse_opendata as pse

ENDPOINT = "his-wlk-cal"
INTERVAL = pd.Timedelta("15min")
API_START = date(2024, 6, 14)   # first business date of his-wlk-cal
FIELDS = {"demand": "demand", "pv": "pv", "wi": "wind", "jg": "jg", "jnwrb": "jnwrb",
          "swm_p": "swm_p", "swm_np": "swm_np"}   # API field -> column name
STATUS = {"downloaded": "fetched", "cached": "cached", "stale": "stale"}   # pse-opendata status -> ApiStatus list
log = logging.getLogger(__name__)


@dataclass
class ApiStatus:
    """What happened while loading the API series (shown on the dashboard as data freshness)."""
    fetched: list[str] = field(default_factory=list)   # months downloaded in this run
    cached: list[str] = field(default_factory=list)    # final months read from cache
    stale: list[str] = field(default_factory=list)     # download failed, older cached copy used
    missing: list[str] = field(default_factory=list)   # download failed and no cache
    last_interval: pd.Timestamp | None = None


def empty_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="ts_utc")
    out = pd.DataFrame({c: pd.Series(dtype=float) for c in FIELDS.values()}, index=idx)
    out["business_date"] = pd.Series(dtype=object)
    return out


def to_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """pse-opendata frame -> frame indexed by interval START (UTC). Rows without demand, PV and wind (future
    intervals) are dropped."""
    if raw.empty:
        return empty_frame()
    out = raw.set_index("start_utc")[list(FIELDS)].rename(columns=FIELDS)
    out.index.name = "ts_utc"
    out["business_date"] = raw["business_date"].to_numpy()
    return out.dropna(subset=["demand", "pv", "wind"], how="all")


def load_mix(start: date, end: date, cache_dir: Path, offline: bool = False,
             client: pse.Client | None = None) -> tuple[pd.DataFrame, ApiStatus]:
    """Load business dates start..end month by month without raising on API errors.

    Whole months are requested, so that pse-opendata caches them and an offline run rebuilds the same result.
    When a download fails, the cached copy of the month is used and the month is reported as stale; without a cached
    copy it is reported as missing. ``offline`` uses the cache only.
    """
    client = client or pse.Client(cache_dir=cache_dir, offline=offline, allow_stale=True)
    status = ApiStatus()
    frames = []
    for month_start in pd.date_range(date(start.year, start.month, 1), end, freq="MS"):
        label = f"{month_start:%Y-%m}"
        m_end = (month_start + pd.offsets.MonthEnd(0)).date()
        loaded = len(client.loads)
        try:
            raw = client.get(ENDPOINT, month_start.date(), m_end, fields=list(FIELDS))
        except pse.PseError as e:
            log.error("his-wlk-cal %s: download failed (%s)", label, e)
            status.missing.append(label)
            continue
        for load in client.loads[loaded:]:
            getattr(status, STATUS[load.status]).append(label)
        df = to_frame(raw)
        log.info("his-wlk-cal %s: %d intervals", label, len(df))
        frames.append(df)
    frames = [f for f in frames if not f.empty]
    out = pd.concat(frames).sort_index() if frames else empty_frame()
    out = out[~out.index.duplicated(keep="last")]
    out = out[(out["business_date"] >= start) & (out["business_date"] <= end)]
    status.last_interval = out.index.max() if len(out) else None
    return out, status
