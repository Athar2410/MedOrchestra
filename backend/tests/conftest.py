import os

# Must run before app modules read settings.
os.environ["STUB_DELAY_SECONDS"] = "0"

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.schemas import CaseInput, Vitals  # noqa: E402

get_settings.cache_clear()


@pytest.fixture
def chest_pain_case() -> CaseInput:
    return CaseInput(
        chief_complaint="Crushing chest pain radiating to left arm for 1 hour",
        vitals=Vitals(
            heart_rate=118, systolic_bp=95, respiratory_rate=24, spo2=93, temperature_c=37.1
        ),
        medications=["Warfarin", "aspirin", "metoprolol"],
    )


@pytest.fixture
def vague_case() -> CaseInput:
    return CaseInput(chief_complaint="Feeling generally unwell and tired")
