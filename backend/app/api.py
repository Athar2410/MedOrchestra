import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.runs import Run, RunManager
from app.schemas import CaseInput, ClinicalReport

router = APIRouter(prefix="/api")


class RunCreated(BaseModel):
    run_id: str
    stream_url: str


class RunStatus(BaseModel):
    run_id: str
    status: str
    report: ClinicalReport | None
    error: str | None


def _runs(request: Request) -> RunManager:
    return request.app.state.runs


def _get_run(request: Request, run_id: str) -> Run:
    run = _runs(request).get(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found")
    return run


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.post("/cases", status_code=status.HTTP_202_ACCEPTED)
async def create_case(case: CaseInput, request: Request) -> RunCreated:
    run = _runs(request).start(case)
    return RunCreated(run_id=run.id, stream_url=f"/api/runs/{run.id}/stream")


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> RunStatus:
    run = _get_run(request, run_id)
    return RunStatus(run_id=run.id, status=run.status, report=run.report, error=run.error)


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    request: Request,
    last_event_id: int | None = Header(None),
) -> StreamingResponse:
    """SSE stream. Each message's `data` is a JSON event with a `type` field;
    the SSE `id` is the event index, so EventSource reconnects resume via Last-Event-ID."""
    run = _get_run(request, run_id)
    start = last_event_id + 1 if last_event_id is not None else 0

    async def body() -> AsyncIterator[str]:
        async for item in _runs(request).subscribe(run, start):
            if item is None:
                yield ": keepalive\n\n"
                continue
            index, event = item
            yield f"id: {index}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
