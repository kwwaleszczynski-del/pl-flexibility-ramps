"""Stylised test system: IEEE RTS generating units, a merit-order dispatch and the insufficient ramping resource
expectation (IRRE).

Units: the 32 units of the IEEE Reliability Test System 1979 (3405 MW; unit sizes, numbers, MTTF and MTTR from
Table 6 of the RTS-79 paper) with the operating data published with RTS-96 (Grigg et al. 1999): minimum stable
output from Table 9 (lowest point of each heat-rate curve), ramp rate and hot start-up time from Table 10.

IRRE follows the high-level method of Lannoye, Flynn and O'Malley (IEEE PES General Meeting 2012), the screening
version of the metric introduced in Lannoye et al., IEEE Trans. Power Systems 27(2), 2012:

* each observation t of the net load is dispatched in merit order (non-chronological), with the marginal unit
  raised to its minimum stable output (MSO) at the expense of the previous unit;
* the available upward flexibility over horizon i is, for online units, min(RR * i, capacity - production) and,
  for offline units that can start within i, min(RR * (i - start time), capacity);
* the deficit is D(t, i) = NLR(t, i) - F(t, i) with NLR(t, i) = NL(t + i) - NL(t); IRRE counts the upward ramps
  with D > 0;
* forced outages enter through the single-outage states, weighted by
  Coeff1 = prod(1 - FOR_r) and Coeff2_r = FOR_r * prod_{j != r}(1 - FOR_j).

The RTS hydro units (6 x 50 MW, 200 GWh a year, quarterly shares 35/35/10/20 %, 90 % capacity in the second half
of the year; RTS-96 Table 9 note) are energy-limited: they shave each day's highest net load with that day's share
of the annual energy, and their unused capacity counts as instantaneous upward flexibility.

Usage:
    fleet = Fleet.rts()
    table = irre(net_load_15min, fleet, horizons_h=[0.25, 1, 3])
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from flex_ramps.ramps import TZ, step_of

PEAK_LOAD_MW = 2850.0   # RTS-79 annual peak load


@dataclass(frozen=True)
class UnitGroup:
    name: str
    fuel: str
    capacity: float    # MW
    count: int
    mso: float         # minimum stable output, MW (RTS-96 Table 9)
    ramp: float        # MW/min (RTS-96 Table 10)
    start_h: float     # hot start-up time, h (RTS-96 Table 10); inf = no start within the horizons studied
    mttf: float        # h (RTS-79 Table 6)
    mttr: float        # h

    @property
    def forced_outage_rate(self) -> float:
        return self.mttr / (self.mttf + self.mttr)


# Thermal units in the assumed merit order: nuclear, coal, #6 oil steam, #2 oil combustion turbines; within a fuel
# by full-load heat rate (RTS-96 Table 9).
THERMAL = [
    UnitGroup("U400", "nuclear", 400, 2, 100.0, 20, np.inf, 1100, 150),
    UnitGroup("U350", "coal", 350, 1, 140.0, 4, 8, 1150, 100),
    UnitGroup("U155", "coal", 155, 4, 54.25, 3, 3, 960, 40),
    UnitGroup("U76", "coal", 76, 4, 15.2, 2, 3, 1960, 40),
    UnitGroup("U197", "oil steam", 197, 3, 68.95, 3, 4, 950, 50),
    UnitGroup("U100", "oil steam", 100, 3, 25.0, 7, 2, 1200, 50),
    UnitGroup("U12", "oil steam", 12, 5, 2.4, 1, 2, 2940, 60),
    UnitGroup("U20", "oil CT", 20, 4, 15.8, 3, 0, 450, 50),
]
HYDRO = UnitGroup("U50", "hydro", 50, 6, 0.0, np.inf, 0, 1980, 20)
HYDRO_ANNUAL_MWH = 200_000.0
HYDRO_QUARTER_SHARES = (0.35, 0.35, 0.10, 0.20)
HYDRO_SECOND_HALF = 0.9


@dataclass(frozen=True)
class Fleet:
    """Thermal units in merit order, one entry per unit."""
    names: tuple[str, ...]
    capacity: np.ndarray
    mso: np.ndarray
    ramp: np.ndarray
    start_min: np.ndarray
    forced_outage_rate: np.ndarray
    hydro_mw: float = HYDRO.capacity * HYDRO.count

    @classmethod
    def from_groups(cls, groups: list[UnitGroup], hydro_mw: float = HYDRO.capacity * HYDRO.count) -> "Fleet":
        units = [g for g in groups for _ in range(g.count)]
        return cls(names=tuple(g.name for g in units),
                   capacity=np.array([g.capacity for g in units], dtype=float),
                   mso=np.array([g.mso for g in units], dtype=float),
                   ramp=np.array([g.ramp for g in units], dtype=float),
                   start_min=np.array([60.0 * g.start_h for g in units], dtype=float),
                   forced_outage_rate=np.array([g.forced_outage_rate for g in units], dtype=float),
                   hydro_mw=hydro_mw)

    @classmethod
    def rts(cls, nuclear_must_run: bool = False) -> "Fleet":
        """RTS-79 units with RTS-96 operating data. ``nuclear_must_run`` fixes the nuclear units at full output (no
        ramping, MSO = capacity), the treatment used by the RTS-GMLC update instead of the RTS-96 values."""
        groups = list(THERMAL)
        if nuclear_must_run:
            g = groups[0]
            groups[0] = UnitGroup(g.name, g.fuel, g.capacity, g.count, g.capacity, 0.0, np.inf, g.mttf, g.mttr)
        return cls.from_groups(groups)

    def without(self, r: int) -> "Fleet":
        keep = np.arange(len(self.capacity)) != r
        return Fleet(tuple(n for i, n in enumerate(self.names) if keep[i]), self.capacity[keep], self.mso[keep],
                     self.ramp[keep], self.start_min[keep], self.forced_outage_rate[keep], self.hydro_mw)


def outage_weights(forced_outage_rate: np.ndarray) -> np.ndarray:
    """Weights of the all-available state and of each single-unit outage (Lannoye et al. 2012, eq. 5-7)."""
    q = np.asarray(forced_outage_rate, dtype=float)
    avail = np.prod(1.0 - q)
    single = np.array([q[r] * np.prod(np.delete(1.0 - q, r)) for r in range(len(q))])
    w = np.concatenate([[avail], single])
    return w / w.sum()


def merit_dispatch(x: np.ndarray, capacity: np.ndarray, mso: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Merit-order production of each unit (rows: observations) for net loads ``x``.

    Units below the marginal one run at capacity. A marginal unit scheduled below its MSO is raised to it and the
    previous units are reduced, never below their own MSO. Returns (production, over-generation that has to be
    curtailed - including a negative net load, i.e. variable generation above demand - and unserved load).
    """
    x = np.asarray(x, dtype=float)
    n = len(capacity)
    cum = np.cumsum(capacity)
    unserved = np.maximum(x - cum[-1], 0.0)
    xx = np.clip(x, 0.0, cum[-1])
    m = np.minimum(np.searchsorted(cum, xx, side="left"), n - 1)
    prev = np.where(m > 0, cum[np.maximum(m - 1, 0)], 0.0)
    rows = np.arange(len(x))
    prod = np.where(np.arange(n)[None, :] < m[:, None], capacity[None, :], 0.0)
    pm = xx - prev
    prod[rows, m] = pm
    need = np.where(pm > 0, np.maximum(mso[m] - pm, 0.0), 0.0)
    prod[rows, m] += need
    for j in range(n - 1, -1, -1):
        active = (j < m) & (need > 0)
        if not active.any():
            continue
        cut = np.minimum(need, np.where(active, prod[:, j] - mso[j], 0.0))
        prod[:, j] -= cut
        need = need - cut
    return prod, need + np.maximum(-x, 0.0), unserved


