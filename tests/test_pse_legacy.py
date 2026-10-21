from datetime import date

import numpy as np
import pandas as pd
import pytest

from flex_ramps import pse_legacy

HEADER = "Data;Godzina;Generacja źródeł wiatrowych;Generacja źródeł fotowoltaicznych\n"


def day_rows(day: str, labels: list[str], pv: str = "1,500") -> str:
    return "".join(f"{day};{h};{1000 + i},250;{pv}\n" for i, h in enumerate(labels))


def test_parse_normal_day():
    df = pse_legacy.parse_legacy_csv(HEADER + day_rows("2023-06-01", [str(h) for h in range(1, 25)]),
                                     "PL_GEN_WIATR")
    assert len(df) == 24
    assert df.index[0] == pd.Timestamp("2023-05-31 22:00", tz="UTC")   # 00:00 CEST
    assert df["wind"].iloc[0] == pytest.approx(1000.25)
    assert df["pv"].iloc[0] == pytest.approx(1.5)


def test_parse_dst_days_and_missing_pv():
    spring = day_rows("2023-03-26", [str(h) for h in range(1, 24)], pv="-")          # 23 hours
    autumn = day_rows("2023-10-29", ["1", "2", "2A"] + [str(h) for h in range(3, 25)])  # 25 hours
    df = pse_legacy.parse_legacy_csv(HEADER + spring + autumn, "PL_GEN_WIATR")
    assert len(df) == 48
    assert df.index.is_unique and df.index.is_monotonic_increasing
    for block in (df.index[:23], df.index[23:]):              # each DST day is a continuous hourly UTC run
        assert (pd.Series(block[1:] - block[:-1]) == pd.Timedelta("1h")).all()
    assert df.index[22] + pd.Timedelta("1h") == pd.Timestamp("2023-03-26 22:00", tz="UTC")   # next local midnight
    assert df.index[0] == pd.Timestamp("2023-03-25 23:00", tz="UTC")
    assert df.index[23] == pd.Timestamp("2023-10-28 22:00", tz="UTC")
    assert df.index[-1] == pd.Timestamp("2023-10-29 22:00", tz="UTC")
    assert np.isnan(df["pv"].iloc[0]) and df["pv"].iloc[-1] == pytest.approx(1.5)


def test_html_error_page_rejected():
    with pytest.raises(ValueError):
        pse_legacy.parse_legacy_csv("<!DOCTYPE html><html></html>", "PL_GEN_WIATR")


def test_demand_report_columns():
    text = ("Data;Godzina;Krajowe zapotrzebowanie na moc;Sumaryczna generacja JWCD;Sumaryczna generacja nJWCD;"
            "Krajowe saldo wymiany międzysystemowej równoległej;Krajowe saldo wymiany międzysystemowej nierównoległej\n"
            "2024-06-13;1;16653,675;9544,775;4238,575;2619,575;327,225\n"
            "2024-06-13;2;15950,138;9569,825;4097,888;2038,375;343,825\n")
    df = pse_legacy.parse_legacy_csv(text, "PL_WYK_KSE")
    assert list(df.columns) == ["demand", "jwcd", "njwcd", "swm_p", "swm_np"]
    assert df["swm_p"].iloc[0] == pytest.approx(2619.575)


class FakeSession:
    def __init__(self):
        self.urls = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        report = url.split("/csv/")[1].split("/")[0]
        if report == "PL_GEN_WIATR":
            body = HEADER + day_rows("2024-06-12", [str(h) for h in range(1, 25)]) \
                + day_rows("2024-06-13", [str(h) for h in range(1, 25)])
        else:
            body = ("Data;Godzina;Krajowe zapotrzebowanie na moc\n"
                    + "".join(f"2024-06-{d};{h};17000,0\n" for d in (12, 13) for h in range(1, 25)))

        class R:
            status_code = 200
            content = body.encode("cp1250")

            def raise_for_status(self):
                pass

        return R()


def test_load_legacy_caches_months(tmp_path):
    s = FakeSession()
    df, st = pse_legacy.load_legacy(date(2024, 6, 12), tmp_path, pause_s=0, session=s)
    assert len(s.urls) == 2 and "data_do/20240613" in s.urls[0]     # the last month ends on 13 June 2024
    assert len(df) == 48 and df["demand"].notna().all() and df["wind"].notna().all()
    assert np.isnan(df["swm_p"]).all()                              # columns absent from the file are NaN
    s2 = FakeSession()
    df2, st2 = pse_legacy.load_legacy(date(2024, 6, 12), tmp_path, pause_s=0, session=s2)
    assert s2.urls == [] and len(st2.cached) == 2 and len(df2) == 48


def test_offline_reports_missing(tmp_path):
    df, st = pse_legacy.load_legacy(date(2024, 5, 1), tmp_path, offline=True)
    assert df.empty and len(st.missing) == 4
