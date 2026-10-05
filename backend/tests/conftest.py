import os
from pathlib import Path

# Must run before app modules read settings. Env vars override backend/.env, so tests
# never use the real Groq key, RxNorm, Supabase, WHO ICD, the LLM cache, or the full
# DDInter download.
FIXTURES = Path(__file__).parent / "fixtures"
os.environ.update(
    STUB_DELAY_SECONDS="0",
    GROQ_API_KEY="",
    LLM_CACHE_PATH="",
    RXNORM_ENABLED="false",
    DDINTER_DIR=str(FIXTURES / "ddinter"),
    DATABASE_URL="",
    ICD_CLIENT_ID="",
    ICD_CLIENT_SECRET="",
    NCBI_API_KEY="",
)

from collections.abc import Callable  # noqa: E402
from contextlib import ExitStack  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.schemas import CaseInput, Evidence, Vitals  # noqa: E402
from app.services.llm import LLMError, use_llm  # noqa: E402
from app.services.retrieval import use_retriever  # noqa: E402

get_settings.cache_clear()


class FakeLLM:
    """Returns canned responses keyed by output schema class name.

    A response may be a model instance, a callable (system, user) -> instance, or an
    exception instance to raise.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    async def structured(
        self, *, system: str, user: str, schema: type[BaseModel], model=None, budget_seconds=None
    ):
        self.calls.append(
            {
                "schema": schema.__name__,
                "system": system,
                "user": user,
                "model": model,
                "budget_seconds": budget_seconds,
            }
        )
        response = self.responses.get(schema.__name__)
        if response is None:
            raise LLMError(f"FakeLLM has no response for {schema.__name__}")
        if isinstance(response, Exception):
            raise response
        if isinstance(response, Callable):
            response = response(system, user)
        return schema.model_validate(
            response.model_dump() if isinstance(response, BaseModel) else response
        )


@pytest.fixture(autouse=True)
def no_llm():
    """Default for every test: behave as if no API key is configured."""
    with use_llm(None):
        yield


@pytest.fixture
def fake_llm(no_llm):
    """`fake_llm({...})` installs a FakeLLM for the rest of the test."""
    with ExitStack() as stack:

        def install(responses: dict[str, Any]) -> FakeLLM:
            fake = FakeLLM(responses)
            stack.enter_context(use_llm(fake))
            return fake

        yield install


class FakeRetriever:
    """Returns the given evidence for any queries and records them."""

    def __init__(self, evidence: list[Evidence] | Exception) -> None:
        self.evidence = evidence
        self.calls: list[list[str]] = []

    async def search(self, queries: list[str]) -> list[Evidence]:
        self.calls.append(queries)
        if isinstance(self.evidence, Exception):
            raise self.evidence
        return self.evidence


def make_evidence(pmid: int, title: str, query: str = "q", score: float = 1.0) -> Evidence:
    return Evidence(
        pmid=pmid, title=title, abstract=f"Abstract about {title}.", journal="J Test",
        pub_year=2024, query=query, score=score,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def no_retriever():
    """Default for every test: no Supabase / MedCPT."""
    with use_retriever(None):
        yield


@pytest.fixture
def fake_retriever(no_retriever):
    with ExitStack() as stack:

        def install(evidence: list[Evidence] | Exception) -> FakeRetriever:
            fake = FakeRetriever(evidence)
            stack.enter_context(use_retriever(fake))
            return fake

        yield install


@pytest.fixture
def chest_pain_case() -> CaseInput:
    return CaseInput(
        chief_complaint="Crushing chest pain radiating to left arm for 1 hour",
        vitals=Vitals(
            heart_rate=118, systolic_bp=95, respiratory_rate=24, spo2=93, temperature_c=37.1
        ),
        medications=["Warfarin", "aspirin 75mg od", "metoprolol"],
    )


@pytest.fixture
def vague_case() -> CaseInput:
    return CaseInput(chief_complaint="Feeling generally unwell and tired")
