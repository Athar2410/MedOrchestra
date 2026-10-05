"""Structured-output LLM client for Groq's OpenAI-compatible API.

Agents call `get_llm()`; it returns None when no API key is configured, and every
agent must then fall back to rule-based behaviour. Requests use Groq strict
`json_schema` mode (constrained decoding), so the model's reply always parses
into the requested Pydantic model; Pydantic-only constraints (e.g. 0 <= x <= 1)
get one repair round-trip.

Tests swap the client with `use_llm(fake)`.
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    pass


class _ModelUnavailable(LLMError):
    """Transient failure (rate limit, overload, timeout): try the fallback model."""


class LLMClient(Protocol):
    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        model: str | None = None,
        budget_seconds: float | None = None,
    ) -> T: ...


# JSON Schema keywords Groq strict mode does not accept; Pydantic still enforces them.
_UNSUPPORTED_KEYWORDS = {
    "title", "default", "description", "format", "pattern",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "minItems", "maxItems",
}  # fmt: skip


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema -> Groq strict-mode schema: $refs inlined, every property
    required, no additional properties, unsupported keywords removed."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def fix(node: Any) -> Any:
        if isinstance(node, list):
            return [fix(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return fix(defs[node["$ref"].rsplit("/", 1)[-1]])
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "properties":
                out[key] = {name: fix(sub) for name, sub in value.items()}
            elif key not in _UNSUPPORTED_KEYWORDS:
                out[key] = fix(value)
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return fix(schema)


class ResponseCache:
    """Maps a hash of the full request body to the raw model reply."""

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, body TEXT)")

    @staticmethod
    def key(body: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def get(self, key: str) -> str | None:
        row = self._db.execute("SELECT body FROM responses WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, content: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?)", (key, content))
        self._db.commit()


_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


class GroqClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.groq_base_url,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=settings.llm_call_budget_seconds,
            transport=transport,
        )
        self._cache = ResponseCache(settings.llm_cache_path) if settings.llm_cache_path else None

    def _reasoning_params(self, model: str) -> dict[str, Any]:
        # Keep chain-of-thought out of `content` so it parses as the schema.
        if model.startswith("openai/gpt-oss"):
            return {
                "reasoning_effort": self._settings.llm_reasoning_effort,
                "include_reasoning": False,
            }
        if model.startswith("qwen/"):
            # "parsed" moves reasoning to a separate field; "hidden" + strict json_schema
            # makes Qwen emit degenerate text and Groq returns 400 json_validate_failed.
            return {"reasoning_format": "parsed"}
        return {}

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        model: str | None = None,
        budget_seconds: float | None = None,
    ) -> T:
        budget = budget_seconds or self._settings.llm_call_budget_seconds
        start = asyncio.get_running_loop().time()
        deadline = start + budget
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        requested = model or self._settings.llm_model
        fallback = self._settings.llm_fallback_model
        models = [requested] + ([fallback] if fallback and fallback != requested else [])

        for i, candidate in enumerate(models):
            body = self._body(candidate, messages, schema)
            # The primary model may use only part of the budget so the fallback always
            # gets a real chance (a single slow request would otherwise consume it all).
            model_deadline = start + 0.6 * budget if i < len(models) - 1 else deadline
            try:
                content = await self._complete(body, model_deadline)
                break
            except _ModelUnavailable as exc:
                logger.warning("LLM %s unavailable: %s", candidate, exc)
                last_error = exc
        else:
            raise LLMError(f"No LLM available within budget: {last_error}")

        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            logger.warning("LLM output failed validation, repairing: %s", exc)
            repair = {
                **body,
                "messages": [
                    *messages,
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Fix these validation errors:\n{exc}"},
                ],
            }
            try:
                return schema.model_validate_json(await self._complete(repair, deadline))
            except ValidationError as exc2:
                raise LLMError(f"LLM output invalid after repair: {exc2}") from exc2

    def _body(self, model: str, messages: list[dict], schema: type[BaseModel]) -> dict[str, Any]:
        return {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": strict_schema(schema),
                },
            },
            **self._reasoning_params(model),
        }

    async def _complete(self, body: dict[str, Any], deadline: float) -> str:
        key = ResponseCache.key(body)
        if self._cache and (cached := self._cache.get(key)) is not None:
            return cached
        content = await self._post(body, deadline)
        if self._cache:
            self._cache.set(key, content)
        return content

    async def _post(self, body: dict[str, Any], deadline: float) -> str:
        """POST with retries; raises _ModelUnavailable when retries or the budget run out."""
        loop = asyncio.get_running_loop()
        attempts = self._settings.llm_max_retries + 1
        for attempt in range(1, attempts + 1):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise _ModelUnavailable("call budget exhausted")
            try:
                # httpx timeouts apply per read, not to the whole request; enforce the
                # deadline on the total.
                async with asyncio.timeout(remaining):
                    resp = await self._http.post("/chat/completions", json=body)
            except (httpx.TransportError, TimeoutError) as exc:
                if attempt == attempts or deadline - loop.time() <= 1:
                    raise _ModelUnavailable(f"request failed: {exc!r}") from exc
                await asyncio.sleep(min(2 ** (attempt - 1), deadline - loop.time()))
                continue
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            # Qwen strict mode occasionally fails constrained decoding at random; a retry
            # usually succeeds.
            json_failed = resp.status_code == 400 and "json_validate_failed" in resp.text
            if resp.status_code in _RETRYABLE_STATUS or json_failed:
                delay = min(
                    float(resp.headers.get("retry-after") or 2 ** (attempt - 1)),
                    self._settings.llm_max_retry_wait_seconds,
                )
                if attempt == attempts or loop.time() + delay >= deadline:
                    raise _ModelUnavailable(f"HTTP {resp.status_code}")
                logger.warning("LLM HTTP %s, retrying in %.1fs", resp.status_code, delay)
                await asyncio.sleep(delay)
                continue
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:500]}")
        raise AssertionError("unreachable")


_UNSET = object()
_override: Any = _UNSET


@lru_cache
def _default_client() -> GroqClient | None:
    settings = get_settings()
    return GroqClient(settings) if settings.groq_api_key else None


def get_llm() -> LLMClient | None:
    return _default_client() if _override is _UNSET else _override


@contextmanager
def use_llm(client: LLMClient | None) -> Iterator[None]:
    """Temporarily replace the LLM client (tests; `None` simulates no API key)."""
    global _override
    previous, _override = _override, client
    try:
        yield
    finally:
        _override = previous
