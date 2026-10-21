import numpy as np
import pandas as pd
import pytest

from flex_ramps import rts
from rts79_load import hourly_load

TZ = "Europe/Warsaw"


def test_rts_fleet_matches_the_test_system():
    fleet = rts.Fleet.rts()
    assert len(fleet.capacity) == 26 and fleet.capacity.sum() == 3105.0
    cap, q = rts.rts_all_units()
    assert len(cap) == 32 and cap.sum() == 3405.0
    assert q[0] == pytest.approx(150 / 1250)              # U400: MTTR / (MTTF + MTTR)
    assert (fleet.mso <= fleet.capacity).all()
    must = rts.Fleet.rts(nuclear_must_run=True)
    assert must.mso[0] == 400 and must.ramp[0] == 0


def test_lole_reproduces_the_rts79_reference_value():
    """Billinton & Allan: hourly LOLE of the RTS-79 generating system with its chronological load is 9.394 h/yr."""
    cap, q = rts.rts_all_units()
    assert rts.lole_hours(hourly_load(), cap, q) == pytest.approx(9.394, abs=0.01)


def test_outage_weights():
    q = np.array([0.1, 0.2])
    w = rts.outage_weights(q)
    raw = np.array([0.9 * 0.8, 0.1 * 0.8, 0.2 * 0.9])
    assert w == pytest.approx(raw / raw.sum())


def test_merit_dispatch_meets_load_and_minimum_output():
    cap = np.array([100.0, 50.0, 30.0])
    mso = np.array([40.0, 20.0, 15.0])
    prod, curtail, unserved = rts.merit_dispatch(np.array([120.0, 105.0, 10.0, 200.0]), cap, mso)
    # 120: unit 1 full, unit 2 at 20
    assert prod[0] == pytest.approx([100.0, 20.0, 0.0])
    # 105: unit 2 would run at 5 < 20 -> raised to 20, unit 1 reduced to 85
    assert prod[1] == pytest.approx([85.0, 20.0, 0.0])
    # 10: below the first unit's MSO -> 30 MW over-generation
    assert prod[2] == pytest.approx([40.0, 0.0, 0.0]) and curtail[2] == pytest.approx(30.0)
    # 200: everything at capacity and 20 MW unserved
    assert prod[3] == pytest.approx(cap) and unserved[3] == pytest.approx(20.0)
    served = prod.sum(axis=1) - curtail
    assert served[:3] == pytest.approx([120.0, 105.0, 10.0])


def test_flexibility_online_and_offline_units():
    fleet = rts.Fleet(("a", "b"), capacity=np.array([100.0, 20.0]), mso=np.array([40.0, 15.0]),
                      ramp=np.array([1.0, 5.0]), start_min=np.array([np.inf, 30.0]),
                      forced_outage_rate=np.array([0.0, 0.0]), hydro_mw=0.0)
    prod = np.array([[70.0, 0.0]])
    # online a: min(1 MW/min * 60, 100 - 70) = 30; offline b (30 min start): min(5 * 30, 20) = 20
    assert rts.thermal_flexibility(prod, fleet, 60)[0] == pytest.approx(50.0)
    # 15 minutes: a gives 15, b cannot start
    assert rts.thermal_flexibility(prod, fleet, 15)[0] == pytest.approx(15.0)


def _net_load(values, freq="15min"):
    idx = pd.date_range(pd.Timestamp("2025-03-03", tz=TZ).tz_convert("UTC"), periods=len(values), freq=freq)
    return pd.Series(np.asarray(values, dtype=float), index=idx)


def test_hydro_uses_the_daily_energy_budget():
    nl = _net_load(np.r_[np.full(48, 1500.0), np.full(48, 2500.0)])        # one day, March (Q1 share 35 %)
    hydro, cap = rts.hydro_schedule(nl)
    budget = rts.HYDRO_ANNUAL_MWH * 0.35 / 90
    assert (hydro * 0.25).sum() == pytest.approx(budget, rel=1e-6)
    assert hydro.iloc[:48].max() == 0 and (hydro <= cap).all()


def test_irre_flat_load_has_no_deficit_and_a_jump_is_always_short():
    fleet = rts.Fleet.rts()
    flat = rts.irre(_net_load(np.full(96 * 2, 1500.0)), fleet, [0.25, 1.0])
    assert (flat["irre_obs"] == 0).all() and (flat["n_up"] == 0).all()
    # a 1500 MW jump in one quarter-hour cannot be met (15-minute flexibility is far smaller)
    jump = rts.irre(_net_load(np.r_[np.full(96, 1000.0), np.full(96, 2500.0)]), fleet, [0.25])
    assert jump.at[0.25, "n_up"] == 1 and jump.at[0.25, "irre_pct"] == pytest.approx(100.0)
    assert jump.at[0.25, "deficit_max"] > 0


def test_storage_flexibility_removes_deficits():
    fleet = rts.Fleet.rts()
    nl = _net_load(np.r_[np.full(96, 1000.0), np.full(96, 1600.0)])
    without = rts.irre(nl, fleet, [0.25])
    with_storage = rts.irre(nl, fleet, [0.25], storage_mw=600.0)
    assert without.at[0.25, "irre_obs"] > 0 and with_storage.at[0.25, "irre_obs"] == 0
    assert len(rts.deficits(nl, fleet, 0.25)) == 1


def test_negative_net_load_is_over_generation():
    cap = np.array([100.0, 50.0])
    mso = np.array([40.0, 20.0])
    prod, over, _ = rts.merit_dispatch(np.array([-50.0]), cap, mso)
    assert prod.sum() == 0 and over[0] == pytest.approx(50.0)
