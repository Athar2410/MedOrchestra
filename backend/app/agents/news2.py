"""NEWS2 (Royal College of Physicians, 2017) early-warning score, SpO2 scale 1.

Missing vitals score 0 and are reported back so the clinician sees what the
score could not account for.
"""

from app.schemas import Urgency, Vitals


def _respiratory_rate(rr: int) -> int:
    if rr <= 8:
        return 3
    if rr <= 11:
        return 1
    if rr <= 20:
        return 0
    if rr <= 24:
        return 2
    return 3


def _spo2(spo2: int) -> int:
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def _systolic_bp(sbp: int) -> int:
    if sbp <= 90:
        return 3
    if sbp <= 100:
        return 2
    if sbp <= 110:
        return 1
    if sbp <= 219:
        return 0
    return 3


def _heart_rate(hr: int) -> int:
    if hr <= 40:
        return 3
    if hr <= 50:
        return 1
    if hr <= 90:
        return 0
    if hr <= 110:
        return 1
    if hr <= 130:
        return 2
    return 3


def _temperature(temp: float) -> int:
    if temp <= 35.0:
        return 3
    if temp <= 36.0:
        return 1
    if temp <= 38.0:
        return 0
    if temp <= 39.0:
        return 1
    return 2


_SCORERS = {
    "respiratory_rate": _respiratory_rate,
    "spo2": _spo2,
    "systolic_bp": _systolic_bp,
    "heart_rate": _heart_rate,
    "temperature_c": _temperature,
}


def score_news2(vitals: Vitals) -> tuple[int, dict[str, int], list[str]]:
    """Return (total, per-parameter breakdown, missing parameters)."""
    breakdown: dict[str, int] = {}
    missing: list[str] = []
    for field, scorer in _SCORERS.items():
        value = getattr(vitals, field)
        if value is None:
            missing.append(field)
        else:
            breakdown[field] = scorer(value)
    breakdown["supplemental_o2"] = 2 if vitals.on_supplemental_o2 else 0
    breakdown["consciousness"] = 0 if vitals.consciousness == "A" else 3
    return sum(breakdown.values()), breakdown, missing


def urgency_from_news2(total: int, breakdown: dict[str, int]) -> Urgency:
    """NEWS2 thresholds: >=7 high; 5-6 or any single red (3) parameter medium."""
    if total >= 7:
        return "HIGH"
    if total >= 5 or any(points == 3 for points in breakdown.values()):
        return "MEDIUM"
    return "LOW"
