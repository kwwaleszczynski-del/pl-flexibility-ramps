import numpy as np
import pandas as pd
import pytest

from flex_ramps import ramps

TZ = "Europe/Warsaw"


def series(values, start="2025-03-01", freq="15min"):
    idx = pd.date_range(pd.Timestamp(start, tz=TZ).tz_convert("UTC"), periods=len(values), freq=freq)
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def test_ramp_of_a_linear_series_is_constant():
    s = series(np.arange(20) * 10.0)
    r = ramps.ramp(s, pd.Timedelta("1h"))
    assert r.dropna().eq(40.0).all()
    assert r.iloc[-4:].isna().all()          # no ramp past the end


def test_ramp_is_nan_across_gaps():
    s = series(np.arange(12) * 1.0).drop(series(np.arange(12) * 1.0).index[5])
    r = ramps.ramp(s, pd.Timedelta("15min"))
    assert r.isna().sum() == 3               # into the gap, out of the gap, and the last value
    assert len(r) == 12


def test_horizon_must_be_a_multiple_of_the_step():
    with pytest.raises(ValueError):
        ramps.ramp(series(np.zeros(10), freq="1h"), pd.Timedelta("15min"))


def test_residual_load_and_pv_scaling():
    f = pd.DataFrame({"demand": [100.0], "pv": [30.0], "wind": [20.0]})
    assert ramps.residual_load(f).iloc[0] == 50.0
    assert ramps.residual_load(f, pv_scale=1.5).iloc[0] == 35.0


def test_period_keys_use_local_time():
    idx = pd.DatetimeIndex([pd.Timestamp("2024-12-31 23:30", tz="UTC")])   # 00:30 on 1 January in Warsaw
    assert list(ramps.period_keys(idx, "year")) == ["2025"]
    assert list(ramps.period_keys(idx, "month")) == ["2025-01"]


def test_duration_curve_is_sorted():
    s = series(np.random.default_rng(0).normal(10000, 2000, 96 * 30), start="2025-01-01")
    d = ramps.duration_curves(s)
    col = d["2025"].to_numpy()
    assert (np.diff(col) <= 0).all()
    assert col[0] == pytest.approx(s.max()) and col[-1] == pytest.approx(s.min())


@pytest.mark.parametrize("day, lat, lon, expected_utc", [
    ("2025-06-21", 52.23, 21.01, "19:01"),     # Warsaw, sunset 21:01 CEST
    ("2025-12-21", 52.23, 21.01, "14:25"),     # Warsaw, sunset 15:25 CET
])
def test_sunset_against_known_times(day, lat, lon, expected_utc):
    got = ramps.sunset_utc(pd.DatetimeIndex([pd.Timestamp(day)]), lat, lon)[0]
    want = pd.Timestamp(f"{day} {expected_utc}", tz="UTC")
    assert abs(got - want) <= pd.Timedelta("3min")


def test_evening_ramp_finds_the_rise_before_sunset():
    idx = pd.date_range(pd.Timestamp("2025-06-10", tz=TZ).tz_convert("UTC"), periods=2 * 96, freq="15min")
    loc = idx.tz_convert(TZ)
    hour = loc.hour + loc.minute / 60
    # flat 10 GW, linear rise of 1 GW per hour from 17:00 to 21:00 local, then flat 14 GW (two identical days)
    values = 10000 + 1000 * np.clip(hour - 17, 0, 4)
    ev = ramps.evening_ramps(pd.Series(values, index=idx))
    row = ev.iloc[0]
    assert row["ramp_1h"] == pytest.approx(1000.0)
    assert row["ramp_3h"] == pytest.approx(3000.0)
    assert row["lift"] == pytest.approx(4000.0)
    assert "20:45" <= row["sunset_local"] <= "21:15"
    assert np.isnan(ev["ramp_3h"].iloc[1])     # the last day's window runs past the data


def test_midday_minimum_ignores_night_values():
    idx = pd.date_range(pd.Timestamp("2025-06-10", tz=TZ).tz_convert("UTC"), periods=96, freq="15min")
    loc = idx.tz_convert(TZ)
    values = np.full(96, 9000.0)
    values[(loc.hour == 3)] = 1000.0          # night minimum, outside the window
    values[(loc.hour == 13) & (loc.minute == 15)] = 4000.0
    frame = pd.DataFrame({"demand": 20000.0, "conv": 8000.0, "net_import": -2000.0}, index=idx)
    mid = ramps.midday_minima(frame, pd.Series(values, index=idx))
    assert mid["residual"].iloc[0] == 4000.0
    assert mid["time_local"].iloc[0] == "13:15"
    assert mid["conv"].iloc[0] == 8000.0


def test_ramp_table_quantiles():
    s = series(np.r_[np.zeros(50), np.arange(50) * 100.0], start="2025-05-01")
    t = ramps.ramp_table(s, {"15min": pd.Timedelta("15min")}, "year")
    assert t.loc["2025", "15min_max"] == 100.0
    assert t.loc["2025", "15min_n"] == 99


def test_trend_slope():
    v = pd.Series([1.0, 3.0, 5.0], index=["2023", "2024", "2025"])
    assert ramps.trend(v)["slope"] == pytest.approx(2.0)


def test_incomplete_last_day_is_left_out():
    # two days, the second ends at 13:00 local: its midday window and evening ramp are incomplete
    idx = pd.date_range(pd.Timestamp("2025-06-10", tz=TZ).tz_convert("UTC"),
                        pd.Timestamp("2025-06-11 13:00", tz=TZ).tz_convert("UTC"), freq="15min", inclusive="left")
    values = pd.Series(10000.0 - np.arange(len(idx)), index=idx)
    mid = ramps.midday_minima(pd.DataFrame(index=idx), values)
    assert len(mid) == 1 and mid.index[0] == pd.Timestamp("2025-06-10")
    ev = ramps.evening_ramps(values)
    assert np.isnan(ev["lift"].iloc[1]) and np.isnan(ev["midday_min"].iloc[1])


def test_day_lengths_follow_dst():
    days = pd.DatetimeIndex(["2025-03-30", "2025-06-01", "2025-10-26"])
    assert list(ramps.day_lengths(days, pd.Timedelta("15min"))) == [92, 96, 100]
