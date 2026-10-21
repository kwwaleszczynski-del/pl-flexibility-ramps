"""How much storage would keep residual-load ramps inside a band: a small linear programme per day.

For one local day with residual load L_t (MW, 15-minute steps) the storage charges c_t and discharges d_t, so the
rest of the system sees L'_t = L_t + c_t - d_t. For every horizon k (15 min, 1 h, 3 h) the ramps of L' must stay
inside a band [lo_k, hi_k], here the P5-P95 or P1-P99 range of the ramps observed in the reference year:

    lo_k <= L'_{t+k} - L'_t <= hi_k

    e_t = e_{t-1} + dt (eta_c c_t - d_t / eta_d),  e_{-1} = e_{N-1}  (state of charge back to its start each day)
    0 <= c_t, d_t <= P,   0 <= e_t <= E

First the smallest power P is found, then the smallest energy E at that power. Days are independent, so a storage
with the largest daily P and the largest daily E satisfies the band on every day.

Usage:
    bands = ramp_bands(rl_2025, quantiles=(0.05, 0.95))
    daily = size_storage(rl_2025 * 1.0, bands)
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import linprog

from flex_ramps.ramps import TZ, day_lengths, ramp, regular, step_of

LAGS = {"15min": pd.Timedelta("15min"), "1h": pd.Timedelta("1h"), "3h": pd.Timedelta("3h")}
ROUND_TRIP = 0.85
log = logging.getLogger(__name__)


def ramp_bands(rl: pd.Series, quantiles: tuple[float, float]) -> dict[str, tuple[float, float]]:
    """(lower, upper) quantile of the ramps for each horizon in LAGS."""
    out = {}
    for label, h in LAGS.items():
        r = ramp(rl, h).dropna()
        out[label] = (float(r.quantile(quantiles[0])), float(r.quantile(quantiles[1])))
    return out


def _day_problem(load: np.ndarray, lags: list[tuple[int, float, float]], dt_h: float, eta: float):
    n = len(load)
    nv = 3 * n + 2
    ic, id_, ie, ip, iE = 0, n, 2 * n, 3 * n, 3 * n + 1
    rows, cols, vals, rhs = [], [], [], []
    r = 0

    def add(entries, b):
        nonlocal r
        for col, val in entries:
            rows.append(r)
            cols.append(col)
            vals.append(val)
        rhs.append(b)
        r += 1

    for t in range(n):
        add([(ic + t, 1.0), (ip, -1.0)], 0.0)
        add([(id_ + t, 1.0), (ip, -1.0)], 0.0)
        add([(ie + t, 1.0), (iE, -1.0)], 0.0)
    for k, lo, hi in lags:
        for t in range(n - k):
            dl = load[t + k] - load[t]
            # (c - d)_{t+k} - (c - d)_t <= hi - dl   and   >= lo - dl
            entries = [(ic + t + k, 1.0), (id_ + t + k, -1.0), (ic + t, -1.0), (id_ + t, 1.0)]
            add(entries, hi - dl)
            add([(c, -v) for c, v in entries], dl - lo)
    a_ub = sparse.csr_matrix((vals, (rows, cols)), shape=(r, nv))
    b_ub = np.array(rhs)
    eq_rows, eq_cols, eq_vals = [], [], []
    eta_c = eta_d = np.sqrt(eta)
    for t in range(n):
        prev = ie + (t - 1) % n
        for col, val in ((ie + t, 1.0), (prev, -1.0), (ic + t, -dt_h * eta_c), (id_ + t, dt_h / eta_d)):
            eq_rows.append(t)
            eq_cols.append(col)
            eq_vals.append(val)
    a_eq = sparse.csr_matrix((eq_vals, (eq_rows, eq_cols)), shape=(n, nv))
    return a_ub, b_ub, a_eq, np.zeros(n), (ic, id_, ip, iE, nv)


def size_day(load: np.ndarray, bands: dict[str, tuple[float, float]], step: pd.Timedelta,
             eta: float = ROUND_TRIP) -> tuple[float, float, float]:
    """Smallest (power MW, energy MWh) keeping one day's ramps inside ``bands``; also the discharged energy, MWh."""
    dt_h = step / pd.Timedelta("1h")
    lags = []
    violated = False
    for label, (lo, hi) in bands.items():
        k = int(round(LAGS[label] / step))
        if k >= len(load):
            continue
        dl = load[k:] - load[:-k]
        violated |= bool(((dl > hi + 1e-9) | (dl < lo - 1e-9)).any())
        lags.append((k, lo, hi))
    if not violated:
        return 0.0, 0.0, 0.0
    a_ub, b_ub, a_eq, b_eq, (ic, id_, ip, iE, nv) = _day_problem(load, lags, dt_h, eta)
    n = len(load)
    tiny = 1e-6
    cost = np.zeros(nv)
    cost[ip] = 1.0
    cost[iE] = tiny
    cost[ic:ic + n] = cost[id_:id_ + n] = tiny
    res = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=(0, None), method="highs")
    if res.status != 0:
        raise RuntimeError(f"storage LP failed: {res.message}")
    p_star = res.x[ip]
    cost2 = np.zeros(nv)
    cost2[iE] = 1.0
    cost2[ic:ic + n] = cost2[id_:id_ + n] = tiny
    cap_row = sparse.csr_matrix(([1.0], ([0], [ip])), shape=(1, nv))
    res2 = linprog(cost2, A_ub=sparse.vstack([a_ub, cap_row]), b_ub=np.append(b_ub, p_star * (1 + 1e-6) + 1e-3),
                   A_eq=a_eq, b_eq=b_eq, bounds=(0, None), method="highs")
    if res2.status != 0:
        raise RuntimeError(f"storage LP (energy) failed: {res2.message}")
    discharged = float(res2.x[id_:id_ + n].sum() * dt_h)
    return float(p_star), float(res2.x[iE]), discharged


def size_storage(rl: pd.Series, bands: dict[str, tuple[float, float]], eta: float = ROUND_TRIP) -> pd.DataFrame:
    """Daily storage requirement (power_mw, energy_mwh, discharged_mwh) for every complete local day of ``rl``."""
    s = regular(rl)
    step = step_of(s.index)
    day = s.index.tz_convert(TZ).tz_localize(None).normalize()
    lengths = day_lengths(day.unique(), step)
    out = {}
    for d, part in s.groupby(day):
        if part.isna().any() or len(part) != lengths[d]:
            continue
        out[d] = size_day(part.to_numpy(dtype=float), bands, step, eta)
    frame = pd.DataFrame.from_dict(out, orient="index", columns=["power_mw", "energy_mwh", "discharged_mwh"])
    frame.index.name = "day"
    return frame


def summarise(daily: pd.DataFrame) -> dict[str, float]:
    """Storage that covers every day (largest daily power and energy) and the 95th percentile of the days."""
    active = daily[daily["power_mw"] > 1e-3]
    return {
        "days": float(len(daily)),
        "days_active": float(len(active)),
        "power_max_mw": float(daily["power_mw"].max()),
        "energy_max_mwh": float(daily["energy_mwh"].max()),
        "power_p95_mw": float(daily["power_mw"].quantile(0.95)),
        "energy_p95_mwh": float(daily["energy_mwh"].quantile(0.95)),
        "power_mean_active_mw": float(active["power_mw"].mean()) if len(active) else 0.0,
        "discharged_gwh": float(daily["discharged_mwh"].sum() / 1000.0),
    }
