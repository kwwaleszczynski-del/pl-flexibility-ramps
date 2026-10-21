"""IEEE RTS-79 chronological load model (Tables 1-3 of the RTS-79 paper), used only to validate the adequacy code."""
import numpy as np

WEEKLY = np.array([
    86.2, 90.0, 87.8, 83.4, 88.0, 84.1, 83.2, 80.6, 74.0, 73.7, 71.5, 72.7, 70.4,
    75.0, 72.1, 80.0, 75.4, 83.7, 87.0, 88.0, 85.6, 81.1, 90.0, 88.7, 89.6, 86.1,
    75.5, 81.6, 80.1, 88.0, 72.2, 77.6, 80.0, 72.9, 72.6, 70.5, 78.0, 69.5, 72.4,
    72.4, 74.3, 74.4, 80.0, 88.1, 88.5, 90.9, 94.0, 89.0, 94.2, 97.0, 100.0, 95.2,
]) / 100.0
DAILY = np.array([93, 100, 98, 96, 94, 77, 75]) / 100.0      # Monday..Sunday, % of the weekly peak
HOURLY = {                                                     # % of the daily peak, hour 0 = 00:00-01:00
    ("winter", "weekday"): [67, 63, 60, 59, 59, 60, 74, 86, 95, 96, 96, 95,
                            95, 95, 93, 94, 99, 100, 100, 96, 91, 83, 73, 63],
    ("winter", "weekend"): [78, 72, 68, 66, 64, 65, 66, 70, 80, 88, 90, 91,
                            90, 88, 87, 87, 91, 100, 99, 97, 94, 92, 87, 81],
    ("summer", "weekday"): [64, 60, 58, 56, 56, 58, 64, 76, 87, 95, 99, 100,
                            99, 100, 100, 97, 96, 96, 93, 92, 92, 93, 87, 72],
    ("summer", "weekend"): [74, 70, 66, 65, 64, 62, 62, 66, 81, 86, 91, 93,
                            93, 92, 91, 91, 92, 94, 95, 95, 100, 93, 88, 80],
    ("spring_fall", "weekday"): [63, 62, 60, 58, 59, 65, 72, 85, 95, 99, 100, 99,
                                 93, 92, 90, 88, 90, 92, 96, 98, 96, 90, 80, 70],
    ("spring_fall", "weekend"): [75, 73, 69, 66, 65, 65, 68, 74, 83, 89, 92, 94,
                                 91, 90, 90, 86, 85, 88, 92, 100, 97, 95, 90, 85],
}


def season(week: int) -> str:
    if week <= 8 or week >= 44:
        return "winter"
    if 18 <= week <= 30:
        return "summer"
    return "spring_fall"


def hourly_load(peak: float = 2850.0) -> np.ndarray:
    """8736 hourly loads (52 weeks of 7 days, starting on a Monday)."""
    out = []
    for w in range(52):
        for d in range(7):
            kind = "weekend" if d >= 5 else "weekday"
            out.extend(peak * WEEKLY[w] * DAILY[d] * np.asarray(HOURLY[(season(w + 1), kind)]) / 100.0)
    return np.asarray(out)
