import asyncio
import json

import httpx
import pytest

from app.main import create_app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def parse_sse(body: str) -> list[tuple[int, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if "data" in fields:
            events.append((int(fields["id"]), json.loads(fields["data"])))
    return events


async def create_run(client, case) -> str:
    resp = await client.post("/api/cases", json=case.model_dump(mode="json"))
    assert resp.status_code == 202
    return resp.json()["run_id"]


async def test_stream_emits_full_agent_timeline(client, vague_case):
    run_id = await create_run(client, vague_case)
    resp = await client.get(f"/api/runs/{run_id}/stream")
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    types = [e["type"] for _, e in events]
    assert [i for i, _ in events] == list(range(len(events)))
    assert types[0] == "run_started"
    assert types[-1] == "done" and events[-1][1]["status"] == "completed"
    assert "reroute" in types
    assert types.index("report") < types.index("done")

    completed = [(e["agent"], e["attempt"]) for _, e in events if e["type"] == "agent_completed"]
    assert ("diagnostician", 2) in completed and ("critique", 2) in completed


async def test_reconnect_resumes_from_last_event_id(client, vague_case):
    run_id = await create_run(client, vague_case)
    full = parse_sse((await client.get(f"/api/runs/{run_id}/stream")).text)

    resumed = parse_sse(
        (await client.get(f"/api/runs/{run_id}/stream", headers={"Last-Event-ID": "3"})).text
    )
    assert resumed == full[4:]


async def test_get_run_returns_report(client, chest_pain_case):
    run_id = await create_run(client, chest_pain_case)
    for _ in range(100):
        body = (await client.get(f"/api/runs/{run_id}")).json()
        if body["status"] != "running":
            break
        await asyncio.sleep(0.01)
    assert body["status"] == "completed"
    assert body["report"]["urgency"] == "HIGH"


async def test_validation_and_unknown_run(client):
    assert (await client.post("/api/cases", json={"chief_complaint": ""})).status_code == 422
    assert (await client.get("/api/runs/nope/stream")).status_code == 404


@pytest.fixture
def fake_store(monkeypatch):
    """In-memory stand-in for the Supabase `runs` table."""
    from app.services import run_store

    rows: dict[str, dict] = {}
    monkeypatch.setattr(run_store, "save_run", lambda row: rows.setdefault(row["id"], row))
    monkeypatch.setattr(run_store, "load_run", lambda run_id: rows.get(run_id))
    monkeypatch.setattr(
        run_store,
        "list_runs",
        lambda limit=20: [
            {
                "run_id": r["id"],
                "created_at": "2026-10-06T10:00:00Z",
                "status": r["status"],
                "duration_ms": r["duration_ms"],
                "chief_complaint": r["case_input"]["chief_complaint"],
                "urgency": r["report"]["urgency"],
                "top_diagnosis": None,
            }  # fmt: skip
            for r in rows.values()
        ][:limit],
    )
    return rows


async def test_finished_run_is_saved_listed_and_replayable_after_eviction(
    client, vague_case, fake_store
):
    run_id = await create_run(client, vague_case)
    live = parse_sse((await client.get(f"/api/runs/{run_id}/stream")).text)

    saved = fake_store[run_id]
    assert saved["status"] == "completed" and saved["report"]["case"]["chief_complaint"]
    listed = (await client.get("/api/runs")).json()
    assert [r["run_id"] for r in listed] == [run_id]

    # Simulate a server restart: the run is gone from memory, only the database has it.
    client._transport.app.state.runs._runs.clear()
    replayed = parse_sse((await client.get(f"/api/runs/{run_id}/stream")).text)
    assert replayed == live
    assert (await client.get(f"/api/runs/{run_id}")).json()["status"] == "completed"
