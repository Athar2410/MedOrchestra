import pytest

from app.agents.news2 import score_news2, urgency_from_news2
from app.schemas import Vitals


def test_normal_vitals_score_zero():
    total, breakdown, missing = score_news2(
        Vitals(heart_rate=72, systolic_bp=120, respiratory_rate=14, spo2=98, temperature_c=36.8)
    )
    assert total == 0
    assert missing == []
    assert urgency_from_news2(total, breakdown) == "LOW"


def test_missing_vitals_reported_not_scored():
    total, _, missing = score_news2(Vitals(heart_rate=72))
    assert total == 0
    assert set(missing) == {"respiratory_rate", "spo2", "systolic_bp", "temperature_c"}


def test_single_red_parameter_is_medium():
    total, breakdown, _ = score_news2(Vitals(respiratory_rate=26))
    assert total == 3
    assert urgency_from_news2(total, breakdown) == "MEDIUM"


def test_septic_picture_is_high():
    vitals = Vitals(
        heart_rate=125,
        systolic_bp=88,
        respiratory_rate=26,
        spo2=91,
        temperature_c=39.4,
        consciousness="C",
    )
    total, breakdown, _ = score_news2(vitals)
    # HR 2 + SBP 3 + RR 3 + SpO2 3 + temp 2 + new confusion 3
    assert total == 16
    assert urgency_from_news2(total, breakdown) == "HIGH"


@pytest.mark.parametrize(
    ("temp", "points"),
    [(35.0, 3), (35.1, 1), (36.0, 1), (36.1, 0), (38.0, 0), (38.1, 1), (39.0, 1), (39.1, 2)],
)
def test_temperature_band_edges(temp, points):
    _, breakdown, _ = score_news2(Vitals(temperature_c=temp))
    assert breakdown["temperature_c"] == points


def test_supplemental_oxygen_adds_two():
    total, _, _ = score_news2(Vitals(on_supplemental_o2=True))
    assert total == 2
