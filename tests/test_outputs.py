import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_frame
from flex_ramps import analysis, cli
from flex_ramps.dashboard import write_dashboard


@pytest.fixture
def result(frames, monkeypatch):
    monkeypatch.setattr(analysis, "IRRE_HORIZONS", [0.25, 1.0, 2.0, 3.0])
    q, h = frames
    return analysis.analyse(q, h, base_year=2025, meta={"history_start": "2023-01"})


def test_analysis_tables(result):
    a = result.annual
    assert list(a.index) == ["2023", "2024", "2025"]
    assert result.meta["full_years"] == [2023, 2024]
    assert a.loc["2023", "coverage"] == pytest.approx(1.0)
    assert np.isnan(a.loc["2023", "q_ramp15min_p99"]) and not np.isnan(a.loc["2025", "q_ramp15min_p99"])
    sc = result.scenarios
    assert list(sc.index) == [1.0, 1.2, 1.5]
    assert sc.loc[1.5, "midday_min_mean"] < sc.loc[1.0, "midday_min_mean"]   # more PV deepens the trough
    st = result.storage
    assert set(st.index.get_level_values("band")) == {"P5-P95", "P1-P99"}
    assert (st["power_max_mw"] >= 0).all()
    ic = result.irre_curve
    assert set(ic["variant"]) == {"RTS-96 data", "nuclear must-run", "RTS-96 data + storage"}
    assert ((ic["irre_pct"] >= 0) & (ic["irre_pct"] <= 100)).all()
    assert list(result.irre_years.index) == ["2023", "2024"]
    assert result.trends["pv_share"]["n"] == 2


def test_summary_is_json(result):
    text = json.dumps(result.summary(), allow_nan=False)
    assert "base_year_stats" in text


def test_report_and_dashboard(result, tmp_path):
    cli.write_report(result, tmp_path / "results", tmp_path / "img")
    assert (tmp_path / "results" / "summary.json").exists()
    assert len(list((tmp_path / "img").glob("*.png"))) == 8
    page = write_dashboard(result, tmp_path / "docs").read_text(encoding="utf-8")
    for fig_id in ("duck", "duration", "hist_1h", "trend_3h", "profile", "midday", "irre", "storage_energy"):
        assert f'"{fig_id}"' in page
    assert "NaN" not in page and "%%" not in page
    assert (tmp_path / "docs" / "annual.csv").exists()


def test_cli_offline_end_to_end(tmp_path):
    """Synthetic cache files in the layout of the real cache; no network is touched (--offline)."""
    api_dir, legacy_dir = tmp_path / "data" / "api", tmp_path / "data" / "legacy"
    api_dir.mkdir(parents=True)
    legacy_dir.mkdir(parents=True)
    old = synthetic_frame("2024-06-01", "2024-06-14", freq="1h")
    pd.DataFrame({"demand": old["demand"], "jwcd": old["conv"], "njwcd": old["pv"] + old["wind"],
                  "swm_p": old["net_import"], "swm_np": 0.0}).to_parquet(legacy_dir / "PL_WYK_KSE_2024-06.parquet")
    old[["wind", "pv"]].to_parquet(legacy_dir / "PL_GEN_WIATR_2024-06.parquet")
    new = synthetic_frame("2024-06-14", "2024-09-01")
    api = pd.DataFrame({"demand": new["demand"], "pv": new["pv"], "wind": new["wind"],
                        "jg": new["conv"], "jnwrb": new["pv"] + new["wind"], "swm_p": new["net_import"],
                        "swm_np": 0.0})
    api["business_date"] = new.index.tz_convert("Europe/Warsaw").date
    for label, part in api.groupby(new.index.tz_convert("Europe/Warsaw").strftime("%Y-%m")):
        part.to_parquet(api_dir / f"mix_{label}.parquet")
    args = ["--data-dir", str(tmp_path / "data"), "--offline", "--today", "2024-08-31", "--legacy-start",
            "2024-06-01", "--base-year", "2024"]
    assert cli.main(["report", *args, "--results", str(tmp_path / "results"), "--no-figures"]) == 0
    summary = json.loads((tmp_path / "results" / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["history_start"] == "2024-06"
    assert cli.main(["dashboard", *args, "--out", str(tmp_path / "docs")]) == 0
    assert (tmp_path / "docs" / "index.html").exists()
    # a missing legacy month stops the run unless gaps are allowed
    (legacy_dir / "PL_GEN_WIATR_2024-06.parquet").unlink()
    assert cli.main(["report", *args, "--results", str(tmp_path / "r2"), "--no-figures"]) == 2
