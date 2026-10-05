from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    cors_origins: list[str] = ["http://localhost:3000"]

    # Critique agent: below this confidence it asks the Diagnostician to retry.
    critique_threshold: float = 0.60
    # Hard cap on Critique -> Diagnostician re-routes (PRD: max 1 per case).
    max_reroutes: int = 1

    # Artificial per-agent delay so the streaming UI is visible while agents are stubs.
    stub_delay_seconds: float = 0.6

    run_timeout_seconds: float = 60.0
    max_runs_in_memory: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()
