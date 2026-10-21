import numpy as np
import pandas as pd

from flex_ramps import data


def test_first_complete_month_counts_calendar_intervals():
    idx = pd.date_range(pd.Timestamp("2024-06-14", tz="Europe/Warsaw").tz_convert("UTC"),
                        pd.Timestamp("2024-08-01", tz="Europe/Warsaw").tz_convert("UTC"), freq="1h",
                        inclusive="left")
    frame = pd.DataFrame({"demand": 1.0, "pv": 1.0, "wind": 1.0}, index=idx)
    assert data.first_complete_month(frame) == "2024-07"      # June starts on the 14th


def test_hourly_stitches_legacy_and_api():
    tz = "Europe/Warsaw"
    old_idx = pd.date_range(pd.Timestamp("2024-06-12", tz=tz).tz_convert("UTC"),
                            pd.Timestamp("2024-06-14", tz=tz).tz_convert("UTC"), freq="1h", inclusive="left")
    legacy = pd.DataFrame({"demand": 100.0, "jwcd": 60.0, "njwcd": 40.0, "swm_p": 0.0, "swm_np": 0.0,
                           "wind": 10.0, "pv": 5.0}, index=old_idx)
    new_idx = pd.date_range(pd.Timestamp("2024-06-14", tz=tz).tz_convert("UTC"), periods=96, freq="15min")
    api = pd.DataFrame({"demand": 200.0, "pv": 20.0, "wi": 30.0, "jg": 150.0, "jnwrb": 50.0, "swm_p": 1.0,
                        "swm_np": 2.0}, index=new_idx)
    api = api.rename(columns={"wi": "wind"})
    h = data.hourly(api, legacy)
    assert len(h) == 72 and h.index.is_unique
    assert h["demand"].iloc[0] == 100.0 and h["demand"].iloc[-1] == 200.0
    assert h["conv"].iloc[0] == 85.0 and h["conv"].iloc[-1] == 150.0      # domestic generation - PV - wind
    assert h["net_import"].iloc[-1] == 3.0
    q = data.quarter_hourly(api)
    assert len(q) == 96 and np.isclose(q["conv"].iloc[0], 150.0)