def thermal_flexibility(prod: np.ndarray, fleet: Fleet, horizon_min: float) -> np.ndarray:
    """Upward flexibility of the thermal units over ``horizon_min`` minutes (Lannoye et al. 2012, eq. 2-3)."""
    online = prod > 0
    up_online = np.minimum(fleet.ramp[None, :] * horizon_min, fleet.capacity[None, :] - prod)
    can_start = fleet.start_min <= horizon_min
    up_offline = np.where(can_start,
                          np.minimum(fleet.ramp * np.maximum(horizon_min - fleet.start_min, 0.0), fleet.capacity),
                          0.0)
    return np.where(online, up_online, up_offline[None, :]).sum(axis=1)


def hydro_schedule(nl: pd.Series, hydro_mw: float = HYDRO.capacity * HYDRO.count) -> tuple[pd.Series, pd.Series]:
    """Daily peak shaving by the energy-limited RTS hydro units: (hydro output, available hydro capacity), MW."""
    step_h = step_of(nl.index) / pd.Timedelta("1h")
    loc = nl.index.tz_convert(TZ)
    cap = np.where(loc.month <= 6, hydro_mw, HYDRO_SECOND_HALF * hydro_mw)
    out = np.zeros(len(nl))
    day = loc.tz_localize(None).normalize()
    values = nl.to_numpy(dtype=float)
    for d, pos in pd.Series(np.arange(len(nl)), index=day).groupby(level=0):
        idx = pos.to_numpy()
        q = (d.month - 1) // 3
        q_start = pd.Timestamp(d.year, 3 * q + 1, 1)
        q_days = ((q_start + pd.offsets.QuarterEnd(0)) - q_start).days + 1
        budget = HYDRO_ANNUAL_MWH * HYDRO_QUARTER_SHARES[q] / q_days
        out[idx] = _water_fill(values[idx], cap[idx], budget, step_h)
    return pd.Series(out, index=nl.index, name="hydro"), pd.Series(cap, index=nl.index, name="hydro_cap")


