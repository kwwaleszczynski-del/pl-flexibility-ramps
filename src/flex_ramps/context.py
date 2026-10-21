"""Published assessments used for comparison (values copied from the documents; they need to be re-checked against
the current versions before reuse).

PSE S.A., "Sprawozdanie na temat szacowanych potrzeb w zakresie elastycznosci w odniesieniu do lat 2030 i 2035 dla
Krajowego Systemu Elektroenergetycznego" (national flexibility needs assessment under Art. 19e of Regulation (EU)
2019/943), published 29 July 2026, approved by the President of URE. Based on ERAA 2024 data, hourly resolution,
36 weather years x 15 forced-outage draws (540 Monte Carlo years).
"""
from __future__ import annotations

PSE_FNA_URL = "https://www.pse.pl/-/sprawozdanie-na-temat-szacowanych-potrzeb-w-zakresie-elastycznosci"
PSE_FNA_TITLE = ("PSE (2026), Sprawozdanie na temat szacowanych potrzeb w zakresie elastycznosci w odniesieniu do lat "
                 "2030 i 2035 dla KSE")

# Tab. 16: hourly change of the residual load (MW/h), all Monte Carlo hours
PSE_FNA_RAMPS = {
    "2030": {"min": -11616, "p01": -4056, "p05": -2624, "p50": -130, "p95": 3165, "p99": 4567, "max": 12351},
    "2035": {"min": -17804, "p01": -5371, "p05": -3421, "p50": -128, "p95": 3937, "p99": 5744, "max": 17602},
}

# Executive summary and Tab. 18
PSE_FNA_FINDINGS = {
    "uncovered_upward_ramping_gwh": {"2030": 6, "2035": 56},          # mean GWh per year
    "uncovered_upward_ramping_max_mw": {"2030": 4933, "2035": 6597},  # largest hourly value
    "uncovered_downward_ramping_gwh": {"2030": 0, "2035": 0},
    "res_curtailment_twh": {"2030": 0.81, "2035": 3.28},
    "storage_for_res_target_2035_gwh": 16,                           # ~8 GW (2 h), 4 GW (4 h) or 2 GW (8 h)
}
