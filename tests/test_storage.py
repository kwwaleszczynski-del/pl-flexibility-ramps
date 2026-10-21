import numpy as np
import pandas as pd
import pytest

from flex_ramps import storage

STEP = pd.Timedelta("15min")
BANDS = {"15min": (-100.0, 100.0), "1h": (-400.0, 400.0), "3h": (-1000.0, 1000.0)}


def test_no_storage_needed_inside_the_band():
    load = 5000 + 50 * np.sin(np.linspace(0, 6, 96))
    assert storage.size_day(load, BANDS, STEP) == (0.0, 0.0, 0.0)


def test_a_step_is_smoothed_with_the_minimum_power():
    """A 1000 MW jump must be spread over ten 100 MW steps; the best split needs 450 MW (450 before, 450 after)."""
    load = np.r_[np.zeros(48), np.full(48, 1000.0)]
    power, energy, discharged = storage.size_day(load, BANDS, STEP)
    assert power == pytest.approx(450.0, abs=1.0)
    assert energy > 0 and discharged > 0


def test_losses_need_more_charge_than_discharge():
    load = np.r_[np.zeros(48), np.full(48, 1000.0)]
    _, e_loss, _ = storage.size_day(load, BANDS, STEP, eta=0.85)
    _, e_ideal, _ = storage.size_day(load, BANDS, STEP, eta=1.0)
    assert e_loss >= e_ideal - 1e-6


def test_size_storage_per_local_day_and_summary():
    idx = pd.date_range(pd.Timestamp("2025-04-01", tz="Europe/Warsaw").tz_convert("UTC"), periods=96 * 3, freq=STEP)
    day = np.r_[np.zeros(48), np.full(48, 1000.0)]
    rl = pd.Series(np.r_[day, np.zeros(96), day], index=idx)
    daily = storage.size_storage(rl, BANDS)
    assert len(daily) == 3
    assert daily["power_mw"].iloc[1] == 0.0
    s = storage.summarise(daily)
    assert s["days_active"] == 2 and s["power_max_mw"] == pytest.approx(450.0, abs=1.0)


def test_bands_are_ramp_quantiles():
    idx = pd.date_range("2025-01-01", periods=96 * 10, freq=STEP, tz="UTC")
    rl = pd.Series(np.random.default_rng(1).normal(0, 100, len(idx)).cumsum(), index=idx)
    b = storage.ramp_bands(rl, (0.05, 0.95))
    lo, hi = b["1h"]
    r = rl.shift(-4) - rl
    assert lo == pytest.approx(r.quantile(0.05)) and hi == pytest.approx(r.quantile(0.95))


def test_partial_days_are_skipped():
    idx = pd.date_range(pd.Timestamp("2025-04-01", tz="Europe/Warsaw").tz_convert("UTC"), periods=96 + 40, freq=STEP)
    rl = pd.Series(np.r_[np.zeros(48), np.full(48, 1000.0), np.zeros(40)], index=idx)
    daily = storage.size_storage(rl, BANDS)
    assert len(daily) == 1
