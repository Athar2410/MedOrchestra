import json
from typing import Literal

import httpx
import pytest
from pydantic import BaseModel, Field

from app.config import get_settings
from app.services.llm import GroqClient, LLMError, strict_schema


class Item(BaseModel):
    title: str  # a field literally named "title" must survive keyword stripping
    score: float = Field(ge=0, le=1)
    code: str | None = None


class Out(BaseModel):
    urgency: Literal["LOW", "HIGH"]
    items: list[Item]


def test_strict_schema_is_groq_compatible():
    schema = strict_schema(Out)
    assert schema["required"] == ["urgency", "items"]
    assert schema["additionalProperties"] is False
    item = schema["properties"]["items"]["items"]
    assert "$ref" not in json.dumps(schema)
    assert item["required"] == ["title", "score", "code"]
    assert item["additionalProperties"] is False
    assert "title" in item["properties"]
    assert "minimum" not in item["properties"]["score"]


def chat_response(content: dict | str) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


GOOD = {"urgency": "HIGH", "items": [{"title": "ACS", "score": 0.9, "code": None}]}


def client(handler, tmp_path=None, **overrides) -> GroqClient:
    settings = get_settings().model_copy(
        update={
            "groq_api_key": "test",
            "llm_cache_path": str(tmp_path / "cache.sqlite") if tmp_path else "",
            **overrides,
        }
    )
    return GroqClient(settings, transport=httpx.MockTransport(handler))


async def test_request_shape_and_parsing():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return chat_response(GOOD)

    out = await client(handler).structured(system="s", user="u", schema=Out)
    assert out.items[0].title == "ACS"
    body = seen[0]
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["include_reasoning"] is False


async def test_qwen_uses_parsed_reasoning():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return chat_response(GOOD)

    await client(handler).structured(system="s", user="u", schema=Out, model="qwen/qwen3.8-27b")
    assert seen[0]["reasoning_format"] == "parsed"


async def test_retries_rate_limit_and_json_validate_failed():
    responses = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(
            400, headers={"retry-after": "0"}, text='{"error":{"code":"json_validate_failed"}}'
        ),
        chat_response(GOOD),
    ]
    out = await client(lambda r: responses.pop(0)).structured(system="s", user="u", schema=Out)
    assert out.urgency == "HIGH" and responses == []


async def test_non_retryable_error_raises():
    with pytest.raises(LLMError, match="401"):
        await client(lambda r: httpx.Response(401, text="bad key")).structured(
            system="s", user="u", schema=Out
        )


async def test_repairs_pydantic_constraint_violation():
    bad = {"urgency": "HIGH", "items": [{"title": "ACS", "score": 1.7, "code": None}]}
    responses = [chat_response(bad), chat_response(GOOD)]
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return responses.pop(0)

    out = await client(handler).structured(system="s", user="u", schema=Out)
    assert out.items[0].score == 0.9
    assert "validation errors" in requests[1]["messages"][-1]["content"]


async def test_cache_avoids_second_request(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return chat_response(GOOD)

    c = client(handler, tmp_path)
    await c.structured(system="s", user="u", schema=Out)
    await c.structured(system="s", user="u", schema=Out)
    await c.structured(system="s", user="different", schema=Out)
    assert len(calls) == 2


async def test_falls_back_to_secondary_model_when_primary_overloaded():
    models = []

    def handler(request):
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "openai/gpt-oss-120b":
            return httpx.Response(503, headers={"retry-after": "0"})
        return chat_response(GOOD)

    out = await client(handler, llm_max_retries=1).structured(system="s", user="u", schema=Out)
    assert out.urgency == "HIGH"
    assert models == ["openai/gpt-oss-120b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]


async def test_budget_caps_total_wait():
    import time

    def handler(request):
        return httpx.Response(503, headers={"retry-after": "30"})

    c = client(handler, llm_call_budget_seconds=0.5, llm_max_retry_wait_seconds=5.0)
    started = time.perf_counter()
    with pytest.raises(LLMError, match="within budget"):
        await c.structured(system="s", user="u", schema=Out)
    assert time.perf_counter() - started < 0.5


async def test_primary_slowness_leaves_budget_for_fallback():
    import asyncio

    async def handler(request):
        if json.loads(request.content)["model"] == "openai/gpt-oss-120b":
            await asyncio.sleep(5)  # longer than the whole budget
        return chat_response(GOOD)

    c = client(handler, llm_call_budget_seconds=1.0)
    out = await c.structured(system="s", user="u", schema=Out)
    assert out.urgency == "HIGH"
