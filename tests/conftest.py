"""Synthetic PSE-like data for offline tests (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

TZ = "Europe/Warsaw"


def synthetic_frame(start: str, end: str, freq: str = "15min", seed: int = 0, pv_peak: float = 12000.0) -> pd.DataFrame:
    """Demand with a daily and weekly cycle, a PV bell around local noon, noisy wind; MW, index = interval start UTC."""
    idx = pd.date_range(pd.Timestamp(start, tz=TZ).tz_convert("UTC"), pd.Timestamp(end, tz=TZ).tz_convert("UTC"),
                        freq=freq, inclusive="left", name="ts_utc")
    loc = idx.tz_convert(TZ)
    hour = (loc.hour + loc.minute / 60.0).to_numpy()
    doy = loc.dayofyear.to_numpy()
    rng = np.random.default_rng(seed)
    season = 0.5 + 0.5 * np.cos(2 * np.pi * (doy - 172) / 365.0)            # 1 in summer, 0 in winter
    demand = 17000 + 3000 * np.sin(np.pi * (hour - 6) / 14).clip(0) + 2000 * (1 - season)
    demand = demand - 1500 * (loc.dayofweek >= 5)
    pv = pv_peak * (0.3 + 0.7 * season) * np.exp(-((hour - 12.5) / 3.0) ** 2) * (hour > 5) * (hour < 21)
    wind = np.clip(3000 + np.cumsum(rng.normal(0, 60, len(idx))) % 4000, 0, None)
    conv = demand - pv - wind + 1500
    return pd.DataFrame({"demand": demand, "pv": pv, "wind": wind, "conv": conv, "net_import": -1500.0},
                        index=idx)


@pytest.fixture
def frames():
    """(15-minute frame for January 2025, hourly frame 2023-01-01..2025-02-01)."""
    q = synthetic_frame("2025-01-01", "2025-02-01")
    h = synthetic_frame("2023-01-01", "2025-02-01", freq="1h", seed=1)
    return q, h
