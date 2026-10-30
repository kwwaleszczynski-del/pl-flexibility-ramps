from datetime import date

import numpy as np
import pandas as pd
import pse_opendata as pse
import pytest

from flex_ramps import pse_legacy

HEADER = "Data;Godzina;Generacja źródeł wiatrowych;Generacja źródeł fotowoltaicznych\n"
DEMAND_HEADER = ("Data;Godzina;Krajowe zapotrzebowanie na moc;Sumaryczna generacja JWCD;Sumaryczna generacja nJWCD;"
                 "Krajowe saldo wymiany międzysystemowej równoległej;"
                 "Krajowe saldo wymiany międzysystemowej nierównoległej\n")


def day_rows(day: str, labels: list[str], pv: str = "1,500") -> str:
    return "".join(f"{day};{h};{1000 + i},250;{pv}\n" for i, h in enumerate(labels))


class FakeSession:
    """Legacy export: ``files`` maps a report code to the CSV body served for every month; records the URLs."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.urls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.urls.append(url)
        body = self.files[url.split("/csv/")[1].split("/")[0]]

        class R:
            status_code, headers, reason = 200, {}, "OK"
            content = body.encode("cp1250")

        return R()


def load(files: dict[str, str], start: date, tmp_path, **kwargs):
    session = FakeSession(files)
    client = pse.Client(session=session, cache_dir=tmp_path, sleep=lambda s: None, legacy_min_interval=0)
    df, status = pse_legacy.load_legacy(start, tmp_path, client=client, **kwargs)
    return df, status, session


def demand_rows(days, value="17000,0") -> str:
    return DEMAND_HEADER + "".join(f"{d};{h};{value};1;2;3;4\n" for d in days for h in range(1, 25))


def test_normal_day(tmp_path):
    files = {"PL_GEN_WIATR": HEADER + day_rows("2024-06-12", [str(h) for h in range(1, 25)]),
             "PL_WYK_KSE": demand_rows(["2024-06-12"])}
    df, _, _ = load(files, date(2024, 6, 12), tmp_path)
    day = df.loc[:"2024-06-12 21:00"]
    assert len(day) == 24
    assert day.index[0] == pd.Timestamp("2024-06-11 22:00", tz="UTC")   # 00:00 CEST
    assert day["wind"].iloc[0] == pytest.approx(1000.25)
    assert day["pv"].iloc[0] == pytest.approx(1.5)
    assert list(df.columns) == ["demand", "jwcd", "njwcd", "swm_p", "swm_np", "wind", "pv"]
    assert day["swm_p"].iloc[0] == pytest.approx(3.0)


def test_dst_days_and_missing_pv(tmp_path):
    spring = day_rows("2023-03-26", ["1"] + [str(h) for h in range(3, 25)], pv="-")  # 23 hours, no hour 2
    autumn = day_rows("2023-10-29", ["1", "2", "2A"] + [str(h) for h in range(3, 25)])  # 25 hours
    files = {"PL_GEN_WIATR": HEADER + spring + autumn, "PL_WYK_KSE": demand_rows([])}
    df, _, _ = load(files, date(2023, 3, 1), tmp_path)
    df = df[df["wind"].notna()]
    assert len(df) == 48
    assert df.index.is_unique and df.index.is_monotonic_increasing
    for block in (df.index[:23], df.index[23:]):              # each DST day is a continuous hourly UTC run
        assert (pd.Series(block[1:] - block[:-1]) == pd.Timedelta("1h")).all()
    assert df.index[22] + pd.Timedelta("1h") == pd.Timestamp("2023-03-26 22:00", tz="UTC")   # next local midnight
    assert df.index[0] == pd.Timestamp("2023-03-25 23:00", tz="UTC")
    assert df.index[23] == pd.Timestamp("2023-10-28 22:00", tz="UTC")
    assert df.index[-1] == pd.Timestamp("2023-10-29 22:00", tz="UTC")
    assert np.isnan(df["pv"].iloc[0]) and df["pv"].iloc[-1] == pytest.approx(1.5)


def test_html_error_page_is_a_missing_month(tmp_path):
    files = {"PL_GEN_WIATR": "<!DOCTYPE html><html></html>", "PL_WYK_KSE": demand_rows(["2024-06-12"])}
    df, status, _ = load(files, date(2024, 6, 1), tmp_path)
    assert status.missing == ["PL_GEN_WIATR 2024-06"] and df["wind"].isna().all()


def test_load_legacy_caches_months(tmp_path):
    files = {"PL_GEN_WIATR": HEADER + day_rows("2024-06-12", [str(h) for h in range(1, 25)])
             + day_rows("2024-06-13", [str(h) for h in range(1, 25)]),
             "PL_WYK_KSE": "Data;Godzina;Krajowe zapotrzebowanie na moc\n"
             + "".join(f"2024-06-{d};{h};17000,0\n" for d in (12, 13) for h in range(1, 25))}
    df, st, s = load(files, date(2024, 6, 12), tmp_path)
    assert len(s.urls) == 2 and "data_do/20240613" in s.urls[0]     # the last month ends on 13 June 2024
    assert len(df) == 48 and df["demand"].notna().all() and df["wind"].notna().all()
    assert np.isnan(df["swm_p"]).all()                              # columns absent from the file are NaN
    df2, st2, s2 = load(files, date(2024, 6, 12), tmp_path)
    assert s2.urls == [] and len(st2.cached) == 2 and len(df2) == 48


def test_offline_reports_missing(tmp_path):
    df, st = pse_legacy.load_legacy(date(2024, 5, 1), tmp_path, offline=True)
    assert df.empty and len(st.missing) == 4
