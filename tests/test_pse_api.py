from datetime import UTC, date, datetime

import pandas as pd
import pse_opendata as pse

from flex_ramps import pse_api


def record(end_utc: str, business_date: str, **values):
    base = {"dtime_utc": end_utc, "business_date": business_date, "demand": 18000.0, "pv": 100.0, "wi": 2000.0,
            "jg": 9000.0, "jnwrb": 8000.0, "swm_p": 500.0, "swm_np": 100.0}
    base.update(values)
    return base


class FakeSession:
    """Serves the records of the requested business dates; counts the calls."""

    def __init__(self, records):
        self.records = list(records)
        self.calls = 0

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls += 1
        lo, hi = [part.split("'")[1] for part in params["$filter"].split(" and ")]
        rows = [r for r in self.records if lo <= r["business_date"] <= hi]

        class R:
            status_code, headers, reason = 200, {}, "OK"

            def json(self):
                return {"value": rows}

        return R()


def client(session, tmp_path, **kwargs):
    return pse.Client(session=session, cache_dir=tmp_path, now=lambda: datetime(2025, 6, 1, tzinfo=UTC),
                      sleep=lambda s: None, min_interval=0, allow_stale=True, **kwargs)


def test_frames_use_interval_start_and_drop_rows_without_values(tmp_path):
    s = FakeSession([record("2025-01-01 00:15:00", "2025-01-01"),
                     record("2025-01-01 00:30:00", "2025-01-01", pv=None, wi=None, demand=None)])
    df, st = pse_api.load_mix(date(2025, 1, 1), date(2025, 1, 1), tmp_path, client=client(s, tmp_path))
    assert len(df) == 1                       # the row without values (future interval) is dropped
    assert df.index[0] == pd.Timestamp("2025-01-01 00:00", tz="UTC")
    assert df["wind"].iloc[0] == 2000.0 and df["swm_np"].iloc[0] == 100.0
    assert df["business_date"].iloc[0] == date(2025, 1, 1)


def test_final_month_read_from_cache(tmp_path):
    month = [record(f"2025-01-{d:02d} 00:15:00", f"2025-01-{d:02d}") for d in range(1, 32)]
    s = FakeSession(month)
    df, st = pse_api.load_mix(date(2025, 1, 1), date(2025, 1, 31), tmp_path, client=client(s, tmp_path))
    assert st.fetched == ["2025-01"] and len(df) == 31
    s2 = FakeSession([])
    df2, st2 = pse_api.load_mix(date(2025, 1, 1), date(2025, 1, 31), tmp_path, client=client(s2, tmp_path))
    assert s2.calls == 0 and st2.cached == ["2025-01"] and len(df2) == 31


def test_offline_without_cache_reports_missing(tmp_path):
    df, st = pse_api.load_mix(date(2025, 1, 1), date(2025, 1, 31), tmp_path, offline=True)
    assert df.empty and st.missing == ["2025-01"]
