"""One pass of the analysis: residual-load statistics on the hourly history and the 15-minute API period, IRRE on
the stylised RTS system, storage sizing and the comparison with the PSE flexibility needs assessment.

Usage:
    result = analyse(quarter_frame, hourly_frame, base_year=2025, meta={...})
    result.summary()
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from flex_ramps import context, rts, storage
from flex_ramps.ramps import (HORIZONS, TZ, distribution, duration_curves, evening_ramps, midday_minima,
                              monthly_profile, period_keys, ramp, ramp_table, residual_load, trend)

PV_SCALES = (1.0, 1.2, 1.5)
BANDS = {"P5-P95": (0.05, 0.95), "P1-P99": (0.01, 0.99)}
IRRE_HORIZONS = [0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0]
IRRE_YEAR_HORIZONS = [1.0, 2.0, 3.0, 4.0]
HOURLY_HORIZONS = {"1h": pd.Timedelta("1h"), "3h": pd.Timedelta("3h")}
MIDDAY_THRESHOLDS = {"5gw": 5000.0, "3gw": 3000.0}
FULL_COVERAGE = 0.97
HIST_BINS = {"15min": 50.0, "1h": 200.0, "3h": 500.0}
log = logging.getLogger(__name__)


@dataclass
class Analysis:
    meta: dict
    base_year: int
    annual: pd.DataFrame
    monthly: pd.DataFrame
    duration: pd.DataFrame
    histograms: pd.DataFrame
    profile: pd.DataFrame
    duck: pd.DataFrame
    evening: pd.DataFrame
    midday: pd.DataFrame
    scenarios: pd.DataFrame
    irre_curve: pd.DataFrame
    irre_years: pd.DataFrame
    deficit_hours: pd.DataFrame
    storage: pd.DataFrame
    storage_daily: pd.DataFrame
    trends: dict = field(default_factory=dict)
    context: pd.DataFrame = field(default_factory=pd.DataFrame)
    extremes: dict = field(default_factory=dict)

    def summary(self) -> dict:
        a, sc, st, ic = self.annual, self.scenarios, self.storage, self.irre_curve
        y = str(self.base_year)
        out = {"meta": self.meta, "base_year": self.base_year}
        if y in a.index:
            row = a.loc[y]
            out["base_year_stats"] = {k: _clean(row[k]) for k in row.index}
        out["trends"] = self.trends
        out["extremes"] = self.extremes
        out["scenarios"] = {f"pv_x{k:.1f}": {c: _clean(v) for c, v in sc.loc[k].items()} for k in sc.index}
        out["storage"] = [{c: _clean(v) for c, v in r.items()} for r in st.reset_index().to_dict("records")]
        pick = ic[ic["horizon_h"].isin([1.0, 2.0, 3.0, 4.0])]
        out["irre"] = [{c: _clean(v) for c, v in r.items()} for r in pick.to_dict("records")]
        peak = ic.loc[ic.groupby(["variant", "pv_scale"])["irre_pct"].idxmax()]
        out["irre_peak"] = [{c: _clean(v) for c, v in r.items()} for r in peak.to_dict("records")]
        out["irre_years"] = {str(k): {c: _clean(v) for c, v in r.items()} for k, r in self.irre_years.iterrows()}
        return out


def _clean(v):
    if isinstance(v, (np.floating, float)):
        return None if np.isnan(v) else round(float(v), 4)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _expected_intervals(keys: pd.Index, freq: str, step: pd.Timedelta) -> pd.Series:
    out = {}
    for k in keys.unique():
        y = int(k[:4])
        if freq == "year":
            a, b = pd.Timestamp(y, 1, 1), pd.Timestamp(y + 1, 1, 1)
        else:
            m = int(k[5:7])
            a = pd.Timestamp(y, m, 1)
            b = a + pd.offsets.MonthBegin(1)
        out[k] = (b.tz_localize(TZ) - a.tz_localize(TZ)) / step
    return pd.Series(out)


def _day_keys(days: pd.Index, freq: str) -> pd.Index:
    d = pd.DatetimeIndex(days)
    if freq == "year":
        return pd.Index([f"{y:04d}" for y in d.year])
    return pd.Index([f"{y:04d}-{m:02d}" for y, m in zip(d.year, d.month)])


def period_table(hourly: pd.DataFrame, quarter: pd.DataFrame, evening: pd.DataFrame, midday: pd.DataFrame,
                 freq: str) -> pd.DataFrame:
    """Residual-load and ramp statistics per local calendar year or month."""
    rl = residual_load(hourly)
    keys = period_keys(hourly.index, freq)
    ok = rl.notna()
    t = pd.DataFrame(index=keys.unique().sort_values())
    t["coverage"] = ok.groupby(keys).sum() / _expected_intervals(keys, freq, pd.Timedelta("1h"))
    for col in ("demand", "pv", "wind"):
        t[f"{col}_twh"] = hourly[col].where(ok).groupby(keys).sum() / 1e6
    t["pv_share"] = t["pv_twh"] / t["demand_twh"]
    t["wind_share"] = t["wind_twh"] / t["demand_twh"]
    t["rl_mean"] = rl.groupby(keys).mean()
    t["rl_min"] = rl.groupby(keys).min()
    t["rl_max"] = rl.groupby(keys).max()
    t["conv_min"] = hourly["conv"].groupby(keys).min()
    t = t.join(ramp_table(rl, HOURLY_HORIZONS, freq).add_prefix("ramp"))
    if not quarter.empty:
        q = ramp_table(residual_load(quarter), HORIZONS, freq).add_prefix("q_ramp")
        t = t.join(q)
    ek = _day_keys(evening.index, freq)
    t["evening3h_mean"] = evening["ramp_3h"].groupby(ek).mean()
    t["evening3h_p95"] = evening["ramp_3h"].groupby(ek).quantile(0.95)
    t["evening3h_max"] = evening["ramp_3h"].groupby(ek).max()
    t["evening1h_mean"] = evening["ramp_1h"].groupby(ek).mean()
    t["lift_mean"] = evening["lift"].groupby(ek).mean()
    t["lift_max"] = evening["lift"].groupby(ek).max()
    mk = _day_keys(midday.index, freq)
    t["midday_min_mean"] = midday["residual"].groupby(mk).mean()
    t["midday_min_min"] = midday["residual"].groupby(mk).min()
    for label, thr in MIDDAY_THRESHOLDS.items():
        t[f"days_below_{label}"] = (midday["residual"] < thr).groupby(mk).sum()
    t["conv_at_midday_min"] = midday["conv"].groupby(mk).mean()
    t["export_at_midday_min"] = (-midday["net_import"]).groupby(mk).mean()
    return t


def _histograms(series: dict[str, pd.Series]) -> pd.DataFrame:
    rows = []
    for name, (horizon, values) in series.items():
        v = values.dropna()
        if v.empty:
            continue
        w = HIST_BINS[horizon]
        edges = np.arange(np.floor(v.min() / w) * w, np.ceil(v.max() / w) * w + w, w)
        counts, edges = np.histogram(v, bins=edges)
        centers = 0.5 * (edges[:-1] + edges[1:])
        rows.append(pd.DataFrame({"series": name, "horizon": horizon, "center": centers,
                                  "share": counts / counts.sum()}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def _duck(hourly: pd.DataFrame, months=(5, 6, 7, 8)) -> pd.DataFrame:
    """Mean residual load by local hour of day, May-August of each year (rows: year, columns: hour)."""
    rl = residual_load(hourly).dropna()
    loc = rl.index.tz_convert(TZ)
    sel = np.isin(loc.month, months)
    part = rl[sel]
    ploc = loc[sel]
    return part.groupby([ploc.year, ploc.hour]).mean().unstack()


def _year_slice(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    loc = frame.index.tz_convert(TZ)
    return frame[loc.year == year]


def scenario_table(qb: pd.DataFrame, hb: pd.DataFrame) -> tuple[pd.DataFrame, dict[float, pd.Series]]:
    rows, rls = {}, {}
    for k in PV_SCALES:
        rl = residual_load(qb, k)
        rls[k] = rl
        row = {"pv_share": float((k * qb["pv"]).sum() / qb["demand"].sum()),
               "rl_min": float(rl.min()), "rl_max": float(rl.max()),
               "intervals_rl_below_0": float((rl < 0).sum())}
        for label, h in HORIZONS.items():
            d = distribution(ramp(rl, h))
            row.update({f"ramp{label}_{s}": d[s] for s in ("p01", "p05", "p95", "p99", "min", "max")})
        ev = evening_ramps(rl)
        row.update({"evening3h_mean": float(ev["ramp_3h"].mean()), "evening3h_p95": float(ev["ramp_3h"].quantile(0.95)),
                    "evening3h_max": float(ev["ramp_3h"].max()), "lift_mean": float(ev["lift"].mean())})
        mid = midday_minima(qb, rl)
        row.update({"midday_min_mean": float(mid["residual"].mean()), "midday_min_min": float(mid["residual"].min())})
        for label, thr in MIDDAY_THRESHOLDS.items():
            row[f"days_below_{label}"] = float((mid["residual"] < thr).sum())
        hrl = residual_load(hb, k)
        row.update({f"hourly_ramp1h_{s}": v for s, v in distribution(ramp(hrl, pd.Timedelta("1h"))).items()
                    if s in ("p01", "p99", "min", "max")})
        rows[k] = row
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "pv_scale"
    return out, rls


def storage_tables(rls: dict[float, pd.Series]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, daily = [], []
    for band, quantiles in BANDS.items():
        bands = storage.ramp_bands(rls[1.0], quantiles)
        for k, rl in rls.items():
            d = storage.size_storage(rl, bands)
            log.info("storage %s, PV x%.1f: %d days", band, k, len(d))
            s = storage.summarise(d)
            rows.append({"band": band, "pv_scale": k, **s,
                         **{f"band_{h}_lo": lo for h, (lo, _) in bands.items()},
                         **{f"band_{h}_hi": hi for h, (_, hi) in bands.items()}})
            daily.append(d.assign(band=band, pv_scale=k).reset_index())
    return pd.DataFrame(rows).set_index(["band", "pv_scale"]), pd.concat(daily, ignore_index=True)


def irre_tables(qb: pd.DataFrame, rls: dict[float, pd.Series], storage_power: dict[float, float]
                ) -> tuple[pd.DataFrame, pd.DataFrame, dict[float, float]]:
    scale = rts.PEAK_LOAD_MW / float(qb["demand"].max())
    variants = {"RTS-96 data": rts.Fleet.rts(), "nuclear must-run": rts.Fleet.rts(nuclear_must_run=True)}
    rows, hours, lole = [], [], {}
    cap, q = rts.rts_all_units()
    for k, rl in rls.items():
        nl = rl * scale
        if nl.isna().any():
            log.warning("IRRE PV x%.1f: %d missing intervals in the reference year filled by interpolation", k,
                        int(nl.isna().sum()))
            nl = nl.interpolate(limit_area="inside").ffill().bfill()
        lole[k] = rts.lole_hours(nl.resample("1h").mean().dropna().to_numpy(), cap, q)
        runs = dict(variants)
        runs["RTS-96 data + storage"] = variants["RTS-96 data"]
        for name, fleet in runs.items():
            extra = storage_power.get(k, 0.0) * scale if name.endswith("storage") else 0.0
            t = rts.irre(nl, fleet, IRRE_HORIZONS, storage_mw=extra)
            t = t.reset_index().assign(variant=name, pv_scale=k, storage_mw_rts=extra)
            rows.append(t)
            if name == "RTS-96 data":
                for h in (2.0, 3.0):
                    d = rts.deficits(nl, fleet, h)
                    loc = d.index.tz_convert(TZ)
                    cnt = pd.Series(loc.hour).value_counts().reindex(range(24), fill_value=0)
                    hours.append(pd.DataFrame({"pv_scale": k, "horizon_h": h, "hour": range(24),
                                               "deficits": cnt.to_numpy()}))
    curve = pd.concat(rows, ignore_index=True)
    curve["scale"] = scale
    return curve, pd.concat(hours, ignore_index=True), lole


def irre_by_year(hourly: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    fleet = rts.Fleet.rts()
    cap, q = rts.rts_all_units()
    rows = {}
    for y in years:
        h = _year_slice(hourly, y)
        rl = residual_load(h).interpolate(limit=3, limit_area="inside")
        if rl.isna().any() or len(rl) < 8700:
            log.warning("IRRE %d skipped: incomplete hourly data", y)
            continue
        scale = rts.PEAK_LOAD_MW / float(h["demand"].max())
        nl = rl * scale
        t = rts.irre(nl, fleet, IRRE_YEAR_HORIZONS)
        row = {f"irre_pct_{int(hh)}h": t.at[hh, "irre_pct"] for hh in IRRE_YEAR_HORIZONS}
        row.update({f"irre_hours_{int(hh)}h": t.at[hh, "irre_hours"] for hh in IRRE_YEAR_HORIZONS})
        row["lole_h"] = rts.lole_hours(nl.to_numpy(), cap, q)
        row["scale"] = scale
        rows[str(y)] = row
    out = pd.DataFrame.from_dict(rows, orient="index")
    out.index.name = "year"
    return out


def extremes(rl: pd.Series, horizons: dict[str, pd.Timedelta] = HORIZONS) -> dict:
    """Largest rise and fall per horizon and the lowest residual load, with their local start times."""
    def at(ts) -> str:
        return f"{pd.Timestamp(ts).tz_convert(TZ):%Y-%m-%d %H:%M}"

    out = {}
    for label, h in horizons.items():
        r = ramp(rl, h).dropna()
        if r.empty:
            continue
        out[label] = {"max": float(r.max()), "max_at": at(r.idxmax()), "min": float(r.min()), "min_at": at(r.idxmin())}
    v = rl.dropna()
    if not v.empty:
        out["residual_min"] = {"value": float(v.min()), "at": at(v.idxmin())}
        out["residual_max"] = {"value": float(v.max()), "at": at(v.idxmax())}
    return out


def context_table(annual: pd.DataFrame, scenarios: pd.DataFrame, base_year: int) -> pd.DataFrame:
    y = str(base_year)
    rows = []
    for stat in ("min", "p01", "p05", "p95", "p99", "max"):
        rows.append({
            "statistic": stat,
            "pse_fna_2030": context.PSE_FNA_RAMPS["2030"][stat],
            "pse_fna_2035": context.PSE_FNA_RAMPS["2035"][stat],
            f"observed_{y}_hourly": annual.at[y, f"ramp1h_{stat}"] if y in annual.index else np.nan,
            f"observed_{y}_15min_data": annual.at[y, f"q_ramp1h_{stat}"] if y in annual.index else np.nan,
            f"{y}_pv_x1.5_hourly": scenarios.at[1.5, f"hourly_ramp1h_{stat}"]
            if f"hourly_ramp1h_{stat}" in scenarios else np.nan,
        })
    return pd.DataFrame(rows).set_index("statistic")


def analyse(quarter: pd.DataFrame, hourly: pd.DataFrame, base_year: int, meta: dict | None = None,
            run_irre_years: bool = True) -> Analysis:
    meta = dict(meta or {})
    rl_h = residual_load(hourly)
    evening = evening_ramps(rl_h)
    midday = midday_minima(hourly, rl_h)
    annual = period_table(hourly, quarter, evening, midday, "year")
    monthly = period_table(hourly, quarter, evening, midday, "month")
    full_years = [int(k) for k in annual.index if annual.at[k, "coverage"] >= FULL_COVERAGE]
    log.info("full years in the hourly history: %s", full_years)

    qb = _year_slice(quarter, base_year)
    hb = _year_slice(hourly, base_year)
    scenarios, rls = scenario_table(qb, hb)
    storage_summary, storage_daily = storage_tables(rls)
    p_cover = {k: storage_summary.at[("P1-P99", k), "power_max_mw"] for k in PV_SCALES}
    irre_curve, deficit_hours, lole = irre_tables(qb, rls, p_cover)
    scenarios["lole_h_rts"] = pd.Series(lole)
    irre_years = irre_by_year(hourly, full_years) if run_irre_years else pd.DataFrame()

    duration = duration_curves(rl_h)
    for k in PV_SCALES[1:]:
        duration[f"{base_year} PV +{(k - 1) * 100:.0f}%"] = duration_curves(residual_load(hb, k))[str(base_year)]
    first = str(full_years[0]) if full_years else None
    hist_series = {
        f"{base_year}": ("15min", ramp(rls[1.0], HORIZONS["15min"])),
        f"{base_year} PV +50%": ("15min", ramp(rls[1.5], HORIZONS["15min"])),
    }
    for label in ("1h", "3h"):
        hist_series[f"{base_year} {label}"] = (label, ramp(rls[1.0], HORIZONS[label]))
        hist_series[f"{base_year} PV +50% {label}"] = (label, ramp(rls[1.5], HORIZONS[label]))
        if first:
            h_first = residual_load(_year_slice(hourly, int(first)))
            hist_series[f"{first} hourly {label}"] = (label, ramp(h_first, HOURLY_HORIZONS[label]))
            hist_series[f"{base_year} hourly {label}"] = (label, ramp(residual_load(hb), HOURLY_HORIZONS[label]))

    full = annual.loc[[str(y) for y in full_years]]
    trends = {c: trend(full[c]) for c in ("ramp1h_p99", "ramp1h_p01", "ramp1h_max", "ramp3h_p99", "ramp3h_p01",
                                          "evening3h_mean", "evening3h_max", "lift_mean", "midday_min_mean",
                                          "rl_min", "days_below_5gw", "conv_at_midday_min", "export_at_midday_min",
                                          "pv_share") if c in full}
    meta.update({"full_years": full_years, "base_year": base_year})
    return Analysis(
        meta=meta, base_year=base_year, annual=annual, monthly=monthly, duration=duration,
        histograms=_histograms(hist_series), profile=monthly_profile(residual_load(quarter)) if not quarter.empty
        else pd.DataFrame(), duck=_duck(hourly), evening=evening, midday=midday, scenarios=scenarios,
        irre_curve=irre_curve, irre_years=irre_years, deficit_hours=deficit_hours, storage=storage_summary,
        storage_daily=storage_daily, trends=trends, context=context_table(annual, scenarios, base_year),
        extremes={f"{base_year}_15min": extremes(rls[1.0]),
                  f"{base_year}_pv_x1.5_15min": extremes(rls[1.5]),
                  "history_hourly": extremes(rl_h, HOURLY_HORIZONS)})
