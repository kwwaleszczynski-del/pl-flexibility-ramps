"""Hourly history from the legacy PSE CSV export (www.pse.pl/getcsv), before the reports API existed.

Two daily reports of the "old" PSE data service are used, one calendar month per request:

    PL_WYK_KSE    demand  "Krajowe zapotrzebowanie na moc" (national demand), MW
                  jwcd    "Sumaryczna generacja JWCD" (centrally dispatched units), MW
                  njwcd   "Sumaryczna generacja nJWCD" (other units, including wind and PV), MW
                  swm_p   "Krajowe saldo wymiany miedzysystemowej rownoleglej" (synchronous exchange, + = import), MW
                  swm_np  "... nierownoleglej" (asynchronous exchange, + = import), MW
    PL_GEN_WIATR  wind    "Generacja zrodel wiatrowych", MW
                  pv      "Generacja zrodel fotowoltaicznych", MW (published from 2020; "-" before)

Files are cp1250, ``;``-separated with a decimal comma. Rows are local (Europe/Warsaw) dates and hours 1..24, hour 1
being 00:00-01:00; the extra autumn hour is labelled ``2A`` and the spring day has 23 rows. The export ends on
13 June 2024, the day before the new reports API (api.raporty.pse.pl) starts. The server answers HTTP 429 to quick
series of requests and to long ranges, and rejects one-day ranges, so months are requested one by one with a pause.

Usage:
    from flex_ramps.pse_legacy import load_legacy
    hourly, status = load_legacy(date(2020, 1, 1), cache_dir=Path("data/legacy"))
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

URL = "https://www.pse.pl/getcsv/-/export/csv/{report}/data_od/{start}/data_do/{end}"
LEGACY_END = date(2024, 6, 13)   # last business date of the legacy export
TZ = "Europe/Warsaw"
HEADERS = {"User-Agent": "Mozilla/5.0 (open-source analysis of public data)"}
log = logging.getLogger(__name__)

REPORTS = {
    "PL_WYK_KSE": {
        "krajowe zapotrzebowanie na moc": "demand",
        "sumaryczna generacja jwcd": "jwcd",
        "sumaryczna generacja njwcd": "njwcd",
        "krajowe saldo wymiany międzysystemowej równoległej": "swm_p",
        "krajowe saldo wymiany międzysystemowej nierównoległej": "swm_np",
    },
    "PL_GEN_WIATR": {
        "generacja źródeł wiatrowych": "wind",
        "generacja źródeł fotowoltaicznych": "pv",
    },
}


@dataclass
class LegacyStatus:
    fetched: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def empty_frame(report: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="ts_utc")
    return pd.DataFrame({c: pd.Series(dtype=float) for c in REPORTS[report].values()}, index=idx)


def parse_legacy_csv(text: str, report: str) -> pd.DataFrame:
    """One legacy CSV -> hourly frame indexed by hour START in UTC.

    Within a local day the rows are chronological, so the n-th row of a day starts n hours after local midnight
    (this handles the 23- and 25-hour days without interpreting the ``2A`` label).
    """
    if text.lstrip().startswith("<"):
        raise ValueError("PSE returned an HTML page instead of CSV")
    raw = pd.read_csv(io.StringIO(text), sep=";", dtype=str, keep_default_na=False)
    names = {c: REPORTS[report].get(c.strip().lower()) for c in raw.columns[2:]}
    if not any(names.values()):
        raise ValueError(f"{report}: unexpected columns {list(raw.columns)}")
    out = pd.DataFrame(index=raw.index)
    for col, name in names.items():
        if name:
            out[name] = pd.to_numeric(raw[col].str.strip().str.replace(",", ".", regex=False), errors="coerce")
    for name in REPORTS[report].values():
        if name not in out:
            out[name] = np.nan
    day = raw.iloc[:, 0].str.strip()
    position = day.groupby(day, sort=False).cumcount().to_numpy()
    midnight = pd.DatetimeIndex(pd.to_datetime(day.to_numpy())).tz_localize(TZ).tz_convert("UTC")
    out.index = pd.DatetimeIndex(midnight + pd.to_timedelta(position, unit="h"), name="ts_utc")
    out = out[list(REPORTS[report].values())]
    return out[~out.index.duplicated(keep="first")].sort_index()


def _get(session: requests.Session, url: str, tries: int = 6, wait_s: float = 120.0) -> requests.Response:
    for k in range(tries):
        r = session.get(url, headers=HEADERS, timeout=120)
        if r.status_code == 429 and k < tries - 1:
            log.warning("PSE legacy export: HTTP 429, waiting %.0f s", wait_s * (k + 1))
            time.sleep(wait_s * (k + 1))
            continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")


def fetch_month(report: str, month_start: date, session: requests.Session) -> pd.DataFrame:
    end = min((pd.Timestamp(month_start) + pd.offsets.MonthEnd(0)).date(), LEGACY_END)
    url = URL.format(report=report, start=f"{month_start:%Y%m%d}", end=f"{end:%Y%m%d}")
    return parse_legacy_csv(_get(session, url).content.decode("cp1250"), report)


def load_legacy(start: date, cache_dir: Path, offline: bool = False, pause_s: float = 15.0,
                session: requests.Session | None = None) -> tuple[pd.DataFrame, LegacyStatus]:
    """Both legacy reports from ``start`` to 13 June 2024, merged on the hour, one cached Parquet per report-month.

    The history is final, so a cached month is never downloaded again. A month that cannot be downloaded is listed
    in ``status.missing`` (and is simply absent from the frame).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = session or requests.Session()
    status = LegacyStatus()
    merged = []
    for report in REPORTS:
        frames = []
        for m in pd.date_range(date(start.year, start.month, 1), LEGACY_END, freq="MS"):
            label = f"{report} {m:%Y-%m}"
            path = cache_dir / f"{report}_{m:%Y-%m}.parquet"
            if path.exists():
                frames.append(pd.read_parquet(path))
                status.cached.append(label)
                continue
            if offline:
                status.missing.append(label)
                continue
            try:
                df = fetch_month(report, m.date(), session)
            except (requests.RequestException, ValueError, RuntimeError) as e:
                log.error("%s: download failed (%s)", label, e)
                status.missing.append(label)
                continue
            df.to_parquet(path)
            frames.append(df)
            status.fetched.append(label)
            log.info("%s: %d hours", label, len(df))
            time.sleep(pause_s)
        part = pd.concat(frames).sort_index() if frames else empty_frame(report)
        merged.append(part[~part.index.duplicated(keep="first")])
    out = pd.concat(merged, axis=1).sort_index()
    out = out[out.index >= pd.Timestamp(start, tz=TZ).tz_convert("UTC")]
    return out, status
