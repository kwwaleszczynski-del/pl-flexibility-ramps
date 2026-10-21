from datetime import date

import pandas as pd

from flex_ramps import pse_api


def record(end_utc: str, business_date: str, **values):
    base = {"dtime_utc": end_utc, "business_date": business_date, "demand": 18000.0, "pv": 100.0, "wi": 2000.0,
            "jg": 9000.0, "jnwrb": 8000.0, "swm_p": 500.0, "swm_np": 100.0}
    base.update(values)
    return base


class FakeSession:
    """Serves pages of records; the first page links to the second."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        page = self.pages.pop(0)

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return page

        return R()


def test_parse_records_uses_interval_start():
    df = pse_api.parse_records([record("2025-01-01 00:15:00", "2025-01-01"),
                                record("2025-01-01 00:30:00", "2025-01-01", pv=None, wi=None, demand=None)])
    assert len(df) == 1                       # the row without values (future interval) is dropped
    assert df.index[0] == pd.Timestamp("2025-01-01 00:00", tz="UTC")
    assert df["wind"].iloc[0] == 2000.0
    assert df["business_date"].iloc[0] == date(2025, 1, 1)


def test_fetch_follows_next_link():
    pages = [{"value": [record("2025-01-01 00:15:00", "2025-01-01")], "nextLink": "https://next"},
             {"value": [record("2025-01-01 00:30:00", "2025-01-01")]}]
    s = FakeSession(pages)
    df = pse_api.fetch_period(date(2025, 1, 1), date(2025, 1, 1), session=s)
    assert s.calls == 2 and len(df) == 2


def test_final_month_read_from_cache(tmp_path):
    month = [record(f"2025-01-{d:02d} 00:15:00", f"2025-01-{d:02d}") for d in range(1, 32)]
    s = FakeSession([{"value": month}])
    df, st = pse_api.load_mix(date(2025, 1, 1), date(2025, 1, 31), tmp_path, today=date(2025, 6, 1), session=s)
    assert st.fetched == ["2025-01"] and len(df) == 31
    s2 = FakeSession([])
    df2, st2 = pse_api.load_mix(date(2025, 1, 1), date(2025, 1, 31), tmp_path, today=date(2025, 6, 1), session=s2)
    assert s2.calls == 0 and st2.cached == ["2025-01"] and len(df2) == 31


def test_offline_without_cache_reports_missing(tmp_path):
    df, st = pse_api.load_mix(date(2025, 1, 1), date(2025, 1, 31), tmp_path, today=date(2025, 6, 1), offline=True)
    assert df.empty and st.missing == ["2025-01"]
