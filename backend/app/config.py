from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    cors_origins: list[str] = ["http://localhost:3000"]

    # Critique agent: below this confidence it asks the Diagnostician to retry.
    critique_threshold: float = 0.60
    # Hard cap on Critique -> Diagnostician re-routes (PRD: max 1 per case).
    max_reroutes: int = 1

    # Artificial delay for agents that are still placeholders, so the streaming UI is visible.
    stub_delay_seconds: float = 0.6

    run_timeout_seconds: float = 60.0
    max_runs_in_memory: int = 200

    # LLM (Groq, OpenAI-compatible API). Without a key, agents fall back to rule-based output.
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-120b"
    # Deliberately a different model family from llm_model (see Critique agent).
    critique_model: str = "qwen/qwen3.8-27b"
    # Used when the requested model stays unavailable (503/429) within the call budget.
    llm_fallback_model: str = "openai/gpt-oss-20b"
    llm_reasoning_effort: str = "low"
    # Wall-clock budget per structured() call across retries and the fallback model, so a
    # degraded provider costs at most this much before agents fall back to rules (PRD <=15s).
    llm_call_budget_seconds: float = 12.0
    llm_max_retries: int = 3
    llm_max_retry_wait_seconds: float = 5.0
    # SQLite response cache (makes evaluation runs reproducible). Empty string disables it.
    llm_cache_path: str = str(BACKEND_DIR / ".cache" / "llm.sqlite")

    # DDInter 2.0 CSVs, fetched by `python -m pipelines.download_ddinter`.
    ddinter_dir: Path = BACKEND_DIR / "data" / "ddinter"

    # RxNorm (NLM RxNav) for brand/misspelled drug names -> ingredients.
    rxnorm_enabled: bool = True
    rxnorm_base_url: str = "https://rxnav.nlm.nih.gov/REST"
    rxnorm_timeout_seconds: float = 4.0
    # approximateTerm scores: brands ~13, typos ~8, unrelated text ~4.5.
    rxnorm_min_score: float = 7.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