def _water_fill(x: np.ndarray, cap: np.ndarray, energy: float, step_h: float) -> np.ndarray:
    """Output min(cap, max(0, x - level)) with the level chosen so that the energy equals ``energy`` (MWh)."""
    if (cap * step_h).sum() <= energy:
        return np.minimum(cap, np.maximum(x, 0.0))
    lo, hi = float(np.min(x) - np.max(cap)), float(np.max(x))
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        e = (np.clip(x - mid, 0.0, cap) * step_h).sum()
        lo, hi = (mid, hi) if e > energy else (lo, mid)
    return np.minimum(np.clip(x - hi, 0.0, cap), np.maximum(x, 0.0))


def irre(nl: pd.Series, fleet: Fleet, horizons_h: list[float], storage_mw: float = 0.0,
         outages: bool = True) -> pd.DataFrame:
    """IRRE of ``fleet`` against the net load ``nl`` (MW, complete regular series) for each horizon.

    Columns: n_up (upward net-load ramps), irre_obs (expected number of upward ramps without sufficient
    flexibility, outage-weighted), irre_pct (% of upward ramps), irre_hours (irre_obs x step),
    deficit_mean / deficit_max (MW, all-available state, over the deficit observations), curtailment_mwh and
    unserved_mwh (all-available state). ``storage_mw`` adds a resource whose agreed flexibility is available at
    all times (as the paper assumes for storage and demand response).
    """
    if nl.isna().any():
        raise ValueError("net load must be complete")
    step = step_of(nl.index)
    step_min = step / pd.Timedelta("1min")
    hydro, hydro_cap = hydro_schedule(nl, fleet.hydro_mw)
    x = (nl - hydro).to_numpy()
    hydro_room = (hydro_cap - hydro).to_numpy()
    load = nl.to_numpy(dtype=float)
    states = [fleet] + ([fleet.without(r) for r in range(len(fleet.capacity))] if outages else [])
    weights = outage_weights(fleet.forced_outage_rate) if outages else np.array([1.0])
    rows = {}
    base = {}
    for s, (state, w) in enumerate(zip(states, weights)):
        prod, curtail, unserved = merit_dispatch(x, state.capacity, state.mso)
        if s == 0:
            base = {"curtailment_mwh": float(curtail.sum() * step_min / 60), "unserved_mwh": float(unserved.sum() * step_min / 60)}
        for h in horizons_h:
            k = int(round(h * 60 / step_min))
            nlr = load[k:] - load[:-k]
            flex = thermal_flexibility(prod[:-k], state, h * 60) + hydro_room[:-k] + storage_mw
            deficit = nlr - flex
            hit = (nlr > 0) & (deficit > 0)
            r = rows.setdefault(h, {"n_up": float((nlr > 0).sum()), "irre_obs": 0.0})
            r["irre_obs"] += w * hit.sum()
            if s == 0:
                r["deficit_mean"] = float(deficit[hit].mean()) if hit.any() else 0.0
                r["deficit_max"] = float(deficit[hit].max()) if hit.any() else 0.0
                r["obs_all_available"] = float(hit.sum())
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "horizon_h"
    out["irre_pct"] = 100.0 * out["irre_obs"] / out["n_up"]
    out["irre_hours"] = out["irre_obs"] * step_min / 60.0
    for k, v in base.items():
        out[k] = v
    return out


