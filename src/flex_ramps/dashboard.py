"""Static HTML dashboard (Plotly.js from the official CDN) for GitHub Pages.

Figures are plain Plotly JSON with colour tokens ("@s1", "@y3", ...) that the page resolves for the light or dark
theme, so one file follows the reader's colour scheme.

Usage:
    write_dashboard(result, Path("docs"))
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from flex_ramps.analysis import PV_SCALES, Analysis
from flex_ramps.context import PSE_FNA_TITLE, PSE_FNA_URL
from flex_ramps.figures import highlight_years, scenario_label

PLOTLY_JS = "https://cdn.plot.ly/plotly-2.35.2.min.js"
REPO_URL = "https://github.com/kwwaleszczynski-del/pl-flexibility-ramps"
SERIES = ["@s1", "@s2", "@s3"]
RAMP_TOKENS = {"#86b6ef": "@y0", "#3987e5": "@y1", "#1c5cab": "@y2", "#0d366b": "@y3", "#c3c2b7": "@old"}
HOUR_AXIS = {"tickvals": list(range(0, 24, 3)), "ticktext": [f"{h:02d}:00" for h in range(0, 24, 3)]}


def _num(values, nd: int = 1) -> list:
    out = []
    for v in values:
        if v is None or (isinstance(v, (float, np.floating)) and math.isnan(v)):
            out.append(None)
        else:
            out.append(round(float(v), nd))
    return out


def _months(index) -> list[str]:
    return [f"{m}-01" for m in index]


def _gw(values, nd: int = 2) -> list:
    return _num(np.asarray(values, dtype=float) / 1000.0, nd)


def _line(x, y, name, colour, width=2, dash=None, **kw) -> dict:
    line = {"color": colour, "width": width}
    if dash:
        line["dash"] = dash
    return {"type": "scatter", "mode": "lines", "x": x, "y": y, "name": name, "line": line, **kw}


def figures(r: Analysis) -> dict[str, dict]:
    figs: dict[str, dict] = {}
    by = str(r.base_year)

    duck = r.duck
    colours = highlight_years([str(y) for y in duck.index])
    figs["duck"] = {"data": [
        _line(list(duck.columns), _gw(row.to_numpy()), str(y), RAMP_TOKENS[colours[str(y)]],
              width=2 if colours[str(y)] != "#c3c2b7" else 1.2, hovertemplate="%{y:.1f} GW")
        for y, row in duck.iterrows()
    ], "layout": {"yaxis": {"title": {"text": "GW"}}, "xaxis": HOUR_AXIS, "hovermode": "x unified"}}

    d = r.duration
    full = [str(y) for y in r.meta.get("full_years", [])]
    colours = highlight_years(full)
    figs["duration"] = {"data": [
        _line(_num(d.index, 1), _gw(d[y]), y, RAMP_TOKENS[colours[y]], width=2 if colours[y] != "#c3c2b7" else 1.2,
              hovertemplate="%{y:.1f} GW, exceeded %{x:.1f}% of hours")
        for y in full
    ], "layout": {"yaxis": {"title": {"text": "GW"}}, "xaxis": {"title": {"text": "% of hours exceeded"}}}}
    cols = [by] + [c for c in d.columns if c.startswith(f"{by} PV")]
    figs["duration_pv"] = {"data": [
        _line(_num(d.index, 1), _gw(d[c]), c if c != by else f"{by} as built", tok,
              hovertemplate="%{y:.1f} GW, exceeded %{x:.1f}% of hours")
        for c, tok in zip(cols, SERIES)
    ], "layout": {"yaxis": {"title": {"text": "GW"}}, "xaxis": {"title": {"text": "% of hours exceeded"}}}}

    h = r.histograms
    for horizon in ("15min", "1h", "3h"):
        names = [by, f"{by} PV +50%"] if horizon == "15min" else [f"{by} {horizon}", f"{by} PV +50% {horizon}"]
        data = []
        for name, tok, label in zip(names, (SERIES[0], SERIES[2]), (f"{by} as built", "PV +50%")):
            part = h[(h["series"] == name) & (h["horizon"] == horizon)]
            data.append({"type": "scatter", "mode": "lines", "line": {"shape": "hvh", "color": tok, "width": 1.6},
                         "x": _gw(part["center"]), "y": _num(100 * part["share"], 3), "name": label,
                         "hovertemplate": "%{x:.2f} GW: %{y:.2f}% of intervals"})
        figs[f"hist_{horizon}"] = {"data": data, "layout": {
            "xaxis": {"title": {"text": f"{horizon} change, GW"}}, "yaxis": {"title": {"text": "% of intervals"}}}}

    m = r.monthly[r.monthly["coverage"] >= 0.9]
    x = _months(m.index)
    for horizon in ("1h", "3h"):
        up, down = m[f"ramp{horizon}_p99"], -m[f"ramp{horizon}_p01"]
        figs[f"trend_{horizon}"] = {"data": [
            _line(x, _gw(up), "upward P99", SERIES[0], width=1.2, opacity=0.55, hovertemplate="%{y:.2f} GW"),
            _line(x, _gw(down), "downward |P1|", SERIES[1], width=1.2, opacity=0.55, hovertemplate="%{y:.2f} GW"),
            _line(x, _gw(up.rolling(12, min_periods=12).mean()), "upward, 12-month mean", SERIES[0], width=2.5,
                  hovertemplate="%{y:.2f} GW"),
            _line(x, _gw(down.rolling(12, min_periods=12).mean()), "downward, 12-month mean", SERIES[1], width=2.5,
                  hovertemplate="%{y:.2f} GW"),
        ], "layout": {"yaxis": {"title": {"text": "GW"}, "rangemode": "tozero"}, "hovermode": "x unified"}}

    p = r.profile
    if not p.empty:
        z = p.to_numpy() / 1000.0
        lim = float(np.nanpercentile(np.abs(z), 99))
        figs["profile"] = {"data": [{
            "type": "heatmap", "z": [_num(row, 2) for row in z], "x": list(p.columns), "y": list(p.index),
            "colorscale": "@div", "zmid": 0, "zmin": -lim, "zmax": lim,
            "colorbar": {"title": {"text": "GW/h"}, "thickness": 12},
            "hovertemplate": "%{y} %{x}: %{z:.2f} GW in the next hour<extra></extra>"}],
            "layout": {"yaxis": {"autorange": "reversed", "type": "category"},
                       "xaxis": {"type": "category", "tickvals": [f"{hh:02d}:00" for hh in range(0, 24, 3)]}}}

    figs["evening"] = {"data": [
        _line(x, _gw(m["evening3h_mean"]), "mean of days", SERIES[0], hovertemplate="%{y:.2f} GW"),
        _line(x, _gw(m["evening3h_max"]), "largest day", SERIES[1], width=1.5, hovertemplate="%{y:.2f} GW"),
        _line(x, _gw(m["lift_mean"]), "midday minimum to evening peak (mean)", SERIES[2], width=1.5,
              hovertemplate="%{y:.2f} GW"),
    ], "layout": {"yaxis": {"title": {"text": "GW"}, "rangemode": "tozero"}, "hovermode": "x unified"}}

    figs["midday"] = {"data": [
        _line(x, _gw(m["midday_min_mean"]), "residual load at the daily midday minimum", SERIES[0],
              hovertemplate="%{y:.1f} GW"),
        _line(x, _gw(m["conv_at_midday_min"]), "conventional and other generation then", SERIES[1],
              hovertemplate="%{y:.1f} GW"),
        _line(x, _gw(m["export_at_midday_min"]), "net export then", SERIES[2], hovertemplate="%{y:.1f} GW"),
    ], "layout": {"yaxis": {"title": {"text": "GW, monthly mean"}}, "hovermode": "x unified"}}
    figs["midday_days"] = {"data": [
        {"type": "bar", "x": x, "y": _num(m["days_below_5gw"], 0), "name": "days with a midday minimum below 5 GW",
         "marker": {"color": SERIES[0]}, "hovertemplate": "%{y} days below 5 GW<extra></extra>"},
        {"type": "bar", "x": x, "y": _num(m["days_below_3gw"], 0), "name": "below 3 GW",
         "marker": {"color": SERIES[1]}, "hovertemplate": "%{y} days below 3 GW<extra></extra>"},
    ], "layout": {"yaxis": {"title": {"text": "days"}}, "barmode": "group", "bargap": 0.2}}

    c = r.irre_curve
    base = c[c["variant"] == "RTS-96 data"]
    figs["irre"] = {"data": [
        {"type": "scatter", "mode": "lines+markers", "x": _num(part["horizon_h"], 2), "y": _num(part["irre_pct"], 2),
         "name": scenario_label(k), "line": {"color": tok, "width": 2}, "marker": {"size": 8, "color": tok},
         "hovertemplate": "%{x} h: %{y:.2f}% of upward ramps"}
        for k, tok in zip(PV_SCALES, SERIES) for part in [base[base["pv_scale"] == k]]
    ], "layout": {"xaxis": {"title": {"text": "horizon, h"}}, "yaxis": {"title": {"text": "% of upward ramps"}}}}
    variants = ["RTS-96 data", "nuclear must-run", "RTS-96 data + storage"]
    short = ["RTS-96", "nuclear must-run", "+ storage"]
    figs["irre_variants"] = {"data": [
        {"type": "bar", "x": short, "name": scenario_label(k), "marker": {"color": tok},
         "y": [_num(c[(c["variant"] == v) & (c["pv_scale"] == k) & (c["horizon_h"] == 2.0)]["irre_pct"], 2)[0]
               for v in variants],
         "hovertemplate": "%{x}: %{y:.2f}%<extra>" + scenario_label(k) + "</extra>"}
        for k, tok in zip(PV_SCALES, SERIES)
    ], "layout": {"barmode": "group", "bargap": 0.3, "xaxis": {"tickangle": 0},
                  "yaxis": {"title": {"text": "% of upward 2-hour ramps"}}}}
    iy = r.irre_years
    if not iy.empty:
        figs["irre_years"] = {"data": [
            {"type": "scatter", "mode": "lines+markers", "x": list(iy.index), "y": _num(iy[f"irre_pct_{hh}h"], 2),
             "name": f"{hh}-hour horizon", "line": {"color": tok, "width": 2}, "marker": {"size": 8, "color": tok},
             "hovertemplate": "%{x}: %{y:.2f}%"}
            for hh, tok in ((1, SERIES[2]), (2, SERIES[0]), (3, SERIES[1]))
        ], "layout": {"xaxis": {"type": "category"}, "yaxis": {"title": {"text": "% of upward ramps"}}}}
    dh = r.deficit_hours
    figs["deficit_hours"] = {"data": [
        {"type": "bar", "x": list(range(24)), "y": _num(part["deficits"], 0), "name": f"{scenario_label(k)}, 2 h",
         "marker": {"color": tok}, "hovertemplate": "%{x}:00: %{y} ramps<extra></extra>"}
        for k, tok in ((1.0, SERIES[0]), (1.5, SERIES[2]))
        for part in [dh[(dh["pv_scale"] == k) & (dh["horizon_h"] == 2.0)]]
    ], "layout": {"barmode": "group", "bargap": 0.15,
                  "xaxis": {**HOUR_AXIS, "tickangle": 0, "title": {"text": "local start hour"}},
                  "yaxis": {"title": {"text": "ramps short of flexibility"}}}}

    s = r.storage.reset_index()
    bands = list(dict.fromkeys(s["band"]))
    for key, col, unit in (("storage_power", "power_max_mw", "GW"), ("storage_energy", "energy_max_mwh", "GWh")):
        figs[key] = {"data": [
            {"type": "bar", "x": [f"ramps in {b}" for b in bands], "name": scenario_label(k), "marker": {"color": tok},
             "y": [_gw(s[(s["band"] == b) & (s["pv_scale"] == k)][col])[0] for b in bands],
             "text": [f"{v:.1f}" for v in [float(s[(s["band"] == b) & (s["pv_scale"] == k)][col].iloc[0]) / 1000
                                           for b in bands]],
             "textposition": "outside", "hovertemplate": "%{x}: %{y:.2f} " + unit + "<extra>" + scenario_label(k)
                                                          + "</extra>"}
            for k, tok in zip(PV_SCALES, SERIES)
        ], "layout": {"barmode": "group", "bargap": 0.3, "yaxis": {"title": {"text": unit}}}}
    return figs


def _fmt(v, spec: str, suffix: str = "") -> str:
    if v is None or (isinstance(v, (float, np.floating)) and math.isnan(v)):
        return "n/a"
    return f"{v:{spec}}{suffix}"


def _tile(label: str, value: str, sub: str) -> str:
    return (f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div>'
            f'<div class="sub">{sub}</div></div>')


def kpi_tiles(r: Analysis) -> str:
    y = str(r.base_year)
    a = r.annual
    if y not in a.index:
        return ""
    row = a.loc[y]
    sc, st, c = r.scenarios, r.storage, r.irre_curve
    irre2 = c[(c["variant"] == "RTS-96 data") & (c["horizon_h"] == 2.0)].set_index("pv_scale")["irre_pct"]
    cells = [
        _tile("Largest 1-hour rise", _fmt(row["q_ramp1h_max"] / 1000, ".1f", " GW"),
              "P99 " + _fmt(row["q_ramp1h_p99"] / 1000, ".1f", " GW") + ", 15-minute data"),
        _tile("Largest 3-hour rise", _fmt(row["q_ramp3h_max"] / 1000, ".1f", " GW"),
              "P99 " + _fmt(row["q_ramp3h_p99"] / 1000, ".1f", " GW")),
        _tile("Evening lift", _fmt(row["lift_mean"] / 1000, ".1f", " GW"),
              "mean daily rise from the midday minimum to the evening peak"),
        _tile("Lowest midday residual load", _fmt(row["midday_min_min"] / 1000, ".1f", " GW"),
              _fmt(row["days_below_5gw"], ".0f") + " days below 5 GW"),
    ]
    p99 = st.loc["P1-P99"]
    cells2 = [
        _tile("Storage to keep ramps in P1-P99", _fmt(p99.at[1.0, "power_max_mw"] / 1000, ".1f", " GW"),
              _fmt(p99.at[1.0, "energy_max_mwh"] / 1000, ".1f", " GWh on the worst day")),
        _tile("Same with PV +50%", _fmt(p99.at[1.5, "power_max_mw"] / 1000, ".1f", " GW"),
              _fmt(p99.at[1.5, "energy_max_mwh"] / 1000, ".1f", " GWh")),
        _tile("IRRE, 2-hour horizon", _fmt(irre2.get(1.0), ".1f", "%"),
              "of upward ramps, RTS fleet; PV +50%: " + _fmt(irre2.get(1.5), ".1f", "%")),
        _tile("Hourly ramp P99 vs PSE 2030", _fmt(row["ramp1h_p99"] / 1000, ".1f", " GW"),
              "PSE assessment for 2030: " + _fmt(r.context.at["p99", "pse_fna_2030"] / 1000, ".1f", " GW")),
    ]
    return (f'<div class="tile-group"><h3>{y} <span>residual load = demand &minus; PV &minus; wind</span></h3>'
            f'<div class="tiles">{"".join(cells)}</div></div>'
            f'<div class="tile-group"><h3>How much flexibility <span>{y} profiles, static scaling</span></h3>'
            f'<div class="tiles">{"".join(cells2)}</div></div>')


TABLE_COLS = {
    "coverage": ("Coverage", ".0%"), "pv_share": ("PV share of demand", ".1%"),
    "wind_share": ("Wind share", ".1%"), "rl_min": ("RL min MW", ".0f"), "rl_max": ("RL max MW", ".0f"),
    "ramp1h_p01": ("1 h P1 MW", ".0f"), "ramp1h_p99": ("1 h P99 MW", ".0f"), "ramp1h_max": ("1 h max MW", ".0f"),
    "ramp3h_p01": ("3 h P1 MW", ".0f"), "ramp3h_p99": ("3 h P99 MW", ".0f"),
    "q_ramp15min_p99": ("15 min P99 MW", ".0f"), "evening3h_mean": ("Evening 3 h mean MW", ".0f"),
    "lift_mean": ("Evening lift MW", ".0f"), "midday_min_mean": ("Midday min mean MW", ".0f"),
    "days_below_5gw": ("Days < 5 GW", ".0f"), "conv_at_midday_min": ("Conv. at midday min MW", ".0f"),
    "export_at_midday_min": ("Export at midday min MW", ".0f"),
}


def table_html(frame: pd.DataFrame, first: str, cols: dict = TABLE_COLS) -> str:
    head = "".join(f"<th>{html.escape(label)}</th>" for label, _ in cols.values())
    rows = []
    for key, row in frame.iterrows():
        cells = "".join(f"<td>{_fmt(row.get(c), spec)}</td>" for c, (_, spec) in cols.items())
        rows.append(f"<tr><th>{html.escape(str(key))}</th>{cells}</tr>")
    return f'<table><thead><tr><th>{first}</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def context_html(r: Analysis) -> str:
    t = r.context
    labels = {"pse_fna_2030": "PSE FNA 2030", "pse_fna_2035": "PSE FNA 2035"}
    cols = {c: (labels.get(c, c.replace("_", " ")), ",.0f") for c in t.columns}
    names = {"min": "minimum", "p01": "P1", "p05": "P5", "p95": "P95", "p99": "P99", "max": "maximum"}
    frame = t.rename(index=names)
    return table_html(frame, "MW/h", cols)


def write_dashboard(r: Analysis, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = r.meta
    status = (f"hourly history from {meta.get('history_start', 'n/a')}, 15-minute data "
              f"{meta.get('api_first_local', 'n/a')} to {meta.get('api_last_local_end', 'n/a')}")
    page = TEMPLATE
    replacements = {
        "%%PLOTLY%%": PLOTLY_JS,
        "%%REPO%%": REPO_URL,
        "%%YEAR%%": str(r.base_year),
        "%%KPIS%%": kpi_tiles(r),
        "%%CONTEXT%%": context_html(r),
        "%%PSE_URL%%": PSE_FNA_URL,
        "%%PSE_TITLE%%": html.escape(PSE_FNA_TITLE),
        "%%ANNUAL%%": table_html(r.annual, "Year"),
        "%%MONTHLY%%": table_html(r.monthly.iloc[::-1], "Month"),
        "%%STATUS%%": html.escape(status),
        "%%GENERATED%%": html.escape(str(meta.get("generated_utc", ""))),
        "%%DOWNLOADED%%": html.escape(str(meta.get("data_downloaded", ""))),
        "%%FIGURES%%": json.dumps(figures(r), separators=(",", ":")).replace("</", "<\\/"),
    }
    for key, value in replacements.items():
        page = page.replace(key, value)
    path = out_dir / "index.html"
    path.write_text(page, encoding="utf-8")
    r.annual.round(4).to_csv(out_dir / "annual.csv")
    r.monthly.round(4).to_csv(out_dir / "monthly.csv")
    r.storage.round(3).to_csv(out_dir / "storage.csv")
    r.irre_curve.round(4).to_csv(out_dir / "irre_by_horizon.csv", index=False)
    return path


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Poland Flexibility Ramps</title>
<meta name="description" content="Residual-load ramps, the evening ramp, midday minima and flexibility needs of the Polish power system on PSE open data.">
<script src="%%PLOTLY%%" charset="utf-8"></script>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --border: rgba(11,11,11,0.10); --accent: #2a78d6;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --border: rgba(255,255,255,0.10); --accent: #3987e5;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --border: rgba(255,255,255,0.10); --accent: #3987e5;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 0 0 4px; }
h3 { font-size: 15px; margin: 0 0 8px; }
h3 span { color: var(--muted); font-weight: 400; }
p { margin: 4px 0 12px; color: var(--ink2); }
a { color: var(--accent); }
.lead { max-width: 840px; }
.meta { font-size: 13px; color: var(--muted); }
.tile-group { margin: 20px 0 8px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tile .label { font-size: 13px; color: var(--ink2); }
.tile .value { font-size: 28px; font-weight: 600; margin: 2px 0; }
.tile .sub { font-size: 12px; color: var(--muted); }
.section { margin-top: 28px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(460px, 1fr)); gap: 16px; margin-top: 12px; }
.grid3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-top: 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 14px 6px;
  min-width: 0; }
.card.wide { grid-column: 1 / -1; }
.chart { width: 100%; height: 340px; }
.note { font-size: 13px; color: var(--muted); }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; width: 100%; }
th, td { padding: 4px 8px; border-bottom: 1px solid var(--grid); text-align: right; white-space: nowrap; }
thead th { color: var(--ink2); font-weight: 600; }
tbody th { text-align: left; font-weight: 600; }
details summary { cursor: pointer; color: var(--accent); margin: 8px 0; }
footer { margin-top: 32px; font-size: 13px; color: var(--muted); }
@media (max-width: 520px) { .grid, .grid3 { grid-template-columns: 1fr; } .chart { height: 300px; } }
</style>
</head>
<body>
<main>
<h1>How much flexibility does the Polish power system need?</h1>
<p class="lead">Ramps of the residual load (demand minus PV and wind) in Poland from public PSE data: hourly since
2020 and every 15 minutes since 14 June 2024. With more solar, the midday trough deepens and the evening rise
steepens; the charts measure both, test a stylised IEEE RTS fleet against the ramps (IRRE) and size the storage that
would keep the ramps inside their usual range.</p>
<p class="meta">Generated %%GENERATED%% UTC. Data: %%STATUS%%. <a href="%%REPO%%">Code and method</a></p>
%%KPIS%%

<div class="section"><h2>The duck takes shape</h2>
<p>Mean residual load by hour of day in May-August of each year (Polish local time) and residual-load duration
curves.</p></div>
<div class="grid">
<section class="card wide"><h3>Residual load by hour of day, May-August</h3><div id="duck" class="chart" style="height:380px"></div></section>
<section class="card"><h3>Duration curves, full years</h3><p class="note">Hourly values sorted from highest to lowest.</p>
<div id="duration" class="chart"></div></section>
<section class="card"><h3>%%YEAR%% with more PV</h3><p class="note">PV profile scaled by 1.2 and 1.5; demand and wind
unchanged.</p><div id="duration_pv" class="chart"></div></section>
</div>

<div class="section"><h2>Ramps</h2>
<p>Distribution of residual-load changes over 15 minutes, 1 hour and 3 hours in %%YEAR%% (15-minute data), as built
and with 50% more PV.</p></div>
<div class="grid3">
<section class="card"><h3>15 minutes</h3><div id="hist_15min" class="chart" style="height:300px"></div></section>
<section class="card"><h3>1 hour</h3><div id="hist_1h" class="chart" style="height:300px"></div></section>
<section class="card"><h3>3 hours</h3><div id="hist_3h" class="chart" style="height:300px"></div></section>
</div>
<div class="grid">
<section class="card"><h3>1-hour ramps by month</h3><p class="note">Monthly P99 of rises and |P1| of falls, hourly
data; thick lines are 12-month means.</p><div id="trend_1h" class="chart"></div></section>
<section class="card"><h3>3-hour ramps by month</h3><p class="note">Same for 3-hour changes.</p>
<div id="trend_3h" class="chart"></div></section>
<section class="card wide"><h3>When the system ramps</h3><p class="note">Mean change of the residual load over the
next hour by month and local start time (15-minute data). Red: rising, blue: falling.</p>
<div id="profile" class="chart" style="height:460px"></div></section>
</div>

<div class="section"><h2>Evening ramp and midday minimum</h2>
<p>The evening ramp is the steepest 3-hour rise starting in the four hours before sunset (sunset computed for the
centre of Poland); the lift is the rise from the day's midday minimum (10:00-16:00) to the evening peak.</p></div>
<div class="grid">
<section class="card"><h3>Evening ramp by month</h3><div id="evening" class="chart"></div></section>
<section class="card"><h3>What happens at the midday minimum</h3><p class="note">At the moment of each day's lowest
midday residual load. Until 2023 conventional generation followed the residual load down; since 2024 it stays above
it and the difference is exported.</p>
<div id="midday" class="chart"></div></section>
<section class="card wide"><h3>Days with a deep midday trough</h3><div id="midday_days" class="chart" style="height:280px"></div></section>
</div>

<div class="section"><h2>Flexibility metric: IRRE on a test system</h2>
<p>The insufficient ramping resource expectation (Lannoye, Flynn and O'Malley, 2012) counts the net-load ramps that
exceed the upward flexibility a fleet can offer within the ramp's horizon. The fleet is the IEEE RTS-79 generating
system (32 units, 3405 MW) with RTS-96 ramp rates, minimum stable outputs and start-up times, dispatched in merit
order against the %%YEAR%% Polish net-load shape scaled to the RTS peak of 2850 MW. A stylised exercise: it shows at
which horizons a conventional fleet runs short, not the state of the Polish system.</p></div>
<div class="grid">
<section class="card"><h3>IRRE by horizon</h3><p class="note">Share of upward ramps without enough flexibility,
weighted over the all-available and single-outage states.</p><div id="irre" class="chart"></div></section>
<section class="card"><h3>Fleet variants, 2-hour horizon</h3><p class="note">Nuclear must-run: nuclear units fixed at
full output. Storage: the power from the storage sizing below, scaled to the RTS system.</p>
<div id="irre_variants" class="chart"></div></section>
<section class="card"><h3>IRRE with each year's net-load shape</h3><p class="note">Hourly data, RTS-96 data fleet.</p>
<div id="irre_years" class="chart"></div></section>
<section class="card"><h3>When the deficits happen</h3><p class="note">Upward 2-hour ramps without enough
flexibility by local start hour (all units available).</p><div id="deficit_hours" class="chart"></div></section>
</div>

<div class="section"><h2>How much storage?</h2>
<p>For each day of %%YEAR%% a linear programme finds the smallest storage power, and then energy (85% round trip,
back to its starting charge every day), that keeps all 15-minute, 1-hour and 3-hour ramps of the residual load inside
the P5-P95 or P1-P99 range of %%YEAR%%. Bars show the worst day; the band is kept fixed when PV grows.</p></div>
<div class="grid">
<section class="card"><h3>Power</h3><div id="storage_power" class="chart"></div></section>
<section class="card"><h3>Energy</h3><div id="storage_energy" class="chart"></div></section>
</div>

<section class="card wide section"><h2>Context: the PSE flexibility needs assessment</h2>
<p class="note">Hourly change of the residual load in the PSE assessment for 2030 and 2035 (<a href="%%PSE_URL%%">%%PSE_TITLE%%</a>,
Tab. 16, ERAA 2024 data, 540 simulated years) against the observed %%YEAR%% values. PSE's residual load subtracts all
non-dispatchable generation, not only PV and wind, so the comparison is indicative. Values copied from the report;
check against the current version before reuse.</p>
<div class="table-wrap">%%CONTEXT%%</div></section>

<section class="card wide section"><h2>Annual figures</h2>
<p class="note">Hourly data; 15-minute statistics from 14 June 2024. Coverage is the share of the year's hours with
data. Download: <a href="annual.csv">annual.csv</a>, <a href="monthly.csv">monthly.csv</a>,
<a href="storage.csv">storage.csv</a>, <a href="irre_by_horizon.csv">irre_by_horizon.csv</a>.</p>
<div class="table-wrap">%%ANNUAL%%</div>
<details><summary>Monthly figures</summary><div class="table-wrap">%%MONTHLY%%</div></details></section>
<footer>
<p>Source: information obtained from www.pse.pl (PSE S.A. public reports: api.raporty.pse.pl report his-wlk-cal and
the legacy CSV export reports PL_WYK_KSE and PL_GEN_WIATR), state as of %%DOWNLOADED%%. The information has been
processed in whole or in part (residual load computed, aggregated, combined with a test system and models); it is not
an official PSE publication. Market and grid rules change and need to be checked against current sources.</p>
</footer>
</main>
<script>
const FIGS = %%FIGURES%%;
const THEMES = {
  light: { surface: "#fcfcfb", ink: "#0b0b0b", ink2: "#52514e", muted: "#898781", grid: "#e1e0d9", axis: "#c3c2b7",
    s1: "#2a78d6", s2: "#eb6834", s3: "#1baf7a", old: "#c3c2b7",
    y0: "#86b6ef", y1: "#3987e5", y2: "#1c5cab", y3: "#0d366b",
    div: [[0, "#104281"], [0.25, "#5598e7"], [0.5, "#f0efec"], [0.75, "#ec8a8a"], [1, "#c23838"]] },
  dark: { surface: "#1a1a19", ink: "#f0efec", ink2: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835",
    s1: "#3987e5", s2: "#d95926", s3: "#199e70", old: "#4a4a47",
    y0: "#184f95", y1: "#2a78d6", y2: "#6da7ec", y3: "#b7d3f6",
    div: [[0, "#6da7ec"], [0.25, "#256abf"], [0.5, "#383835"], [0.75, "#c24a4a"], [1, "#e66767"]] },
};
function themeName() {
  const forced = document.documentElement.dataset.theme;
  if (forced === "light" || forced === "dark") return forced;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function resolve(value, t) {
  if (typeof value === "string" && value.startsWith("@") && value.slice(1) in t) return t[value.slice(1)];
  if (Array.isArray(value)) return value.map(v => resolve(v, t));
  if (value && typeof value === "object") {
    const out = {};
    for (const k in value) out[k] = resolve(value[k], t);
    return out;
  }
  return value;
}
function axis(t, extra) {
  return Object.assign({ gridcolor: t.grid, linecolor: t.axis, zerolinecolor: t.axis, tickfont: { color: t.muted },
    title: { font: { color: t.ink2, size: 12 } }, automargin: true }, extra || {});
}
function render() {
  if (!window.Plotly) return;
  const t = THEMES[themeName()];
  for (const id in FIGS) {
    const el = document.getElementById(id);
    if (!el) continue;
    const fig = resolve(FIGS[id], t);
    const lay = fig.layout || {};
    const layout = Object.assign({}, lay, {
      paper_bgcolor: t.surface, plot_bgcolor: t.surface,
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: t.ink, size: 12 },
      margin: { l: 56, r: 16, t: 8, b: 40 },
      legend: { orientation: "h", y: -0.2, font: { color: t.ink2 } },
      hoverlabel: { bgcolor: t.surface, bordercolor: t.axis, font: { color: t.ink } },
      xaxis: axis(t, lay.xaxis), yaxis: axis(t, lay.yaxis),
    });
    Plotly.react(el, fig.data, layout, { responsive: true, displaylogo: false });
  }
}
render();
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
</script>
</body>
</html>
"""
