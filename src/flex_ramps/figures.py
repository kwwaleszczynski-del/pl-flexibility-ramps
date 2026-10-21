"""README figures (matplotlib, light theme, PNG).

Colours: categorical slots blue / orange / aqua for the PV scenarios (as built, +20 %, +50 %) and for the three
measures of the midday chart; a four-step blue ramp for highlighted years (older years in grey).

Usage:
    save_all(result, Path("docs/img"))
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from flex_ramps.analysis import Analysis, PV_SCALES  # noqa: E402

INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
RAMP = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
OLD = "#c3c2b7"


def scenario_label(k: float) -> str:
    return "as built" if k == 1.0 else f"PV +{(k - 1) * 100:.0f}%"


def _style(ax, ylabel: str | None = None, grid: str = "both"):
    ax.set_facecolor(SURFACE)
    ax.grid(True, axis=grid, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=9)


def _figure(ncols: int = 1, width: float = 10.0, height: float = 4.0):
    fig, axes = plt.subplots(1, ncols, figsize=(width, height), facecolor=SURFACE, squeeze=False)
    return fig, list(axes[0])


def _legend(ax, **kw):
    leg = ax.legend(frameon=False, fontsize=8.5, labelcolor=INK2, **kw)
    return leg


def highlight_years(years: list[str], n: int = 4) -> dict[str, str]:
    """Up to ``n`` evenly spread years (always the last) get the ramp; the rest are grey."""
    if not years:
        return {}
    pick = sorted({years[int(round(i))] for i in np.linspace(0, len(years) - 1, min(n, len(years)))})
    colours = RAMP[-len(pick):]
    return {y: (colours[pick.index(y)] if y in pick else OLD) for y in years}


def fig_duck(r: Analysis, path: Path) -> Path:
    fig, (ax,) = _figure(height=4.2)
    duck = r.duck
    years = [str(y) for y in duck.index]
    colours = highlight_years(years)
    for y, row in duck.iterrows():
        c = colours[str(y)]
        ax.plot(row.index, row.to_numpy() / 1000, color=c, linewidth=2.0 if c != OLD else 1.2,
                label=str(y) if c != OLD else None, zorder=3 if c != OLD else 2)
    ax.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
    _style(ax, "GW")
    ax.set_title("Residual load by hour of day, May-August mean (grey: other years)", color=INK, fontsize=11,
                 loc="left")
    _legend(ax, loc="lower left", ncol=4)
    return _save(fig, path)


def fig_duration(r: Analysis, path: Path) -> Path:
    fig, (a1, a2) = _figure(2, height=4.0)
    d = r.duration
    full = [str(y) for y in r.meta.get("full_years", [])]
    colours = highlight_years(full)
    for y in full:
        c = colours[y]
        a1.plot(d.index, d[y] / 1000, color=c, linewidth=2.0 if c != OLD else 1.2, label=y if c != OLD else None)
    a1.set_title("Residual-load duration curves, full years", color=INK, fontsize=10.5, loc="left")
    by = str(r.base_year)
    cols = [by] + [c for c in d.columns if c.startswith(f"{by} PV")]
    for c, colour in zip(cols, SERIES):
        a2.plot(d.index, d[c] / 1000, color=colour, linewidth=2.0, label=c.replace(f"{by} ", "") if c != by else
                f"{by} as built")
    a2.set_title(f"{by} with more PV (static scaling)", color=INK, fontsize=10.5, loc="left")
    for ax in (a1, a2):
        _style(ax, "GW")
        ax.set_xlabel("% of hours exceeded", color=INK2, fontsize=9)
        ax.axhline(0, color=AXIS, linewidth=0.8)
        _legend(ax, loc="upper right")
    return _save(fig, path)


def fig_distributions(r: Analysis, path: Path) -> Path:
    fig, axes = _figure(3, height=3.8)
    h = r.histograms
    by = str(r.base_year)
    for ax, horizon in zip(axes, ("15min", "1h", "3h")):
        names = [by, f"{by} PV +50%"] if horizon == "15min" else [f"{by} {horizon}", f"{by} PV +50% {horizon}"]
        for name, colour, label in zip(names, (SERIES[0], SERIES[2]), (f"{by} as built", "PV +50%")):
            part = h[(h["series"] == name) & (h["horizon"] == horizon)]
            if part.empty:
                continue
            ax.step(part["center"] / 1000, 100 * part["share"], where="mid", color=colour, linewidth=1.6, label=label)
        sc = r.scenarios
        for k, colour in ((1.0, SERIES[0]), (1.5, SERIES[2])):
            for stat in ("p01", "p99"):
                ax.axvline(sc.at[k, f"ramp{horizon}_{stat}"] / 1000, color=colour, linewidth=0.9, linestyle=":")
        _style(ax, "% of intervals" if horizon == "15min" else None)
        ax.set_xlabel(f"{horizon} change of residual load, GW", color=INK2, fontsize=9)
        ax.set_title(f"{horizon} ramps", color=INK, fontsize=10.5, loc="left")
    _legend(axes[0], loc="upper left")
    axes[2].text(0.98, 0.95, "dotted: P1 and P99", transform=axes[2].transAxes, ha="right", va="top",
                 color=MUTED, fontsize=8)
    return _save(fig, path)


def _months(index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(f"{m}-01") for m in index])


def fig_trend(r: Analysis, path: Path) -> Path:
    fig, (a1, a2) = _figure(2, height=4.0)
    m = r.monthly[r.monthly["coverage"] >= 0.9]
    x = _months(m.index)
    for ax, horizon in ((a1, "1h"), (a2, "3h")):
        up, down = m[f"ramp{horizon}_p99"] / 1000, -m[f"ramp{horizon}_p01"] / 1000
        ax.plot(x, up, color=SERIES[0], linewidth=1.2, alpha=0.6)
        ax.plot(x, down, color=SERIES[1], linewidth=1.2, alpha=0.6)
        ax.plot(x, up.rolling(12, min_periods=12).mean(), color=SERIES[0], linewidth=2.2, label="upward (P99)")
        ax.plot(x, down.rolling(12, min_periods=12).mean(), color=SERIES[1], linewidth=2.2,
                label="downward (|P1|)")
        _style(ax, "GW" if horizon == "1h" else None)
        ax.set_title(f"{horizon[:-1]}-hour ramps, monthly P99 and |P1| (thick: 12-month mean)", color=INK,
                     fontsize=10.5,
                     loc="left")
        ax.set_ylim(bottom=0)
        _legend(ax, loc="upper left")
    return _save(fig, path)


def fig_evening(r: Analysis, path: Path) -> Path:
    fig, (a1, a2) = _figure(2, height=4.0)
    m = r.monthly[r.monthly["coverage"] >= 0.9]
    x = _months(m.index)
    a1.plot(x, m["evening3h_mean"] / 1000, color=SERIES[0], linewidth=2.0, label="mean")
    a1.plot(x, m["evening3h_max"] / 1000, color=SERIES[1], linewidth=1.4, label="largest day")
    _style(a1, "GW")
    a1.set_ylim(bottom=0)
    a1.set_title("Steepest 3-hour rise before sunset, per day", color=INK, fontsize=10.5, loc="left")
    _legend(a1, loc="upper left")
    a = r.annual.loc[[str(y) for y in r.meta.get("full_years", [])]]
    a2.bar(a.index, a["lift_mean"] / 1000, color=SERIES[0], width=0.6, label="midday minimum to evening peak")
    for i, v in enumerate(a["lift_mean"] / 1000):
        a2.text(i, v, f"{v:.1f}", ha="center", va="bottom", color=INK2, fontsize=8.5)
    _style(a2, "GW", grid="y")
    a2.set_title("Evening lift, annual mean of daily values", color=INK, fontsize=10.5, loc="left")
    return _save(fig, path)


def fig_midday(r: Analysis, path: Path) -> Path:
    fig, (ax,) = _figure(height=4.2)
    m = r.monthly[r.monthly["coverage"] >= 0.9]
    x = _months(m.index)
    for col, colour, label in (("midday_min_mean", SERIES[0], "residual load (daily midday minimum)"),
                               ("conv_at_midday_min", SERIES[1], "conventional and other generation at that moment"),
                               ("export_at_midday_min", SERIES[2], "net export at that moment")):
        ax.plot(x, m[col] / 1000, color=colour, linewidth=2.0, label=label)
    ax.axhline(0, color=AXIS, linewidth=0.8)
    _style(ax, "GW, monthly mean")
    ax.set_title("Daily midday minimum: since 2024 conventional generation stays above the residual load",
                 color=INK, fontsize=11, loc="left")
    _legend(ax, loc="upper right")
    return _save(fig, path)


def fig_irre(r: Analysis, path: Path) -> Path:
    fig, (a1, a2) = _figure(2, height=4.0)
    c = r.irre_curve
    base = c[c["variant"] == "RTS-96 data"]
    for k, colour in zip(PV_SCALES, SERIES):
        part = base[base["pv_scale"] == k]
        a1.plot(part["horizon_h"], part["irre_pct"], color=colour, linewidth=2.0, marker="o", markersize=4,
                label=scenario_label(k))
    _style(a1, "% of upward ramps")
    a1.set_xlabel("horizon, h", color=INK2, fontsize=9)
    a1.set_title(f"IRRE of the RTS fleet, {r.base_year} net-load shape", color=INK, fontsize=10.5, loc="left")
    _legend(a1, loc="upper right")
    variants = ["RTS-96 data", "nuclear must-run", "RTS-96 data + storage"]
    h = 2.0
    width = 0.26
    for i, (k, colour) in enumerate(zip(PV_SCALES, SERIES)):
        vals = [float(c[(c["variant"] == v) & (c["pv_scale"] == k) & (c["horizon_h"] == h)]["irre_pct"].iloc[0])
                for v in variants]
        xs = np.arange(len(variants)) + (i - 1) * width
        a2.bar(xs, vals, width=width - 0.02, color=colour, label=scenario_label(k))
        for xx, v in zip(xs, vals):
            a2.text(xx, v, f"{v:.1f}", ha="center", va="bottom", color=INK2, fontsize=7.5)
    a2.set_xticks(range(len(variants)), ["RTS-96 data", "nuclear\nmust-run", "+ storage"])
    _style(a2, None, grid="y")
    a2.set_title(f"IRRE at the {h:.0f}-hour horizon by fleet variant", color=INK, fontsize=10.5, loc="left")
    _legend(a2, loc="upper left")
    return _save(fig, path)


def fig_storage(r: Analysis, path: Path) -> Path:
    fig, (a1, a2) = _figure(2, height=4.0)
    s = r.storage.reset_index()
    bands = list(dict.fromkeys(s["band"]))
    width = 0.26
    for ax, col, unit, div in ((a1, "power_max_mw", "GW", 1000), (a2, "energy_max_mwh", "GWh", 1000)):
        for i, (k, colour) in enumerate(zip(PV_SCALES, SERIES)):
            vals = [float(s[(s["band"] == b) & (s["pv_scale"] == k)][col].iloc[0]) / div for b in bands]
            xs = np.arange(len(bands)) + (i - 1) * width
            ax.bar(xs, vals, width=width - 0.02, color=colour, label=scenario_label(k))
            for xx, v in zip(xs, vals):
                ax.text(xx, v, f"{v:.1f}", ha="center", va="bottom", color=INK2, fontsize=8)
        ax.set_xticks(range(len(bands)), [f"keep ramps in {b}" for b in bands])
        _style(ax, unit, grid="y")
    a1.set_title(f"Storage power needed on the worst day of {r.base_year}", color=INK, fontsize=10.5, loc="left")
    a2.set_title("Storage energy needed on the worst day", color=INK, fontsize=10.5, loc="left")
    _legend(a1, loc="upper left")
    return _save(fig, path)


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)
    return path


def save_all(r: Analysis, img_dir: Path) -> list[Path]:
    return [
        fig_duck(r, img_dir / "duck_curve.png"),
        fig_duration(r, img_dir / "duration_curves.png"),
        fig_distributions(r, img_dir / "ramp_distributions.png"),
        fig_trend(r, img_dir / "ramp_trend.png"),
        fig_evening(r, img_dir / "evening_ramp.png"),
        fig_midday(r, img_dir / "midday_minimum.png"),
        fig_irre(r, img_dir / "irre.png"),
        fig_storage(r, img_dir / "storage.png"),
    ]