def deficits(nl: pd.Series, fleet: Fleet, horizon_h: float, storage_mw: float = 0.0) -> pd.Series:
    """Flexibility deficit D(t, i) > 0 of the all-available state, labelled by the start of the ramp (MW)."""
    step_min = step_of(nl.index) / pd.Timedelta("1min")
    hydro, hydro_cap = hydro_schedule(nl, fleet.hydro_mw)
    prod, _, _ = merit_dispatch((nl - hydro).to_numpy(), fleet.capacity, fleet.mso)
    k = int(round(horizon_h * 60 / step_min))
    load = nl.to_numpy(dtype=float)
    nlr = load[k:] - load[:-k]
    flex = thermal_flexibility(prod[:-k], fleet, horizon_h * 60) + (hydro_cap - hydro).to_numpy()[:-k] + storage_mw
    d = pd.Series(nlr - flex, index=nl.index[:-k], name="deficit")
    return d[(nlr > 0) & (d > 0)]


def capacity_outage_table(capacity: np.ndarray, forced_outage_rate: np.ndarray, step_mw: float = 1.0) -> np.ndarray:
    """Probability mass of the capacity on outage, in steps of ``step_mw`` (two-state units, exact convolution)."""
    size = int(round(capacity.sum() / step_mw)) + 1
    p = np.zeros(size)
    p[0] = 1.0
    for c, q in zip(capacity, forced_outage_rate):
        k = int(round(c / step_mw))
        shifted = np.zeros(size)
        shifted[k:] = p[:size - k]
        p = (1.0 - q) * p + q * shifted
    return p


def lole_hours(hourly_load: np.ndarray, capacity: np.ndarray, forced_outage_rate: np.ndarray) -> float:
    """Loss of load expectation (h per series): sum over hours of P(available capacity < load)."""
    p = capacity_outage_table(capacity, forced_outage_rate)
    total = capacity.sum()
    # P(outage > total - load) = P(available < load)
    tail = np.concatenate([np.cumsum(p[::-1])[::-1], [0.0]])   # tail[k] = P(outage >= k MW)
    k = np.floor(total - np.asarray(hourly_load, dtype=float)).astype(int) + 1
    k = np.clip(k, 0, len(p))
    return float(tail[k].sum())


def rts_all_units() -> tuple[np.ndarray, np.ndarray]:
    """Capacity and forced outage rate of all 32 RTS-79 units (thermal and hydro), for adequacy (LOLE)."""
    groups = THERMAL + [HYDRO]
    cap = np.array([g.capacity for g in groups for _ in range(g.count)], dtype=float)
    q = np.array([g.forced_outage_rate for g in groups for _ in range(g.count)], dtype=float)
    return cap, q
