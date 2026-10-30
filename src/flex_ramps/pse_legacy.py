"""Hourly history from the legacy PSE CSV export (www.pse.pl/getcsv), before the reports API existed.

Two daily reports of the "old" PSE data service are used, read with the pse-opendata client
(https://pypi.org/project/pse-opendata/):

    PL_WYK_KSE    demand  "Krajowe zapotrzebowanie na moc" (national demand), MW
                  jwcd    "Sumaryczna generacja JWCD" (centrally dispatched units), MW
                  njwcd   "Sumaryczna generacja nJWCD" (other units, including wind and PV), MW
                  swm_p   "Krajowe saldo wymiany miedzysystemowej rownoleglej" (synchronous exchange, + = import), MW
                  swm_np  "... nierownoleglej" (asynchronous exchange, + = import), MW
    PL_GEN_WIATR  wind    "Generacja zrodel wiatrowych", MW
                  pv      "Generacja zrodel fotowoltaicznych", MW (published from 2020; "-" before)

Rows of the files are local (Europe/Warsaw) dates and hours 1..24, the extra autumn hour labelled ``2A``; pse-opendata
places every row by its label, requests one calendar month at a time with a pause between requests (the server
answers HTTP 429 to quick series of requests) and caches the months for good in ``cache_dir``
(``legacy/<report>/<YYYY-MM>.parquet``). The export ends on 13 June 2024, the day before the reports API starts.

Usage:
    from flex_ramps.pse_legacy import load_legacy
    hourly, status = load_legacy(date(2020, 1, 1), cache_dir=Path("data"))
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import pse_opendata as pse

LEGACY_END = date(2024, 6, 13)   # last business date of the legacy export
TZ = "Europe/Warsaw"
REPORTS = {"PL_WYK_KSE": ["demand", "jwcd", "njwcd", "swm_p", "swm_np"], "PL_GEN_WIATR": ["wind", "pv"]}
log = logging.getLogger(__name__)


@dataclass
class LegacyStatus:
    fetched: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def empty_frame(report: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz="UTC", name="ts_utc")
    return pd.DataFrame({c: pd.Series(dtype=float) for c in REPORTS[report]}, index=idx)


def load_legacy(start: date, cache_dir: Path, offline: bool = False, pause_s: float = 15.0,
                client: pse.Client | None = None) -> tuple[pd.DataFrame, LegacyStatus]:
    """Both legacy reports from ``start`` to 13 June 2024, merged on the hour, one cached month per report.

    The history is final, so a cached month is never downloaded again. A month that cannot be downloaded is listed
    in ``status.missing`` (and is simply absent from the frame).
    """
    client = client or pse.Client(cache_dir=cache_dir, offline=offline, legacy_min_interval=pause_s)
    status = LegacyStatus()
    merged = []
    for report, fields in REPORTS.items():
        frames = []
        for m in pd.date_range(date(start.year, start.month, 1), LEGACY_END, freq="MS"):
            label = f"{report} {m:%Y-%m}"
            m_end = min((m + pd.offsets.MonthEnd(0)).date(), LEGACY_END)
            loaded = len(client.loads)
            try:
                raw = client.legacy(report, m.date(), m_end, fields=fields)
            except pse.PseError as e:
                log.error("%s: download failed (%s)", label, e)
                status.missing.append(label)
                continue
            for load in client.loads[loaded:]:
                (status.cached if load.status == "cached" else status.fetched).append(label)
            frames.append(raw.set_index("start_utc")[fields])
        part = pd.concat(frames).sort_index() if frames else empty_frame(report)
        merged.append(part[~part.index.duplicated(keep="first")])
    out = pd.concat(merged, axis=1).sort_index()
    out.index.name = "ts_utc"
    out = out[out.index >= pd.Timestamp(start, tz=TZ).tz_convert("UTC")]
    return out, status
