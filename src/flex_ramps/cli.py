"""Command line interface.

Usage:
    python -m flex_ramps fetch                          # download / refresh the PSE data cache only
    python -m flex_ramps report                         # tables in results/, README figures in docs/img/
    python -m flex_ramps dashboard --out docs           # static HTML dashboard for GitHub Pages
    python -m flex_ramps report --offline --today 2026-10-21   # rebuild from the cache with a fixed "today"
    python -m flex_ramps report --base-year 2025        # reference year of the 15-minute, IRRE and storage analysis

Data are read with pse-opendata and cached as monthly Parquet files in ./data (override with --data-dir or
FLEX_DATA_DIR): data/api for the reports API (a month is downloaded again until the 60-day revision window has
passed), data/legacy for the legacy CSV export (final, never refreshed).
Exit code 2 means that the API series, or months of the hourly history, were missing, so nothing was written.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from flex_ramps.analysis import Analysis, analyse
from flex_ramps.data import first_complete_month, hourly, quarter_hourly
from flex_ramps.pse_api import API_START, INTERVAL, load_mix
from flex_ramps.pse_legacy import load_legacy
from flex_ramps.ramps import TZ

log = logging.getLogger("flex_ramps")
LEGACY_START = date(2020, 1, 1)


def _date(s: str) -> date:
    return date.fromisoformat(s)


def load_data(args, today: date):
    api, api_status = load_mix(API_START, today + timedelta(days=1), args.data_dir, offline=args.offline)
    log.info("API: %d intervals, last %s, fetched %d, cached %d, stale %s, missing %s", len(api),
             api_status.last_interval, len(api_status.fetched), len(api_status.cached), api_status.stale,
             api_status.missing)
    legacy, legacy_status = load_legacy(args.legacy_start, args.data_dir, offline=args.offline,
                                        pause_s=args.legacy_pause)
    log.info("legacy: %d hours, fetched %d, cached %d, missing %s", len(legacy), len(legacy_status.fetched),
             len(legacy_status.cached), legacy_status.missing)
    return api, api_status, legacy, legacy_status


def _downloaded(args) -> str:
    """Date the PSE data were obtained: today, or the newest cache file when running offline."""
    files = list(args.data_dir.rglob("*.parquet"))
    if args.offline and files:
        return f"{datetime.fromtimestamp(max(f.stat().st_mtime for f in files)):%Y-%m-%d}"
    return f"{date.today():%Y-%m-%d}"


def run_analysis(args, today: date) -> Analysis | None:
    api, api_status, legacy, legacy_status = load_data(args, today)
    if api.empty:
        log.error("no data from the PSE API or the cache - nothing written")
        return None
    if legacy_status.missing and not args.allow_gaps:
        log.error("%d months of the legacy history are missing (%s ...) - nothing written; run again to resume, "
                  "or pass --allow-gaps", len(legacy_status.missing), legacy_status.missing[0])
        return None
    q = quarter_hourly(api)
    h = hourly(api, legacy)
    start = first_complete_month(h)
    if start is None:
        log.error("no month with complete demand, PV and wind data - nothing written")
        return None
    h = h[h.index >= pd.Timestamp(f"{start}-01").tz_localize(TZ).tz_convert("UTC")]
    base_year = args.base_year or today.year - 1
    meta = {
        "generated_utc": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        "data_downloaded": _downloaded(args),
        "history_start": start,
        "api_first_local": f"{q.index.min().tz_convert(TZ):%Y-%m-%d %H:%M}",
        "api_last_local_end": f"{(api_status.last_interval + INTERVAL).tz_convert(TZ):%Y-%m-%d %H:%M}",
        "api_stale_months": api_status.stale,
        "legacy_missing_months": legacy_status.missing,
    }
    log.info("analysing: hourly history from %s, base year %d", start, base_year)
    return analyse(q, h, base_year=base_year, meta=meta)


def write_report(result: Analysis, results_dir: Path, img_dir: Path | None) -> None:
    from flex_ramps.figures import save_all

    results_dir.mkdir(parents=True, exist_ok=True)
    result.annual.round(4).to_csv(results_dir / "annual.csv")
    result.monthly.round(4).to_csv(results_dir / "monthly.csv")
    result.scenarios.round(4).to_csv(results_dir / "scenarios.csv")
    result.irre_curve.round(4).to_csv(results_dir / "irre_by_horizon.csv", index=False)
    result.irre_years.round(4).to_csv(results_dir / "irre_by_year.csv")
    result.deficit_hours.to_csv(results_dir / "irre_deficits_by_hour.csv", index=False)
    result.storage.round(3).to_csv(results_dir / "storage.csv")
    result.storage_daily.round({"power_mw": 2, "energy_mwh": 2, "discharged_mwh": 2}).to_csv(
        results_dir / "storage_daily.csv", index=False)
    result.duration.round(1).to_csv(results_dir / "duration_curves.csv")
    result.duck.round(1).to_csv(results_dir / "summer_profile.csv")
    result.context.round(1).to_csv(results_dir / "context_pse_fna.csv")
    summary = result.summary()
    summary["meta"].pop("generated_utc", None)   # keep the committed summary stable between identical runs
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
    log.info("tables written to %s", results_dir)
    if img_dir is not None:
        for path in save_all(result, img_dir):
            log.info("figure %s", path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pl-flexibility-ramps", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "report", "dashboard"):
        p = sub.add_parser(name)
        p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("FLEX_DATA_DIR", "data")))
        p.add_argument("--today", type=_date, default=None, help="override today's date (Europe/Warsaw)")
        p.add_argument("--offline", action="store_true", help="use the cache only, no downloads")
        p.add_argument("--legacy-start", type=_date, default=LEGACY_START,
                       help="first date of the hourly history from the legacy export (default 2020-01-01)")
        p.add_argument("--legacy-pause", type=float, default=15.0,
                       help="seconds between legacy export requests (the server answers 429 to quick series)")
        p.add_argument("--allow-gaps", action="store_true", help="analyse even if legacy months are missing")
        p.add_argument("--base-year", type=int, default=None,
                       help="reference year for the 15-minute, IRRE and storage analysis (default: last year)")
        p.add_argument("-v", "--verbose", action="store_true")
        if name == "report":
            p.add_argument("--results", type=Path, default=Path("results"), help="folder for CSV/JSON tables")
            p.add_argument("--img", type=Path, default=Path("docs/img"), help="folder for README figures")
            p.add_argument("--no-figures", action="store_true")
        if name == "dashboard":
            p.add_argument("--out", type=Path, default=Path("docs"), help="output folder of the static page")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    today = args.today or pd.Timestamp.now(tz=TZ).date()

    if args.cmd == "fetch":
        _, _, _, legacy_status = load_data(args, today)
        return 2 if legacy_status.missing else 0
    result = run_analysis(args, today)
    if result is None:
        return 2
    if args.cmd == "report":
        write_report(result, args.results, None if args.no_figures else args.img)
    else:
        from flex_ramps.dashboard import write_dashboard

        path = write_dashboard(result, args.out)
        log.info("dashboard written to %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
